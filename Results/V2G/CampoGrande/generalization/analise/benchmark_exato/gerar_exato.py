#!/usr/bin/env python3
"""
gerar_exato.py — material do benchmark exato (Gurobi) em todas as 768 instâncias, para Codes/E_V2G/docs/15_benchmark_exato_gurobi.md:
distância do LCVF e do V2G ao ótimo só de carga, instâncias em que o V2G fica acima do ótimo e escala do modelo exato.

Lê só resultados gravados (analise/pareado.csv, execucoes_gurobi_sequencial.csv e os perfis por instância). Não roda
nenhum algoritmo. Uso: python3 gerar_exato.py
"""
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

AQUI = Path(__file__).resolve().parent
GEN = AQUI.parents[1]
FIG = AQUI / "figs"
FIG.mkdir(exist_ok=True)
FROTAS = {"bld_s1": "B1", "bld_s2": "B2", "bld_s4": "B3", "bld_s5": "B4", "nor_s1": "N1"}
NOME = {"bld_s1": "B1 Boulder real", "bld_s2": "B2 Boulder flexible", "bld_s4": "B3 Boulder 8-h shifts",
        "bld_s5": "B4 Boulder 24-h", "nor_s1": "N1 Norway"}
CORES = {"bld_s1": "#1f77b4", "bld_s2": "#ff7f0e", "bld_s4": "#d62728", "bld_s5": "#9467bd", "nor_s1": "#4d4d4d"}
C_LCVF, C_V2G, C_EXATO, C_CONV = "#2a78d6", "#eb6834", "#1baf7a", "#4d4d4d"
RNG = np.random.default_rng(42)


def md(df):
    h = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns) + "\n"
    return h + "".join("| " + " | ".join(str(v) for v in r) + " |\n" for r in df.values)


def strip(p, col, fname, titulo, cor, ylim=None):
    fig, axes = plt.subplots(1, 5, figsize=(11, 3.0), sharey=True)
    for ax, fr in zip(axes, FROTAS):
        g = p[p.frota == fr]
        ns = sorted(g.N.unique())
        for k, n in enumerate(ns):
            v = g[g.N == n][col].values
            ax.scatter(k + RNG.uniform(-0.18, 0.18, len(v)), v, s=7, color=cor, alpha=0.5, lw=0)
            ax.scatter([k], [np.median(v)], s=34, color=cor, edgecolor="white", lw=1, zorder=3)
        ax.axhline(0, color=C_EXATO, lw=1)
        ax.set_xticks(range(len(ns)), [f"{n // 1000}k" if n != 50336 else "50.3k" for n in ns], fontsize=7.5)
        ax.set_title(NOME[fr], fontsize=8.5, loc="left")
        ax.grid(axis="y", ls="--", alpha=0.4)
        ax.tick_params(labelsize=7.5)
    axes[0].set_ylabel("peak vs exact optimum (%)", fontsize=8.5)
    if ylim:
        axes[0].set_ylim(*ylim)
    fig.suptitle(titulo, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(FIG / fname, dpi=170)
    plt.close(fig)


def perfil(row, fname, titulo):
    E = GEN
    v = pd.read_csv(E / "v2g_nivelado" / row.frota / row.instancia / "en_profile_all_stages.csv")
    l = pd.read_csv(E / "lcvf_v2g" / row.frota / row.instancia / "en_profile_final.csv")
    e = pd.read_csv(E / "exact_gurobi" / row.frota / row.instancia / "en_profile_final.csv")
    t = np.arange(144) / 6
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.plot(t, v.Pconv_inicial / 1e3, color=C_CONV, lw=1.0, label=f"conventional ({v.Pconv_inicial.max()/1e3:.1f} MW)")
    ax.plot(t, e.Pconv_final / 1e3, color=C_EXATO, lw=1.6, label=f"exact charging-only ({e.Pconv_final.max()/1e3:.2f})")
    ax.plot(t, l.Pconv_final / 1e3, color=C_LCVF, lw=1.3, ls="--", label=f"LCVF ({l.Pconv_final.max()/1e3:.2f})")
    ax.plot(t, v.Pconv_apos_S3 / 1e3, color=C_V2G, lw=1.3, label=f"V2G ({v.Pconv_apos_S3.max()/1e3:.2f})")
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 2)); ax.tick_params(labelsize=7.5)
    ax.set_xlabel("Time (h)", fontsize=8.5); ax.set_ylabel("Total load (MW)", fontsize=8.5)
    ax.grid(True, ls="--", alpha=0.4)
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title(titulo, fontsize=8.5, loc="left")
    fig.tight_layout()
    fig.savefig(FIG / fname, dpi=170)
    plt.close(fig)


def escala(s):
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.2))
    for fr in FROTAS:
        g = s[s.frota == fr].groupby("N")
        ns = np.array(sorted(g.groups))
        axes[0].plot(ns, g.presolve_colunas.median().loc[ns] / 1e6, "o-", color=CORES[fr], ms=3.5, label=NOME[fr])
        axes[1].plot(ns, g.wall_solver_s.median().loc[ns], "o-", color=CORES[fr], ms=3.5)
        axes[2].plot(ns, g.rss_pico_amostrado_mb.max().loc[ns] / 1024, "o-", color=CORES[fr], ms=3.5)
    ns = np.array([3000, 50336])
    axes[1].plot(ns, 3 * (ns / 3000) ** 1.0, ":", color="#999", lw=1, label="∝ N")
    axes[1].plot(ns, 3 * (ns / 3000) ** 2.0, "--", color="#999", lw=1, label="∝ N²")
    axes[0].set_ylabel("variables after presolve (M)", fontsize=8.5)
    axes[1].set_ylabel("solver time, median (s)", fontsize=8.5)
    axes[2].set_ylabel("peak memory, max (GiB)", fontsize=8.5)
    axes[1].set_xscale("log"); axes[1].set_yscale("log")
    tk = [3000, 9000, 18000, 32330, 50336]
    axes[1].set_xticks(tk, [f"{n // 1000}k" for n in tk]); axes[1].minorticks_off()
    for ax in axes:
        ax.set_xlabel("N", fontsize=8.5); ax.grid(True, ls="--", alpha=0.4); ax.tick_params(labelsize=7.5)
    axes[0].legend(fontsize=7); axes[1].legend(fontsize=7)
    fig.suptitle("Scaling of the exact charging-only model (one instance at a time, 4 threads)", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(FIG / "05_escala_gurobi.png", dpi=170)
    plt.close(fig)


def main():
    p = pd.read_csv(GEN / "analise" / "pareado.csv")
    p = p[p.frota.isin(FROTAS)].copy()
    gl, gv = p.gap_pico_lcvf_vs_exato_pct, p.gap_pico_v2g_vs_exato_pct
    gl_v = 100 * (p.pico_final_kW_v2g / p.pico_final_kW_lcvf - 1)
    p["dP_v2g_lcvf_pct"] = gl_v

    # ── figuras ──
    strip(p, "gap_pico_lcvf_vs_exato_pct", "01_lcvf_vs_exato.png",
          "LCVF final peak vs exact charging-only optimum (points: days; large: median)", C_LCVF)
    strip(p, "gap_pico_v2g_vs_exato_pct", "02_v2g_vs_exato.png",
          "V2G final peak vs exact charging-only optimum (below 0: V2G beats any charging-only schedule)", C_V2G)
    fig, ax = plt.subplots(figsize=(6.5, 3.0))
    bins = np.linspace(gl.min() - 0.01, gl.max() + 0.01, 70)
    for fr in FROTAS:
        ax.hist(gl[p.frota == fr], bins=bins, histtype="step", color=CORES[fr], lw=1.2, label=NOME[fr])
    ax.axvline(0.01, color="black", ls=":", lw=1); ax.text(0.02, ax.get_ylim()[1] * 0.85, "+0.01%", fontsize=7)
    ax.set_yscale("log"); ax.set_xlabel("LCVF peak vs optimum (%)", fontsize=8.5); ax.set_ylabel("instances", fontsize=8.5)
    ax.legend(fontsize=7); ax.tick_params(labelsize=7.5); ax.grid(True, ls="--", alpha=0.4)
    fig.tight_layout(); fig.savefig(FIG / "03_hist_lcvf.png", dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    for fr in FROTAS:
        g = p[p.frota == fr]
        ax.scatter(g.gap_pico_v2g_vs_exato_pct, g.dP_v2g_lcvf_pct, s=5 + 15 * g.N / 50336, color=CORES[fr], alpha=0.6, lw=0, label=NOME[fr])
    ax.axhline(0, color="black", lw=0.7); ax.axvline(0, color=C_EXATO, lw=1)
    ax.set_xlabel("V2G peak vs exact optimum (%)", fontsize=8.5)
    ax.set_ylabel("V2G peak vs LCVF (%)", fontsize=8.5)
    ax.legend(fontsize=7, loc="upper left"); ax.tick_params(labelsize=7.5); ax.grid(True, ls="--", alpha=0.4)
    ax.set_title("V2G above the optimum ⇔ V2G above LCVF (LCVF ≈ optimum)", fontsize=8.5, loc="left")
    fig.tight_layout(); fig.savefig(FIG / "04_v2g_exato_vs_lcvf.png", dpi=170); plt.close(fig)

    s = pd.read_csv(GEN / "execucoes_gurobi_sequencial.csv")
    s = s[s.status == "ok"].drop_duplicates("instancia", keep="last")
    escala(s)

    # instâncias ilustrativas: maior gap do LCVF (B3 50k) e V2G acima do ótimo (N1 32k, pior)
    r1 = p.loc[gl.idxmax()]
    perfil(r1, "06_perfil_maior_gap_lcvf.png", f"Largest LCVF gap: {NOME[r1.frota]}, N = {r1.N:,}, {r1.dia}: "
           f"LCVF {r1.gap_pico_lcvf_vs_exato_pct:+.2f}%, V2G {r1.gap_pico_v2g_vs_exato_pct:+.2f}% vs optimum")
    r2 = p.loc[gv.idxmax()]
    perfil(r2, "07_perfil_v2g_acima.png", f"Largest V2G excess: {NOME[r2.frota]}, N = {r2.N:,}, {r2.dia}: "
           f"V2G {r2.gap_pico_v2g_vs_exato_pct:+.2f}%, LCVF {r2.gap_pico_lcvf_vs_exato_pct:+.2f}% vs optimum")
    r3 = p.loc[gv.idxmin()]
    perfil(r3, "08_perfil_v2g_abaixo.png", f"Largest V2G gain: {NOME[r3.frota]}, N = {r3.N:,}, {r3.dia}: "
           f"V2G {r3.gap_pico_v2g_vs_exato_pct:+.2f}% vs optimum")

    # ── tabelas ──
    linhas = []
    for (fr, n), g in p.groupby(["frota", "N"]):
        a, b = g.gap_pico_lcvf_vs_exato_pct, g.gap_pico_v2g_vs_exato_pct
        linhas.append(dict(frota=FROTAS[fr], N=n, LCVF_med=round(a.median(), 3), LCVF_max=round(a.max(), 2),
                           LCVF_ate_001=int((a < 0.01).sum()), V2G_med=round(b.median(), 2), V2G_min=round(b.min(), 2),
                           V2G_max=round(b.max(), 2), V2G_abaixo=int((b < 0).sum()), V2G_acima=int((b > 0).sum())))
    t = pd.DataFrame(linhas)
    t["_o"] = t.frota.map({v: i for i, v in enumerate(FROTAS.values())})
    t = t.sort_values(["_o", "N"]).drop(columns="_o")
    t.to_csv(AQUI / "tabela_celulas.csv", index=False)
    (AQUI / "tabela_celulas.md").write_text(md(t))
    acima = p[gv > 0][["frota", "N", "dia", "gap_pico_v2g_vs_exato_pct", "gap_pico_lcvf_vs_exato_pct", "dP_v2g_lcvf_pct",
                       "instancia"]].copy()
    acima["frota"] = acima.frota.map(FROTAS)
    acima.round(3).sort_values(["frota", "N", "dia"]).to_csv(AQUI / "v2g_acima_do_otimo.csv", index=False)
    lc = p[gl >= 0.01][["frota", "N", "dia", "gap_pico_lcvf_vs_exato_pct", "instancia"]].copy()
    lc["frota"] = lc.frota.map(FROTAS)
    lc.round(3).sort_values(["frota", "N", "dia"]).to_csv(AQUI / "lcvf_acima_001.csv", index=False)
    g = s.groupby(["frota", "N"])
    e = pd.DataFrame({"execucoes": g.size(), "vars_M": (g.presolve_colunas.median() / 1e6).round(2),
                      "solver_med_s": g.wall_solver_s.median().round(0), "solver_max_s": g.wall_solver_s.max().round(0),
                      "total_h": (g.wall_s.sum() / 3600).round(2),
                      "mem_max_GiB": (g.rss_pico_amostrado_mb.max() / 1024).round(1)}).reset_index()
    e["frota"] = e.frota.map(FROTAS)
    e["_o"] = e.frota.map({v: i for i, v in enumerate(FROTAS.values())})
    e = e.sort_values(["_o", "N"]).drop(columns="_o")
    e.to_csv(AQUI / "escala_gurobi.csv", index=False)
    (AQUI / "escala_gurobi.md").write_text(md(e))
    # expoentes (mediana do tempo do solver, 3k→50k)
    m = s.groupby(["frota", "N"]).wall_solver_s.median().unstack()
    exps = {FROTAS[f]: round(float(np.log(m.loc[f, 50336] / m.loc[f, 3000]) / np.log(50336 / 3000)), 2)
            for f in FROTAS if f != "nor_s1"}
    exps["N1"] = round(float(np.log(m.loc["nor_s1", 32330] / m.loc["nor_s1", 3000]) / np.log(32330 / 3000)), 2)
    resumo = dict(
        n=len(p), lcvf_ate_001=int((gl < 0.01).sum()), lcvf_ate_1=int((gl < 1).sum()), lcvf_max=round(gl.max(), 2),
        lcvf_min=round(gl.min(), 2), lcvf_med=round(gl.median(), 3), lcvf_exato=int((gl.abs() < 1e-9).sum()),
        lcvf_acima_001_B3=int(((gl >= 0.01) & (p.frota == "bld_s4")).sum()), lcvf_acima_001=int((gl >= 0.01).sum()),
        v2g_abaixo=int((gv < 0).sum()), v2g_acima=int((gv > 0).sum()), v2g_acima_med=round(gv[gv > 0].median(), 2),
        v2g_acima_max=round(gv.max(), 2), v2g_min=round(gv.min(), 2), v2g_med=round(gv.median(), 2),
        v2g_acima_e_acima_lcvf=int(((gv > 0) & (p.pico_final_kW_v2g > p.pico_final_kW_lcvf)).sum()),
        expoentes=exps, horas_total=round(s.wall_s.sum() / 3600, 1),
        r1=r1.instancia, r2=r2.instancia, r3=r3.instancia)
    for k, v in resumo.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
