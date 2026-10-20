#!/usr/bin/env python3
"""
figuras_exato.py — figuras por instância do Gurobi na generalização de 32 dias.

Junta, para esta pasta, o que fazem Codes/Exact_Alg/gurobi_alg/plot_result.py e
Codes/Exact_Alg/comparacoes/compare_pipelines.py (commit ef994364): as funções de desenho
(`plot_load_profiles`) foram copiadas sem mudança; a leitura passa a usar o nome da
instância do dia (inst_<N>_<frota>_cgr_<YYYYMMDD>_<estação>_<dia>).

  Fig_exact.webp    carga inicial × carga final do Exato (como plot_result.py)
  Fig_compare.webp  inicial, LCVF, V2G nivelado e Exato sobrepostos, com o gap de pico de
                    cada técnica contra o Exato no título (como compare_pipelines.py)

As duas são gravadas na pasta da instância do Gurobi. run_generalizacao.py chama este
script após cada instância do Gurobi; `plot_load_profiles` também é usada por
analise_generalizacao.py (perfis sobrepostos por instância).

Uso: python3 figuras_exato.py <N> <frota> <pc_cgr_YYYYMMDD_<estação>_<dia>.csv>
     GENERALIZACAO_SAIDA=<dir> muda a raiz dos resultados (padrão: Results/V2G/CampoGrande/generalization)
"""
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import AutoMinorLocator
import numpy as np
import pandas as pd

HERE      = Path(__file__).resolve().parent              # Codes/E_V2G/scripts/generalização_32dias
REPO_ROOT = HERE.parents[3]
SAIDA     = Path(os.environ.get('GENERALIZACAO_SAIDA',
                                REPO_ROOT / 'Results' / 'V2G' / 'CampoGrande' / 'generalization'))

# Mesmo layout de plot_stages() em Codes/E_V2G/scripts/v2g_stable.py (que
# segue as figuras por iteração do lcvf_fast_v2g.py): figura 24x13, fontes
# grandes, eixo y em "k", eixo x em time slots 1-144, legenda acima dos eixos,
# pico de cada curva marcado e repetido na legenda, textos em inglês.
# Generalização: 150 dpi e method=4 (sem perdas). Medido em 2026-10-03: ~57% menor que 360 dpi e
# ~15× mais rápido que method=6, que dá o mesmo tamanho que method=4.
DPI          = 150
WEBP_KWARGS  = {"lossless": True, "quality": 100, "method": 4}
TOTAL_TIMESLOTS = 144
F_TICK, F_LABEL, F_TITLE, F_LEGEND = 24, 30, 36, 24


def plot_load_profiles(curves, min_conv, title, out_path, label_below=()):
    """Desenha curvas de carga no padrão das figuras do V2G/LCVF e salva em out_path.

    curves: lista de (label, valores[144], cor, estilo de linha), na ordem da legenda.
    label_below: labels cujo rótulo de pico vai abaixo do marcador.
    """
    with plt.rc_context(matplotlib.rcParamsDefault):  # não herda rcParams de quem chama
        return _plot_load_profiles(curves, min_conv, title, out_path, label_below)


def _plot_load_profiles(curves, min_conv, title, out_path, label_below):
    x = np.arange(1, TOTAL_TIMESLOTS + 1)
    fig, ax = plt.subplots(figsize=(24, 13))
    all_vals = np.concatenate([np.asarray(c[1], float) for c in curves])
    y_lo, y_hi = min_conv * 0.9, all_vals.max() * 1.1
    placed, handles = [], []
    for label, vals, color, ls in curves:
        vals = np.asarray(vals, float)
        i = int(np.argmax(vals))
        peak = vals[i]
        line, = ax.plot(x, vals, color=color, ls=ls, lw=3.0, zorder=3,
                        label=f"{label} — max: {peak:,.2f} kW")
        handles.append(line)
        ax.axhline(peak, color=color, ls=":", lw=1.5, alpha=0.8, zorder=2)
        ax.plot(x[i], peak, "o", ms=16, color=color, markeredgecolor="white",
                markeredgewidth=2.5, zorder=4)
        # rótulo abaixo do marcador se pedido, ou se outra curva passa logo
        # acima do pico (o rótulo ficaria em cima dela)
        others = [np.asarray(c[1], float)[i] for c in curves if c[0] != label]
        below = label in label_below or any(
            peak < o < peak + 0.08 * (y_hi - y_lo) for o in others)
        # rótulos de picos próximos (mesma região do gráfico) são afastados
        # na horizontal, alternando direita/esquerda, para não se sobreporem
        near = sum(1 for (px, py, pb) in placed
                   if pb == below and abs(px - x[i]) < 20 and abs(py - peak) < 0.05 * (y_hi - y_lo))
        dx = 0 if near == 0 else (110 if near % 2 else -110) * ((near + 1) // 2)
        dy = -22 if below else 18
        ax.annotate(f"{peak / 1000:,.1f}k", (x[i], peak), textcoords="offset points",
                    xytext=(dx, dy), ha="center", va="top" if below else "bottom",
                    fontsize=F_TICK, fontweight="bold", color=color, zorder=5,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
        placed.append((x[i], peak, below))
    handles.append(ax.axhline(min_conv, color="black", ls="-", lw=1.5,
                              label=f"Min Conv: {min_conv:,.2f} kW"))

    ax.set_xlabel("Time slot", fontsize=F_LABEL, labelpad=10)
    ax.set_ylabel("Power (kW)", fontsize=F_LABEL, labelpad=10)
    step = max(1, TOTAL_TIMESLOTS // 24)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(x[::step], rotation=45, ha="right", fontsize=F_TICK)
    ax.set_xlim(-1, TOTAL_TIMESLOTS + 2)
    ax.set_ylim(y_lo, y_hi)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    ax.tick_params(axis="both", labelsize=F_TICK)
    ax.yaxis.grid(color="#95a5a6", linestyle="--", linewidth=0.5, alpha=0.7, zorder=0)
    ax.yaxis.grid(which="minor", color="#d3d3d3", linestyle="--", linewidth=0.2, zorder=0)
    ax.yaxis.set_minor_locator(AutoMinorLocator())
    ncol = 2 if len(handles) <= 4 else 3
    ax.legend(handles, [h.get_label() for h in handles],
              loc="lower center", bbox_to_anchor=(0.5, 1.00), ncol=ncol,
              fontsize=F_LEGEND, framealpha=0.9, edgecolor="#cccccc",
              borderpad=0.7, handlelength=2.4, handletextpad=0.6, columnspacing=1.4)
    n_leg_rows = int(np.ceil(len(handles) / ncol))
    ax.set_title(title, fontsize=F_TITLE, pad=0,
                 y=1.07 + 0.075 * n_leg_rows + (0.08 if "\n" in title else 0))
    plt.tight_layout(rect=(0, 0, 1, 0.85))
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", pil_kwargs=WEBP_KWARGS)
    plt.close(fig)
    print(f"[salvo] {out_path}")
    return out_path


def stats(df):
    pconv_final = df['Pconv_final'].values
    return {
        'pico':             pconv_final.max(),
        'vale':             pconv_final.min(),
        'media':            pconv_final.mean(),
        'desvio_medio_abs': np.abs(pconv_final - pconv_final.mean()).mean(),
    }


def _inst(n_evs, frota, dia_csv):
    return f"inst_{n_evs}_{frota}_{Path(dia_csv).stem[3:]}"


def fig_exact(n_evs, frota, dia_csv):
    inst = _inst(n_evs, frota, dia_csv)
    d = SAIDA / 'exact_gurobi' / frota / inst
    df = pd.read_csv(d / "en_profile_final.csv")
    summary = pd.read_csv(d / "summary.csv").iloc[0]
    title = (f"Exact algorithm (Gurobi) | {frota} · {inst.split('_cgr_')[1]} · {n_evs:,} EVs"
             f"\nEnergy delivered: {summary['Pct_entregue']:.2f}% of target (90% SoC)  |  "
             f"F2: {summary['F2_desvio_media']:,.0f}  |  F3: {summary['F3_penalidade_vale']:,.0f}")
    curves = [("Initial (no EV)", df['Pconv_inicial'].values, "blue", "-"),
              ("Exact (Gurobi): conventional + EV", df['Pconv_final'].values, "black", "-")]
    return plot_load_profiles(curves, df['Pconv_inicial'].min(), title, d / "Fig_exact.webp")


def fig_compare(n_evs, frota, dia_csv):
    inst = _inst(n_evs, frota, dia_csv)
    ex = pd.read_csv(SAIDA / 'exact_gurobi' / frota / inst / "en_profile_final.csv")
    lc = pd.read_csv(SAIDA / 'lcvf_v2g' / frota / inst / "en_profile_final.csv")
    v2 = pd.read_csv(SAIDA / 'v2g_nivelado' / frota / inst / "en_profile_all_stages.csv")
    pico_ex = stats(ex)['pico']
    curves = [("Initial (no EV)", lc['Pconv_inicial'].values, "blue", "-"),
              ("LCVF", lc['Pconv_final'].values, "red", "--"),
              ("V2G leveled", v2['Pconv_apos_S3'].values, "green", "-."),
              ("Exact (Gurobi)", ex['Pconv_final'].values, "black", "-")]
    gaps = [f"{lbl}: {100 * (vals.max() - pico_ex) / pico_ex:+.2f}%" for lbl, vals, _, _ in curves[1:3]]
    title = (f"LCVF × V2G × Exact | {frota} · {inst.split('_cgr_')[1]} · {n_evs:,} EVs"
             "\nPeak vs exact optimum  |  " + "  |  ".join(gaps))
    return plot_load_profiles(curves, lc['Pconv_inicial'].min(), title,
                              SAIDA / 'exact_gurobi' / frota / inst / "Fig_compare.webp",
                              label_below=("V2G leveled",))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(f"Uso: python3 {sys.argv[0]} <N> <frota> <pc_cgr_*.csv>")
    n, frota, dia = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    fig_exact(n, frota, dia)
    fig_compare(n, frota, dia)
