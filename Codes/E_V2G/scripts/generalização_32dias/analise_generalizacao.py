#!/usr/bin/env python3
"""
analise_generalizacao.py — consolida e analisa a grade de generalização (docs/13).

Lê só o que `run_generalizacao.py` gravou (não roda nenhum algoritmo) e gera, em
<entrada>/analise/:

  consolidado.csv           uma linha por instância × técnica (V2G nivelado, LCVF, Gurobi)
  pareado.csv               uma linha por instância (dia × frota × N), técnicas lado a lado e gaps
  resumo_frota_tamanho.csv  mediana, IQR e mín–máx sobre os dias, por frota e N
  resumo_estacao.csv        idem, por estação e frota (todos os N)
  testes_pareados.csv       Wilcoxon pareado (V2G × LCVF; V2G e LCVF × Exato), com correção de Holm
  correlacoes.csv           Spearman da redução do S1 contra a carga do dia e a fração de EVs no pico
  faltantes.csv             instâncias esperadas sem concluido.json (grade incompleta)
  resumo.md                 números-chave para o manuscrito
  comparacao/               figuras que comparam as técnicas (de --figuras):
    comparacao.csv                    picos e gaps por instância
    Fig_01_gap_pico_v2g_vs_lcvf.webp  gap V2G × LCVF por frota e N (distribuição sobre os dias)
    Fig_02_gap_pico_estacoes.webp     idem, por estação
    Fig_03_pico_vs_exato.webp         LCVF e V2G relativos ao Exato (todos os N com Gurobi)
    perfis/<inst>.webp                inicial, LCVF e V2G sobrepostos (plot_load_profiles). Por
                                      padrão só onde não há Exato (N ≥ 18.000): onde há, o
                                      Fig_compare.webp do Gurobi já mostra as mesmas curvas + Exato

Definições (as mesmas do manuscrito):
  gap de pico V2G × LCVF   100 · (pico final V2G / pico final LCVF − 1); negativo = V2G menor
  energia líquida V2G      recarga S2+S3 − descarga S1 (kWh); LCVF: energia alocada
  EVs abaixo de 90%        V2G: N − EVs_carga_completa (summary_3stage.csv, após S3);
                           LCVF: última linha de EVs_abaixo_90pct_estado na tabela de iterações
                           (estado FINAL). A coluna EVs_abaixo_90pct é a da TENTATIVA, desfeita
                           quando Flag = 1, e pode subcontar.
  razão energia/carga      energia para levar a frota a 90% de SoC / energia convencional do dia
  EVs conectados no pico   fração de EVs com start_ts ≤ slot de pico convencional ≤ end_ts
                           (ou ≤ wrap_end_ts, continuação na madrugada de sessões após a meia-noite)

Uso: python3 analise_generalizacao.py [--entrada <dir>] [--sem-figuras]
                                      [--perfis sem_exato|todas|referencia|nenhuma] [--jobs 4]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import selecao_dias  # noqa: E402
from figuras_exato import plot_load_profiles  # noqa: E402

REPO_ROOT   = SCRIPTS_DIR.parents[3]
DEFAULT_IN  = REPO_ROOT / "Results/V2G/CampoGrande/generalization"
ALL_DAYS    = REPO_ROOT / "Datasets/ProcessedData/Cities/CampoGrande/all_days"
EV_DIR      = REPO_ROOT / "Datasets/ProcessedData/Vehicles/V2G"
TS_HOURS    = round(24 / 144, 5)
SOC_FULL_TARGET = 0.90
INST_RE     = re.compile(r"^inst_(\d+)_(.+?)_cgr_(\d{8})_([a-z]+)_([a-z]{3})$")


# ─── Leitura ─────────────────────────────────────────────────────────────────

def _meta(inst_dir: Path) -> dict:
    n, frota, dia, estacao, dow = INST_RE.match(inst_dir.name).groups()
    return dict(instancia=inst_dir.name, dia=dia, estacao=estacao, dow=dow, frota=frota, N=int(n))


def _execucao(inst_dir: Path) -> dict:
    f = inst_dir / "concluido.json"
    if not f.exists():
        return {}
    j = json.loads(f.read_text())
    return dict(wall_s=j.get("wall_s"), maxrss_mb=j.get("maxrss_mb"), commit=j.get("commit"))


class Cache:
    """pp_*.csv e perfis diários lidos uma vez só."""

    def __init__(self):
        self.pp, self.dia = {}, {}

    def frota(self, frota, n):
        k = (frota, n)
        if k not in self.pp:
            self.pp[k] = pd.read_csv(EV_DIR / frota / f"pp_{n}_{frota}.csv")
        return self.pp[k]

    def carga(self, dia_estacao_dow):
        if dia_estacao_dow not in self.dia:
            self.dia[dia_estacao_dow] = pd.read_csv(ALL_DAYS / f"pc_cgr_{dia_estacao_dow}.csv")["Pconv"].to_numpy()
        return self.dia[dia_estacao_dow]


def _contexto(m: dict, cache: Cache) -> dict:
    """Indicadores da instância que independem da técnica."""
    pconv = cache.carga(f"{m['dia']}_{m['estacao']}_{m['dow']}")
    pp = cache.frota(m["frota"], m["N"])
    h = int(np.argmax(pconv)) + 1                                  # slot de pico (1-based, como start_ts)
    alvo = np.maximum(0.0, pp.battery_capacity_kWh * SOC_FULL_TARGET - pp.energy_adjusted_kWh)
    # dia periódico: sessão que passa da meia-noite também está conectada em [1, wrap_end_ts]
    wrap = pp.wrap_end_ts.fillna(0).values if "wrap_end_ts" in pp else 0
    e_conv = float(pconv.sum() * TS_HOURS)
    return dict(pico_conv_kW=float(pconv.max()), slot_pico_conv=h, energia_conv_kWh=e_conv,
                energia_frota_90_kWh=float(alvo.sum()),
                razao_energia_carga=float(alvo.sum()) / e_conv,
                frac_conectados_pico=float(((pp.start_ts <= h) & (h <= pp.end_ts) | (h <= wrap)).mean()))


def _v2g(d: Path, cache: Cache) -> dict:
    m = _meta(d)
    s = pd.read_csv(d / "summary_3stage.csv").set_index("Estágio")
    a = pd.read_csv(d / "en_profile_all_stages.csv")
    r1 = pd.read_csv(d / "results_s1_discharge.csv").iloc[-1]
    ini, s1, s3 = s.loc["Inicial"], s.loc["Após S1 (V2G)"], s.loc["Após S3 (máx)"]
    desc, rec = a.Energia_S1_discharge_kWh.sum(), a.Energia_S3_fullcharge_kWh.sum()
    return dict(
        m, tecnica="v2g",
        pico_s1_kW=s1.Pico_Pconv_kW,
        reducao_s1_pct=100 * (1 - s1.Pico_Pconv_kW / ini.Pico_Pconv_kW),
        reducao_var_s1_pct=100 * (1 - s1.Var_Carga_kW2 / ini.Var_Carga_kW2),
        pico_final_kW=s3.Pico_Pconv_kW, var_final_kW2=s3.Var_Carga_kW2,
        energia_descarga_kWh=desc, energia_recarga_kWh=rec, energia_liquida_kWh=rec - desc,
        evs_descarregaram=int(r1.EVs_descarregaram),
        evs_abaixo_90=int(m["N"] - s3.EVs_carga_completa),
        evs_abaixo_min=int(m["N"] - s3.EVs_acima_min),
        soc_medio_inicial=ini.SoC_medio_pct, soc_medio_s1=s1.SoC_medio_pct, soc_medio_final=s3.SoC_medio_pct,
        **_execucao(d))


def _lcvf(d: Path, cache: Cache) -> dict:
    m = _meta(d)
    f = pd.read_csv(d / "en_profile_final.csv")
    t = pd.read_csv(d.parent / f"{d.name}.csv").iloc[-1]     # tabela de iterações (pasta da frota)
    return dict(
        m, tecnica="lcvf",
        pico_final_kW=float(f.Pconv_final.max()), var_final_kW2=float(f.Pconv_final.var(ddof=0)),
        energia_liquida_kWh=float(f.Energia_alocada_kWh.sum()),
        evs_abaixo_90=int(t.EVs_abaixo_90pct_estado),
        **_execucao(d))


def _gurobi(d: Path, cache: Cache) -> dict:
    m = _meta(d)
    f = pd.read_csv(d / "en_profile_final.csv")
    s = pd.read_csv(d / "summary.csv").iloc[0]
    return dict(
        m, tecnica="gurobi",
        pico_final_kW=float(s.Pico_kW), var_final_kW2=float(f.Pconv_final.var(ddof=0)),
        energia_liquida_kWh=float(s.Energia_entregue_kWh), pct_energia_entregue=float(s.Pct_entregue),
        **_execucao(d))


def consolidar(entrada: Path) -> pd.DataFrame:
    cache, rows = Cache(), []
    for sub, fn in [("v2g_nivelado", _v2g), ("lcvf_v2g", _lcvf), ("exact_gurobi", _gurobi)]:
        for d in sorted((entrada / sub).glob("*/inst_*/concluido.json")):
            rows.append(fn(d.parent, cache))
    df = pd.DataFrame(rows)
    ctx = {i: _contexto(_meta(Path(i)), cache) for i in df.instancia.unique()}
    df = df.join(pd.DataFrame.from_dict(ctx, orient="index"), on="instancia")
    df["frac_abaixo_90_pct"] = 100 * df.evs_abaixo_90 / df.N
    return df


# ─── Pareamento e estatísticas ───────────────────────────────────────────────

CHAVE = ["instancia", "dia", "estacao", "dow", "frota", "N"]
CONTEXTO = ["pico_conv_kW", "energia_conv_kWh", "energia_frota_90_kWh", "razao_energia_carga",
            "frac_conectados_pico"]


def parear(c: pd.DataFrame) -> pd.DataFrame:
    v = c[c.tecnica == "v2g"].set_index(CHAVE)
    l = c[c.tecnica == "lcvf"].set_index(CHAVE)
    g = c[c.tecnica == "gurobi"].set_index(CHAVE)
    p = v[CONTEXTO + ["pico_s1_kW", "reducao_s1_pct", "reducao_var_s1_pct",
                      "evs_descarregaram"]].copy()
    for col in ["pico_final_kW", "energia_liquida_kWh", "frac_abaixo_90_pct"]:
        p[f"{col}_v2g"] = v[col]
        p[f"{col}_lcvf"] = l[col]
        p[f"{col}_gurobi"] = g[col] if col != "frac_abaixo_90_pct" else np.nan
    p["gap_pico_v2g_vs_lcvf_pct"] = 100 * (p.pico_final_kW_v2g / p.pico_final_kW_lcvf - 1)
    p["gap_energia_v2g_vs_lcvf_pct"] = 100 * (p.energia_liquida_kWh_v2g / p.energia_liquida_kWh_lcvf - 1)
    p["dif_abaixo_90_pp"] = p.frac_abaixo_90_pct_v2g - p.frac_abaixo_90_pct_lcvf
    p["gap_pico_v2g_vs_exato_pct"] = 100 * (p.pico_final_kW_v2g / p.pico_final_kW_gurobi - 1)
    p["gap_pico_lcvf_vs_exato_pct"] = 100 * (p.pico_final_kW_lcvf / p.pico_final_kW_gurobi - 1)
    return p.reset_index()


METRICAS = ["reducao_s1_pct", "gap_pico_v2g_vs_lcvf_pct", "gap_energia_v2g_vs_lcvf_pct",
            "frac_abaixo_90_pct_v2g", "frac_abaixo_90_pct_lcvf", "dif_abaixo_90_pp",
            "gap_pico_v2g_vs_exato_pct", "gap_pico_lcvf_vs_exato_pct", "razao_energia_carga",
            "frac_conectados_pico"]


def resumir(p: pd.DataFrame, por: list) -> pd.DataFrame:
    def q(x, k):
        return x.quantile(k)
    agg = {}
    for m in METRICAS:
        agg.update({f"{m}__mediana": (m, "median"), f"{m}__q1": (m, lambda x: q(x, .25)),
                    f"{m}__q3": (m, lambda x: q(x, .75)), f"{m}__min": (m, "min"), f"{m}__max": (m, "max")})
    r = p.groupby(por).agg(n_dias=("dia", "nunique"),
                          v2g_vence_lcvf=("gap_pico_v2g_vs_lcvf_pct", lambda x: int((x < 0).sum())),
                          **agg)
    return r.dropna(axis=1, how="all").reset_index()


def _wilcoxon(x, y):
    """Wilcoxon pareado em x − y, com o efeito rank-biserial pareado (+1: x sempre maior)."""
    d = (np.asarray(x) - np.asarray(y))
    d = d[~np.isnan(d)]
    nz = d[d != 0]
    out = dict(n=len(d), x_menor=int((d < 0).sum()), x_maior=int((d > 0).sum()),
               mediana_dif=float(np.median(d)) if len(d) else np.nan, W=np.nan, p=np.nan, efeito_rb=np.nan)
    if len(nz) >= 5:
        r = stats.rankdata(np.abs(nz))
        wp, wm = r[nz > 0].sum(), r[nz < 0].sum()
        res = stats.wilcoxon(nz)
        out.update(W=float(res.statistic), p=float(res.pvalue), efeito_rb=float((wp - wm) / (wp + wm)))
    return out


def testar(p: pd.DataFrame) -> pd.DataFrame:
    comps = [("pico final: V2G × LCVF", "pico_final_kW_v2g", "pico_final_kW_lcvf"),
             ("energia líquida: V2G × LCVF", "energia_liquida_kWh_v2g", "energia_liquida_kWh_lcvf"),
             ("EVs abaixo de 90%: V2G × LCVF", "frac_abaixo_90_pct_v2g", "frac_abaixo_90_pct_lcvf"),
             ("pico final: V2G × Exato", "pico_final_kW_v2g", "pico_final_kW_gurobi"),
             ("pico final: LCVF × Exato", "pico_final_kW_lcvf", "pico_final_kW_gurobi")]
    rows = []
    grupos = [("todas", "todos", p)] + [(f, n, g) for (f, n), g in p.groupby(["frota", "N"])]
    for frota, n, g in grupos:
        for nome, a, b in comps:
            sub = g.dropna(subset=[a, b])
            if len(sub):
                rows.append(dict(comparacao=nome, frota=frota, N=n, **_wilcoxon(sub[a], sub[b])))
    t = pd.DataFrame(rows)
    ok = t.p.notna()
    t.loc[ok, "p_holm"] = _holm(t.loc[ok, "p"])
    return t


def _holm(p: pd.Series) -> np.ndarray:
    p = p.to_numpy()
    ordem = np.argsort(p)
    aj = np.empty_like(p)
    acc = 0.0
    for k, i in enumerate(ordem):
        acc = max(acc, (len(p) - k) * p[i])
        aj[i] = min(acc, 1.0)
    return aj


def correlacionar(p: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for frota, g in [("todas", p)] + list(p.groupby("frota")):
        for x in ["razao_energia_carga", "frac_conectados_pico", "pico_conv_kW"]:
            if g[x].nunique() > 2:
                r = stats.spearmanr(g[x], g.reducao_s1_pct)
                rows.append(dict(frota=frota, x=x, y="reducao_s1_pct", n=len(g),
                                 rho=float(r.statistic), p=float(r.pvalue)))
    return pd.DataFrame(rows)


def faltantes(entrada: Path, c: pd.DataFrame) -> pd.DataFrame:
    from run_generalizacao import FROTAS, N_GUROBI, SUBDIR, inst_name
    feitas = set(zip(c.tecnica, c.instancia))
    rows = []
    for dia in selecao_dias.select_days():
        for frota, tamanhos in FROTAS.items():
            for n in tamanhos:
                for tec in SUBDIR:
                    if tec == "gurobi" and n not in N_GUROBI:
                        continue
                    i = inst_name(n, frota, dia)
                    if (tec, i) not in feitas:
                        rows.append(dict(tecnica=tec, instancia=i))
    return pd.DataFrame(rows, columns=["tecnica", "instancia"])


# ─── Resumo textual ──────────────────────────────────────────────────────────

def _f(x, d=1):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def escrever_resumo(c, p, t, corr, falt, path):
    n_inst = c.groupby("tecnica").size().to_dict()
    pv = p.dropna(subset=["gap_pico_v2g_vs_lcvf_pct"])
    pe = p.dropna(subset=["gap_pico_v2g_vs_exato_pct"])
    tg = t[(t.frota == "todas")].set_index("comparacao")
    L = [f"# Resumo da generalização\n",
         f"Instâncias concluídas: {n_inst}. Faltantes: {len(falt)}.\n",
         "## Estágio 1 (V2G)\n",
         f"- Redução do pico convencional: mediana {_f(p.reducao_s1_pct.median())}% "
         f"(IQR {_f(p.reducao_s1_pct.quantile(.25))}–{_f(p.reducao_s1_pct.quantile(.75))}%, "
         f"mín–máx {_f(p.reducao_s1_pct.min())}–{_f(p.reducao_s1_pct.max())}%).",
         f"- Redução da variância da carga: até {_f(p.reducao_var_s1_pct.max())}%.\n",
         "## V2G × LCVF (pico final)\n",
         f"- V2G com pico menor em {int((pv.gap_pico_v2g_vs_lcvf_pct < 0).sum())} de {len(pv)} instâncias; "
         f"gap mediano {_f(pv.gap_pico_v2g_vs_lcvf_pct.median(), 2)}% "
         f"(mín–máx {_f(pv.gap_pico_v2g_vs_lcvf_pct.min(), 2)}% a {_f(pv.gap_pico_v2g_vs_lcvf_pct.max(), 2)}%).",
         f"- Energia líquida do V2G relativa ao LCVF: mediana {_f(pv.gap_energia_v2g_vs_lcvf_pct.median(), 2)}%, "
         f"mínimo {_f(pv.gap_energia_v2g_vs_lcvf_pct.min(), 2)}%.",
         f"- EVs abaixo de 90%: V2G mediana {_f(pv.frac_abaixo_90_pct_v2g.median(), 2)}%, "
         f"LCVF {_f(pv.frac_abaixo_90_pct_lcvf.median(), 2)}%; diferença máxima "
         f"{_f(pv.dif_abaixo_90_pp.max(), 2)} p.p.\n"]
    for nome in ["pico final: V2G × LCVF", "energia líquida: V2G × LCVF", "EVs abaixo de 90%: V2G × LCVF"]:
        if nome in tg.index and pd.notna(tg.loc[nome, "p"]):
            r = tg.loc[nome]
            L.append(f"- Wilcoxon ({nome}, todas): n = {r.n}, V2G menor em {r.x_menor}, "
                     f"p = {r.p:.2e}, efeito rank-biserial {r.efeito_rb:+.2f}.")
    if len(pe):
        L += [f"\n## Contra o ótimo exato (Gurobi, N = {_lista_n(pe.N)})\n",
              f"- V2G abaixo do ótimo de recarga em {int((pe.gap_pico_v2g_vs_exato_pct < 0).sum())} de {len(pe)}; "
              f"gap mediano {_f(pe.gap_pico_v2g_vs_exato_pct.median(), 2)}%, "
              f"máximo {_f(pe.gap_pico_v2g_vs_exato_pct.max(), 2)}%.",
              f"- LCVF acima do ótimo: mediana {_f(pe.gap_pico_lcvf_vs_exato_pct.median(), 2)}%, "
              f"máximo {_f(pe.gap_pico_lcvf_vs_exato_pct.max(), 2)}%."]
    if len(corr):
        L.append("\n## Correlação (Spearman) com a redução do S1, todas as frotas\n")
        for _, r in corr[corr.frota == "todas"].iterrows():
            L.append(f"- {r.x}: ρ = {r.rho:+.2f} (p = {r.p:.1e}, n = {r.n})")
    path.write_text("\n".join(L) + "\n")


# Paleta categórica validada (dataviz/validate_palette.js, modo claro, --pairs all):
# ordem fixa por técnica. O Exato fica abaixo de 3:1 no fundo → sempre com rótulo na legenda.
C_LCVF, C_V2G, C_EXATO = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
WEBP = {"lossless": True, "quality": 100, "method": 4}
plt.rcParams.update({"font.size": 10, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.facecolor": SURF,
                     "axes.facecolor": SURF, "savefig.facecolor": SURF})
ESTACOES = {"winter": "Inverno", "spring": "Primavera", "summer": "Verão", "autumn": "Outono"}
RNG = np.random.default_rng(0)   # jitter determinístico


def fmt_n(n):
    return f"{n:,}".replace(",", ".")


def fmt_pct(x, d=1):
    return f"{x:+.{d}f}".replace(".", ",")


def _strip(ax, x0, vals, color, marker, width=0.28):
    """Pontos de cada dia (jitter horizontal) + marcador da mediana."""
    vals = np.asarray(vals, float)
    vals = vals[~np.isnan(vals)]
    if not len(vals):
        return None
    xs = x0 + RNG.uniform(-width / 2, width / 2, len(vals))
    ax.scatter(xs, vals, s=12, color=color, alpha=0.45, lw=0, marker=marker, zorder=2)
    med = float(np.median(vals))
    ax.scatter([x0], [med], s=64, color=color, marker=marker, edgecolor=SURF, lw=2, zorder=4)
    return med


def _grid(ax, axis="y"):
    (ax.yaxis if axis == "y" else ax.xaxis).grid(color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def fig_gap_por_n(p, path):
    frotas = list(dict.fromkeys(p.frota))
    ncols = 4
    nrows = int(np.ceil(len(frotas) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.3 * ncols, 2.9 * nrows + 0.9), sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, frota in zip(axes, frotas):
        sub = p[p.frota == frota]
        ns = sorted(sub.N.unique())
        ax.axhline(0, color=C_LCVF, lw=1.5, zorder=1)
        meds = [_strip(ax, i, sub[sub.N == n].gap_pico_v2g_vs_lcvf_pct, C_V2G, "o") for i, n in enumerate(ns)]
        ax.plot(range(len(ns)), meds, color=C_V2G, lw=2, zorder=3)
        for i, (n, m) in enumerate(zip(ns, meds)):
            g = sub[sub.N == n].gap_pico_v2g_vs_lcvf_pct
            ax.annotate(f"{fmt_pct(m)}\n{int((g < 0).sum())}/{len(g)}", (i, m), textcoords="offset points",
                        xytext=(15, 0), ha="left", va="center", fontsize=7, color=INK2)
        ax.set_xticks(range(len(ns)), [fmt_n(n) for n in ns], fontsize=8)
        ax.set_xlim(-0.5, len(ns) - 0.2)
        ax.set_title(frota, fontsize=10, color=INK, loc="left")
        _grid(ax)
    for ax in axes[len(frotas):]:
        ax.set_visible(False)
    for ax in axes[::ncols]:
        ax.set_ylabel("Gap de pico vs LCVF (%)")
    handles = [plt.Line2D([], [], color=C_V2G, lw=2, marker="o", label="V2G nivelado (mediana; pontos = dias)"),
               plt.Line2D([], [], color=C_LCVF, lw=1.5, label="LCVF (0%)")]
    fig.legend(handles=handles, loc="upper right", ncol=2, frameon=False, fontsize=9)
    fig.suptitle(f"V2G nivelado × LCVF: gap de pico por frota e tamanho ({p.dia.nunique()} dias)", x=0.01, ha="left",
                 fontsize=12, color=INK)
    fig.text(0.01, 0.005, "Abaixo de 0% = V2G com pico menor. Rótulo: mediana e nº de dias em que o V2G vence. "
             "Eixo x: tamanho da frota (N).", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    fig.savefig(path, dpi=150, pil_kwargs=WEBP)
    plt.close(fig)


def fig_gap_por_estacao(p, path):
    frotas = list(dict.fromkeys(p.frota))
    est = [e for e in ESTACOES if e in set(p.estacao)]
    ncols = 4
    nrows = int(np.ceil(len(frotas) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.3 * ncols, 2.9 * nrows + 0.9), sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, frota in zip(axes, frotas):
        sub = p[p.frota == frota]
        ax.axhline(0, color=C_LCVF, lw=1.5, zorder=1)
        for i, e in enumerate(est):
            g = sub[sub.estacao == e].gap_pico_v2g_vs_lcvf_pct
            m = _strip(ax, i, g, C_V2G, "o")
            if m is not None:
                ax.annotate(fmt_pct(m), (i, m), textcoords="offset points", xytext=(15, 0),
                            ha="left", va="center", fontsize=7, color=INK2)
        ax.set_xticks(range(len(est)), [ESTACOES[e] for e in est], fontsize=8)
        ax.set_xlim(-0.5, len(est) - 0.2)
        ax.set_title(frota, fontsize=10, color=INK, loc="left")
        _grid(ax)
    for ax in axes[len(frotas):]:
        ax.set_visible(False)
    for ax in axes[::ncols]:
        ax.set_ylabel("Gap de pico vs LCVF (%)")
    handles = [plt.Line2D([], [], color=C_V2G, ls="", marker="o", label="V2G nivelado (mediana; pontos = dia × N)"),
               plt.Line2D([], [], color=C_LCVF, lw=1.5, label="LCVF (0%)")]
    fig.legend(handles=handles, loc="upper right", ncol=2, frameon=False, fontsize=9)
    n_est = p.groupby("estacao").dia.nunique().max()
    fig.suptitle(f"V2G nivelado × LCVF: gap de pico por estação (até {n_est} dias por estação × todos os N)",
                 x=0.01, ha="left",
                 fontsize=12, color=INK)
    fig.text(0.01, 0.005, "Abaixo de 0% = V2G com pico menor. Rótulo: mediana.", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    fig.savefig(path, dpi=150, pil_kwargs=WEBP)
    plt.close(fig)


def _lista_n(ns):
    """Tamanhos presentes, ex.: '3.000, 9.000 e 18.000'."""
    v = [fmt_n(n) for n in sorted(set(int(n) for n in ns))]
    return v[0] if len(v) == 1 else ", ".join(v[:-1]) + " e " + v[-1]

def fig_vs_exato(p, path):
    e = p.dropna(subset=["pico_final_kW_gurobi"])
    grupos = list(e.groupby(["frota", "N"], sort=True))[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(grupos) + 1.6))
    ax.axvline(0, color=C_EXATO, lw=1.5, zorder=1)
    for y, ((frota, n), g) in enumerate(grupos):
        for dy, col, cor, mk in [(0.17, "gap_pico_lcvf_vs_exato_pct", C_LCVF, "s"),
                                 (-0.17, "gap_pico_v2g_vs_exato_pct", C_V2G, "o")]:
            v = g[col].to_numpy(float)
            ys = y + dy + RNG.uniform(-0.07, 0.07, len(v))
            ax.scatter(v, ys, s=12, color=cor, alpha=0.45, lw=0, marker=mk, zorder=2)
            ax.scatter([np.median(v)], [y + dy], s=64, color=cor, marker=mk, edgecolor=SURF, lw=2, zorder=4)
    ax.set_yticks(range(len(grupos)), [f"{f} · {fmt_n(n)}" for (f, n), _ in grupos], fontsize=8)
    ax.set_xlabel("Pico final relativo ao Exato (Gurobi) (%)")
    _grid(ax, "x")
    ax.legend(handles=[plt.Line2D([], [], color=C_LCVF, marker="s", ls="", label="LCVF"),
                       plt.Line2D([], [], color=C_V2G, marker="o", ls="", label="V2G nivelado"),
                       plt.Line2D([], [], color=C_EXATO, lw=1.5, label="Exato (0%)")],
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=9)
    ax.set_title(f"Pico final relativo ao ótimo de recarga (Exato), N = {_lista_n(e.N)} ({len(e)} instâncias)",
                 loc="left", fontsize=11, color=INK, pad=28)
    fig.text(0.01, 0.005, "Marcador grande = mediana sobre os dias; pontos pequenos = dias.", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=150, pil_kwargs=WEBP)
    plt.close(fig)


def fig_perfil(entrada, row, path):
    """Perfis sobrepostos no padrão das figuras do V2G/Gurobi (plot_load_profiles)."""
    inst, frota = row.instancia, row.frota
    L = pd.read_csv(entrada / "lcvf_v2g" / frota / inst / "en_profile_final.csv")
    V = pd.read_csv(entrada / "v2g_nivelado" / frota / inst / "en_profile_all_stages.csv")
    ef = entrada / "exact_gurobi" / frota / inst / "en_profile_final.csv"
    conv, lcvf, niv = L.Pconv_inicial.values, L.Pconv_final.values, V.Pconv_apos_S3.values
    curves = [("Initial (no EV)", conv, "blue", "-"), ("LCVF", lcvf, "red", "--"),
              ("V2G leveled", niv, "green", "-.")]
    gaps = [f"V2G leveled vs LCVF: {100 * (niv.max() / lcvf.max() - 1):+.2f}%"]
    if ef.exists():
        ex = pd.read_csv(ef).Pconv_final.values
        curves.append(("Exact (Gurobi)", ex, "black", "-"))
        gaps += [f"LCVF vs exact: {100 * (lcvf.max() / ex.max() - 1):+.2f}%",
                 f"V2G leveled vs exact: {100 * (niv.max() / ex.max() - 1):+.2f}%"]
    title = (f"LCVF × V2G leveled{' × Exact' if ef.exists() else ''} | {frota} · {row.dia} {row.estacao} · "
             f"{row.N:,} EVs\n" + "  |  ".join(gaps))
    plot_load_profiles(curves, conv.min(), title, path, label_below=("V2G leveled",))


def _perfil(args):
    from types import SimpleNamespace
    entrada, row = args[0], SimpleNamespace(**args[1])
    fig_perfil(entrada, row, entrada / "analise" / "comparacao" / "perfis" / f"{row.instancia}.webp")
    return row.instancia


def figuras(entrada: Path, p: pd.DataFrame, perfis: str, jobs: int):
    out = entrada / "analise" / "comparacao"
    out.mkdir(parents=True, exist_ok=True)
    p = p.dropna(subset=["pico_final_kW_v2g", "pico_final_kW_lcvf"])
    cols = ["instancia", "dia", "estacao", "dow", "frota", "N", "pico_conv_kW", "pico_s1_kW",
            "pico_final_kW_v2g", "pico_final_kW_lcvf", "pico_final_kW_gurobi",
            "gap_pico_v2g_vs_lcvf_pct", "gap_pico_v2g_vs_exato_pct", "gap_pico_lcvf_vs_exato_pct"]
    p[cols].round(4).to_csv(out / "comparacao.csv", index=False)
    fig_gap_por_n(p, out / "Fig_01_gap_pico_v2g_vs_lcvf.webp")
    fig_gap_por_estacao(p, out / "Fig_02_gap_pico_estacoes.webp")
    if p.pico_final_kW_gurobi.notna().any():
        fig_vs_exato(p, out / "Fig_03_pico_vs_exato.webp")
    sel = {"todas": p, "nenhuma": p.iloc[:0],
           "sem_exato": p[p.pico_final_kW_gurobi.isna()]}.get(perfis)
    if perfis == "referencia":
        ref = {Path(x).stem.split("_")[2] for x in selecao_dias.reference_days(selecao_dias.select_days()).values()}
        sel = p[p.dia.isin(ref)]
    feitas = {f.stem for f in (out / "perfis").glob("*.webp")}
    sel = sel[~sel.instancia.isin(feitas)]
    if len(sel):
        import multiprocessing as mp
        (out / "perfis").mkdir(exist_ok=True)
        with mp.get_context("fork").Pool(jobs) as pool:
            for _ in pool.imap_unordered(_perfil, [(entrada, r) for r in sel.to_dict("records")]):
                pass
    print(f"figuras: {len(p)} instâncias, {len(sel)} perfis novos → {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--entrada", type=Path, default=DEFAULT_IN)
    ap.add_argument("--sem-figuras", action="store_true", help="só as tabelas")
    ap.add_argument("--perfis", choices=["sem_exato", "todas", "referencia", "nenhuma"], default="sem_exato")
    ap.add_argument("--jobs", type=int, default=4, help="processos para os perfis por instância")
    args = ap.parse_args()
    entrada = args.entrada.resolve()
    out = entrada / "analise"
    out.mkdir(parents=True, exist_ok=True)

    c = consolidar(entrada)
    p = parear(c)
    t = testar(p)
    corr = correlacionar(p)
    falt = faltantes(entrada, c)

    c.round(4).to_csv(out / "consolidado.csv", index=False)
    p.round(4).to_csv(out / "pareado.csv", index=False)
    resumir(p, ["frota", "N"]).round(4).to_csv(out / "resumo_frota_tamanho.csv", index=False)
    resumir(p, ["estacao", "frota"]).round(4).to_csv(out / "resumo_estacao.csv", index=False)
    t.round(6).to_csv(out / "testes_pareados.csv", index=False)
    corr.round(4).to_csv(out / "correlacoes.csv", index=False)
    falt.to_csv(out / "faltantes.csv", index=False)
    escrever_resumo(c, p, t, corr, falt, out / "resumo.md")
    print((out / "resumo.md").read_text())
    print(f"→ {out}")
    if not args.sem_figuras:
        figuras(entrada, p, args.perfis, args.jobs)


if __name__ == "__main__":
    main()
