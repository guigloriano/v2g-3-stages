#!/usr/bin/env python3
"""
gerar_casos.py — material de análise dos casos em que o V2G nivelado não ganha do LCVF (após a correção do LCVF
de 2026-10-07): tabelas por célula e por instância, métricas do mecanismo e figuras, usados por Codes/E_V2G/docs/14_casos_v2g_perde_lcvf.md.

Lê só resultados já gravados (generalization/{v2g_nivelado,lcvf_v2g,exact_gurobi}, analise/pareado.csv,
e o teste da ordem por menor folga na recarga do V2G (teste_ordem_folga_v2g.csv, gerado
por teste_ordem_folga_v2g.py fora do repositório; só as linhas do Boulder são usadas, porque as da N1 foram medidas
antes do dia periódico de 2026-10-08). Não roda nenhum algoritmo.

Uso: python3 gerar_casos.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

AQUI = Path(__file__).resolve().parent
GEN = AQUI.parents[1]                       # …/generalization
FIG = AQUI / "figs"
FIG.mkdir(exist_ok=True)

FROTAS = {"bld_s1": "B1", "bld_s2": "B2", "bld_s4": "B3", "bld_s5": "B4", "nor_s1": "N1"}
NOME = {"bld_s1": "B1 Boulder real", "bld_s2": "B2 Boulder flexible", "bld_s4": "B3 Boulder 8-h shifts",
        "bld_s5": "B4 Boulder 24-h", "nor_s1": "N1 Norway"}
CORES = {"bld_s1": "#1f77b4", "bld_s2": "#ff7f0e", "bld_s4": "#d62728", "bld_s5": "#9467bd", "nor_s1": "#4d4d4d"}
C_LCVF, C_V2G, C_EXATO, C_CONV, C_S1 = "#2a78d6", "#eb6834", "#1baf7a", "#4d4d4d", "#9a6fb0"
# células em foco: LCVF significativamente melhor (B1 ≥ 39k) e sem diferença (B2 50k). Com o dia periódico
# (2026-10-08) a N1 deixou de perder; a N1 32.330 entra como contraexemplo (V2G ganha com N alto).
FOCO = [("bld_s1", 39000), ("bld_s1", 50336), ("bld_s2", 50336)]
CONTRAEXEMPLO = ("nor_s1", 32330)


def ler_instancia(fr, inst):
    v = pd.read_csv(GEN / "v2g_nivelado" / fr / inst / "en_profile_all_stages.csv")
    l = pd.read_csv(GEN / "lcvf_v2g" / fr / inst / "en_profile_final.csv")
    e = pd.read_csv(GEN / "exact_gurobi" / fr / inst / "en_profile_final.csv")
    return v, l, e


def metricas(p):
    rows = []
    for r in p.itertuples():
        v, l, e = ler_instancia(r.frota, r.instancia)
        conv = v.Pconv_inicial.values
        h = lambda y: (int(np.argmax(y)) ) / 6
        rows.append(dict(
            instancia=r.instancia,
            E_descarga_MWh=v.Energia_S1_discharge_kWh.sum() / 1e3,
            E_recarga_v2g_MWh=(v.Energia_S2_mincharge_kWh.sum() + v.Energia_S3_fullcharge_kWh.sum()) / 1e3,
            E_lcvf_MWh=l.Energia_alocada_kWh.sum() / 1e3,
            hora_pico_conv=h(conv), hora_pico_s1=h(v.Pconv_apos_S1.values),
            hora_pico_v2g=h(v.Pconv_apos_S3.values), hora_pico_lcvf=h(l.Pconv_final.values),
            hora_pico_exato=h(e.Pconv_final.values),
        ))
    m = pd.DataFrame(rows)
    q = p.merge(m, on="instancia")
    q["dP_pct"] = 100 * (q.pico_final_kW_v2g / q.pico_final_kW_lcvf - 1)          # > 0: V2G pior
    q["elev_lcvf_pct"] = 100 * (q.pico_final_kW_lcvf / q.pico_conv_kW - 1)          # quanto a recarga sobe o pico
    q["subida_pos_s1_pct"] = 100 * (q.pico_final_kW_v2g / q.pico_s1_kW - 1)          # quanto a recarga sobe o pico do S1
    q["recarga_extra_MWh"] = q.E_recarga_v2g_MWh - q.E_lcvf_MWh
    q["descarga_sobre_frota_pct"] = 100 * q.E_descarga_MWh * 1e3 / q.energia_frota_90_kWh
    # mesma contagem do relatório: V2G menor se ΔP < 0 (670); empate se 0 ≤ ΔP ≤ 0,005% (diferença de arredondamento)
    q["resultado"] = np.where(q.dP_pct < 0, "V2G menor", np.where(q.dP_pct > 0.005, "LCVF menor", "empate"))
    return q


def tabela_celulas(q):
    # mesma família do relatório (gerar_relatorio.tab_lcvf): Wilcoxon por célula, Holm sobre as 24 células.
    # (analise/testes_pareados.csv corrige 72 testes juntos e dá p maiores, ex. B1 39k: 0,058 lá, 0,013 aqui.)
    sys.path.insert(0, str(GEN.parents[3] / "Articles" / "Energy"))   # gerar_relatorio.py (desde 2026-10-09)
    import gerar_relatorio as gr
    grupos = list(q.groupby(["frota", "N"]))
    ps = [gr.wilcoxon(g.pico_final_kW_lcvf, g.pico_final_kW_v2g)["p"] for _, g in grupos]
    aj = dict(zip([k for k, _ in grupos], gr.holm(ps)))
    linhas = []
    for (fr, n), g in grupos:
        p_holm = float(aj[(fr, n)])
        med = g.dP_pct.median()
        sig = "LCVF melhor" if (p_holm < 0.05 and med > 0) else ("V2G melhor" if (p_holm < 0.05 and med < 0) else "n.s.")
        linhas.append(dict(
            frota=FROTAS[fr], N=n, dias=len(g),
            V2G_menor=int((g.resultado == "V2G menor").sum()), LCVF_menor=int((g.resultado == "LCVF menor").sum()),
            empates=int((g.resultado == "empate").sum()),
            dP_mediana_pct=round(med, 2), dP_min_pct=round(g.dP_pct.min(), 2), dP_max_pct=round(g.dP_pct.max(), 2),
            p_holm=p_holm, veredito=sig,
            reducao_s1_mediana_pct=round(g.reducao_s1_pct.median(), 2),
            elev_lcvf_mediana_pct=round(g.elev_lcvf_pct.median(), 2),
            subida_pos_s1_mediana_pct=round(g.subida_pos_s1_pct.median(), 2),
            descarga_MWh_mediana=round(g.E_descarga_MWh.median(), 1),
            descarga_sobre_frota_pct=round(g.descarga_sobre_frota_pct.median(), 1),
            dE_mediana_pct=round(g.gap_energia_v2g_vs_lcvf_pct.median(), 2),
            dB_mediana_pp=round(g.dif_abaixo_90_pp.median(), 2),
            v2g_vs_exato_mediana_pct=round(g.gap_pico_v2g_vs_exato_pct.median(), 2),
            frac_conectados_pico_pct=round(100 * g.frac_conectados_pico.median(), 0),
        ))
    c = pd.DataFrame(linhas)
    c["_o"] = c.frota.map({v: i for i, v in enumerate(FROTAS.values())})
    return c.sort_values(["_o", "N"]).drop(columns="_o")


def fig_mapa(c):
    """Mediana de ΔP por célula (vermelho = LCVF menor) com o veredito de Holm."""
    ns = [3000, 9000, 18000, 32330, 39000, 50336]
    fs = list(FROTAS.values())
    M = np.full((len(fs), len(ns)), np.nan)
    for r in c.itertuples():
        M[fs.index(r.frota), ns.index(r.N)] = r.dP_mediana_pct
    lim = np.nanmax(np.abs(M))
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    im = ax.imshow(M, cmap="RdBu_r", vmin=-lim, vmax=lim, aspect="auto")
    for r in c.itertuples():
        i, j = fs.index(r.frota), ns.index(r.N)
        mark = {"LCVF melhor": " ▲", "V2G melhor": "", "n.s.": " (n.s.)"}[r.veredito]
        ax.text(j, i, f"{r.dP_mediana_pct:+.2f}%{mark}", ha="center", va="center", fontsize=7.5,
                color="white" if abs(r.dP_mediana_pct) > lim * 0.55 else "black")
    ax.set_xticks(range(len(ns)), [f"{n:,}" for n in ns], fontsize=8)
    ax.set_yticks(range(len(fs)), fs, fontsize=8)
    ax.set_xlabel("Fleet size N", fontsize=8.5)
    cb = fig.colorbar(im, ax=ax, pad=0.02)
    cb.set_label("median ΔP = P_V2G/P_LCVF − 1 (%)", fontsize=7.5); cb.ax.tick_params(labelsize=7)
    ax.set_title("V2G final peak vs LCVF, median over 32 days  (▲ LCVF significantly lower, Holm)", fontsize=8.5, loc="left")
    fig.tight_layout()
    fig.savefig(FIG / "01_mapa_celulas.png", dpi=170)
    plt.close(fig)


def fig_mecanismo(q):
    """ΔP contra (a) a elevação do pico causada pela recarga do LCVF e (b) a energia descarregada no S1."""
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8), sharey=True)
    for fr in FROTAS:
        g = q[q.frota == fr]
        sz = 4 + 18 * (g.N / 50336)
        axes[0].scatter(g.elev_lcvf_pct, g.dP_pct, s=sz, color=CORES[fr], alpha=0.6, lw=0, label=NOME[fr])
        axes[1].scatter(g.descarga_sobre_frota_pct, g.dP_pct, s=sz, color=CORES[fr], alpha=0.6, lw=0)
    for ax in axes:
        ax.axhline(0, color="black", lw=0.8)
        ax.grid(True, ls="--", alpha=0.4)
    axes[0].set_xlabel("LCVF peak above the conventional peak (%)\n= how much charging alone raises the peak", fontsize=8.5)
    axes[1].set_xlabel("Stage 1 discharge / fleet energy to 90% (%)", fontsize=8.5)
    axes[0].set_ylabel("ΔP = P_V2G / P_LCVF − 1 (%)\n> 0: V2G loses", fontsize=8.5)
    axes[0].legend(fontsize=7, loc="lower left", markerscale=1.5)
    fig.suptitle("Where the V2G pipeline loses: all 768 instances (marker size ∝ N)", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(FIG / "02_mecanismo_dispersao.png", dpi=170)
    plt.close(fig)


def fig_horas(q):
    """Hora do pico final: V2G × LCVF nas células em foco, num controle (B1 9k) e no contraexemplo (N1 32k)."""
    cel = FOCO + [("bld_s1", 9000), CONTRAEXEMPLO]
    fig, axes = plt.subplots(1, len(cel), figsize=(11, 2.6), sharey=True)
    for ax, (fr, n) in zip(axes, cel):
        g = q[(q.frota == fr) & (q.N == n)]
        bins = np.arange(0, 24.5, 1)
        ax.hist(g.hora_pico_conv, bins=bins, color=C_CONV, alpha=0.35, label="conventional")
        ax.hist(g.hora_pico_lcvf, bins=bins, histtype="step", color=C_LCVF, lw=1.5, label="LCVF final")
        ax.hist(g.hora_pico_v2g, bins=bins, histtype="step", color=C_V2G, lw=1.5, label="V2G final")
        ax.set_title(f"{FROTAS[fr]} N={n:,}", fontsize=8)
        ax.set_xticks([0, 6, 12, 18, 24]); ax.tick_params(labelsize=7)
        ax.set_xlabel("hour of peak", fontsize=7.5)
    axes[0].set_ylabel("days", fontsize=8)
    axes[0].legend(fontsize=6.5, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "03_hora_do_pico.png", dpi=170)
    plt.close(fig)


def fig_perfil(q, fr, n, qual, nome):
    """Carga e potência dos EVs numa instância: dia mediano ('mediana') ou pior dia ('pior') da célula."""
    g = q[(q.frota == fr) & (q.N == n)].sort_values("dP_pct")
    r = g.iloc[len(g) // 2] if qual == "mediana" else g.iloc[-1] if qual == "pior" else g.iloc[0]
    v, l, e = ler_instancia(fr, r.instancia)
    t = np.arange(144) / 6
    conv = v.Pconv_inicial.values
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 5.4), sharex=True, gridspec_kw={"height_ratios": [1.3, 1]})
    a1.plot(t, conv / 1e3, color=C_CONV, lw=1.1, label=f"conventional (peak {conv.max()/1e3:.1f} MW)")
    a1.plot(t, v.Pconv_apos_S1 / 1e3, color=C_S1, lw=1.1, ls=":", label=f"after Stage 1 (peak {v.Pconv_apos_S1.max()/1e3:.1f})")
    a1.plot(t, l.Pconv_final / 1e3, color=C_LCVF, lw=1.5, ls="--", label=f"LCVF final (peak {l.Pconv_final.max()/1e3:.1f})")
    a1.plot(t, e.Pconv_final / 1e3, color=C_EXATO, lw=1.0, label=f"exact charging-only (peak {e.Pconv_final.max()/1e3:.1f})")
    a1.plot(t, v.Pconv_apos_S3 / 1e3, color=C_V2G, lw=1.5, label=f"V2G final (peak {v.Pconv_apos_S3.max()/1e3:.1f})")
    a1.set_ylabel("Total load (MW)", fontsize=8.5)
    a1.legend(fontsize=7, loc="upper left")
    a1.grid(True, ls="--", alpha=0.4)
    a1.set_title(f"{NOME[fr]}, N = {n:,}, {r.dia} ({qual} day of the cell): ΔP = {r.dP_pct:+.2f}%", fontsize=9, loc="left")
    mw = 6 / 1e3
    a2.axhline(0, color="black", lw=0.7)
    dis = -v.Energia_S1_discharge_kWh.values * mw
    a2.fill_between(t, dis, 0, color=C_V2G, alpha=0.3, lw=0, label=f"V2G discharge (Stage 1): {r.E_descarga_MWh:.1f} MWh")
    a2.plot(t, (v.Energia_S2_mincharge_kWh + v.Energia_S3_fullcharge_kWh).values * mw, color=C_V2G, lw=1.3,
            label=f"V2G recharge (Stages 2+3): {r.E_recarga_v2g_MWh:.1f} MWh")
    a2.plot(t, l.Energia_alocada_kWh.values * mw, color=C_LCVF, lw=1.3, ls="--", label=f"LCVF charging: {r.E_lcvf_MWh:.1f} MWh")
    a2.set_ylabel("EV power (MW)", fontsize=8.5); a2.set_xlabel("Time (h)", fontsize=8.5)
    a2.legend(fontsize=7, loc="upper left")
    a2.grid(True, ls="--", alpha=0.4)
    a2.set_xlim(0, 24); a2.set_xticks(range(0, 25, 2))
    for ax in (a1, a2):
        ax.tick_params(labelsize=7.5)
    fig.tight_layout()
    fig.savefig(FIG / f"{nome}.png", dpi=170)
    plt.close(fig)
    return r


def teste_folga(q):
    t = pd.read_csv(AQUI / "teste_ordem_folga_v2g.csv")
    t = t[t.fr != "nor_s1"]     # N1 medida antes do dia periódico (2026-10-08): fora
    t["instancia"] = "inst_" + t.N.astype(str) + "_" + t.fr + "_" + t.dia
    t = t.merge(q[["instancia", "pico_final_kW_v2g", "pico_final_kW_lcvf"]], on="instancia")
    t["mud_vs_v2g_pct"] = 100 * (t.pico / t.pico_final_kW_v2g - 1)
    t["dP_novo_pct"] = 100 * (t.pico / t.pico_final_kW_lcvf - 1)
    t["dP_orig_pct"] = 100 * (t.pico_final_kW_v2g / t.pico_final_kW_lcvf - 1)
    r = (t.groupby(["var", "fr", "N"])
         .agg(dias=("pico", "size"), mud_mediana_pct=("mud_vs_v2g_pct", "median"), mud_min_pct=("mud_vs_v2g_pct", "min"),
              mud_max_pct=("mud_vs_v2g_pct", "max"), dP_orig_mediana_pct=("dP_orig_pct", "median"),
              dP_novo_mediana_pct=("dP_novo_pct", "median")).round(2).reset_index())
    r["fr"] = r.fr.map(FROTAS)
    tot = t.groupby("var").mud_vs_v2g_pct.agg(["median", "min", "max"]).round(2)
    return r, tot


def main():
    p = pd.read_csv(GEN / "analise" / "pareado.csv")
    p = p[p.frota.isin(FROTAS)]
    q = metricas(p)
    c = tabela_celulas(q)
    c.to_csv(AQUI / "tabela_celulas.csv", index=False)
    cols = ["frota", "N", "dia", "estacao", "dP_pct", "resultado", "pico_conv_kW", "pico_s1_kW", "pico_final_kW_v2g",
            "pico_final_kW_lcvf", "pico_final_kW_gurobi", "reducao_s1_pct", "elev_lcvf_pct", "subida_pos_s1_pct",
            "E_descarga_MWh", "E_recarga_v2g_MWh", "E_lcvf_MWh", "recarga_extra_MWh", "descarga_sobre_frota_pct",
            "gap_energia_v2g_vs_lcvf_pct", "dif_abaixo_90_pp", "hora_pico_conv", "hora_pico_s1", "hora_pico_v2g",
            "hora_pico_lcvf", "frac_conectados_pico", "instancia"]
    q["frota"] = q.frota.map(FROTAS)
    q.sort_values(["frota", "N", "dP_pct"])[cols].round(3).to_csv(AQUI / "instancias.csv", index=False)
    q["frota"] = q.frota.map({v: k for k, v in FROTAS.items()})
    fig_mapa(c)
    fig_mecanismo(q)
    fig_horas(q)
    reps = []
    for fr, n in FOCO:
        for qual in ("mediana", "pior"):
            r = fig_perfil(q, fr, n, qual, f"perfil_{FROTAS[fr]}_{n}_{qual}")
            reps.append(dict(celula=f"{FROTAS[fr]} {n}", qual=qual, dia=r.dia, dP_pct=round(r.dP_pct, 2),
                             instancia=r.instancia, figura=f"figs/perfil_{FROTAS[fr]}_{n}_{qual}.png"))
    r = fig_perfil(q, "bld_s1", 9000, "mediana", "perfil_B1_9000_mediana_controle")
    reps.append(dict(celula="B1 9000 (controle)", qual="mediana", dia=r.dia, dP_pct=round(r.dP_pct, 2),
                     instancia=r.instancia, figura="figs/perfil_B1_9000_mediana_controle.png"))
    r = fig_perfil(q, *CONTRAEXEMPLO, "mediana", "perfil_N1_32330_mediana_contraexemplo")
    reps.append(dict(celula="N1 32330 (contraexemplo)", qual="mediana", dia=r.dia, dP_pct=round(r.dP_pct, 2),
                     instancia=r.instancia, figura="figs/perfil_N1_32330_mediana_contraexemplo.png"))
    pd.DataFrame(reps).to_csv(AQUI / "instancias_representativas.csv", index=False)
    tf, tot = teste_folga(q)
    tf.to_csv(AQUI / "teste_ordem_folga_resumo.csv", index=False)
    print(c.to_string()); print(tf.to_string()); print(tot)
    # correlações de Spearman de ΔP com as métricas do mecanismo
    from scipy import stats
    for x in ["elev_lcvf_pct", "descarga_sobre_frota_pct", "reducao_s1_pct", "frac_conectados_pico", "subida_pos_s1_pct"]:
        rr = stats.spearmanr(q[x], q.dP_pct)
        print(f"spearman dP x {x}: {rr.statistic:+.2f}")


if __name__ == "__main__":
    main()
