#!/usr/bin/env python3
"""
CÓPIA CONGELADA de Codes/E_V2G/scripts/lcvf_fast_v2g.py (commit ef994364 + correções de 2026-10-03), usada
só pela generalização de 32 dias (run_generalizacao.py). Caminhos ajustados para esta pasta e
saídas padrão em Results/V2G/CampoGrande/generalization/. Correções no pipeline principal não
chegam aqui sozinhas: repita-as e registre na docs/13.

lcvf_fast_v2g.py — LCVF (Load-Conservation Valley Filling) applied to the
pre-processed V2G fleet CSVs (pp_*.csv).

Adapted from 'Codes/C_ValleyFilling/04 - lcvf_fast.py'. Same allocation
logic as subalgorithm1_fast / subalgorithm2_fast in that script, with one
deliberate departure from "verbatim" — see "Power vs. Energy units" below —
plus the input/output wiring differences:

  * EV data source : Datasets/ProcessedData/Vehicles/V2G/pp_*.csv
                      (flat folder, one CSV per already-instantiated
                      fleet — no Boulder/PaloAlto/Norway scenario picker).
  * Column renaming : pp_*.csv columns use the `lcvf_` prefix produced by
                      08 - v2g_preprocessing.py; they are renamed back to
                      the names lcvf_fast.py expects (Start2, End2, Power,
                      Energy, TSRemaining) before running the algorithm —
                      Start2/End2/Power/Energy/TSRemaining are then all
                      overwritten (see below), so only the rename's naming
                      is kept, not its original values.
  * Instance naming : inst_{N}_{scenario}_{city_schema}, e.g.
                      inst_18000_bld_s1_cgr_20220611_baseline — matches the
                      folders already produced by v2g_stable.py under
                      Results/V2G/CampoGrande/.
  * Output path     : Results/V2G/CampoGrande/lcvf_v2g/{scenario}/{inst_name}/
                      (uma subpasta por cenário — mesmo padrão de
                      Results/V2G/CampoGrande/{scenario}/ do v2g_stable.py e
                      de Datasets/ProcessedData/Vehicles/V2G/{scenario}/).
  * City file        : CampoGrande, season selectable via argv[1]
                      (baseline/winter/spring/summer/autumn — default
                      baseline, matching the original behavior and the
                      existing V2G result folders). argv[2:], if given,
                      overrides PP_FILE_FILTER (e.g. `pp_3000_bld_s1.csv`)
                      instead of running every pp_*.csv found. Same
                      argv-driven pattern as `Codes/Exact_Alg/gurobi_alg/
                      multiobj_c.c`, added 2026-09-22 to cover bld_s1's
                      non-baseline seasons against the exact algorithm.
  * Charging power   : every EV charges at the fixed V2G rate,
                      CHARGER_POWER_KW = 7.0 (ENERGY_PER_SLOT_KWH =
                      1.16667 kWh/TS), matching v2g_preprocessing.py and
                      v2g_stable.py — not the original evp_*.csv `Power`
                      column (mean ≈0.74 "kW"/EV, a legacy per-session rate
                      that predates the V2G build).
  * Energy target    : Energy is *not* taken from the legacy
                      `lcvf_energy_kWhTS` (original session energy) either.
                      It is derived from the same SoC/battery-capacity
                      model v2g_stable.py's Stage 3 (full-charge) targets,
                      so the two pipelines are charging every EV towards
                      the *same* end state — SOC_FULL_TARGET (90%) of
                      `battery_capacity_kWh` — starting from
                      `energy_adjusted_kWh` (SoC at arrival):
                          Energy := max(0, capacity×0.90 - energy_adjusted_kWh)
                      TSRemaining (slots needed at 7 kW to deliver that
                      Energy) is derived from it too, capped at the EV's
                      parking window (see below):
                          TSRemaining := min(ceil(Energy / ENERGY_PER_SLOT_KWH),
                                              window length)
                      This makes the LCVF-clássico total and the V2G
                      pipeline's S2+S3 total directly comparable — both are
                      now answering "how much energy to take this fleet
                      from arrival SoC to 90%?", just via a single
                      valley-filling pass instead of V2G's
                      discharge → restore-min → fill-to-90% sequence.
  * Window          : Start2/End2 are overwritten with `start_ts`/`end_ts`
                      (the EV's real parking window — the same columns
                      v2g_stable.py's build_avail() uses) instead of the
                      legacy Start2/End2 from the original evp_*.csv
                      dataset. That legacy window is a much narrower
                      "flexibility window" left over from the classic LCVF
                      notebooks (mean ~13 slots vs. ~27 for the EV's actual
                      parking duration); combined with the SoC-based Energy
                      target above it left 61% of EVs window-clipped before
                      the valley-filling search ever ran. start_ts/end_ts
                      brings that down to <1%, and matches the window V2G
                      itself charges/discharges within.
  * Power vs. Energy : The original 04 - lcvf_fast.py (and the classic
                      notebooks it's based on) uses a single `Power` value
                      both to increment pconv/phacc/psup/pdem (power, kW)
                      *and* to decrement each EV's remaining Energy (kWh)
                      by that same amount every allocated slot — correct
                      only if 1 TS = 1 hour. At 144 TS/day (10-min slots)
                      that conflates the two domains: setting Power to the
                      true charger rating (7 kW, comparable to
                      v2g_stable.py's CHARGER_POWER_KW-based pconv updates)
                      would drain a battery 6x too fast; setting it to
                      ENERGY_PER_SLOT_KWH (1.16667, correct for the battery)
                      makes every EV's contribution to the power-domain
                      chart/search 6x smaller than its real 7 kW rating —
                      which is what made the LCVF "before" figure in
                      02_galeria.html look under-populated next to V2G's
                      (true-kW) uncoordinated-demand figure.
                      Fixed here by keeping the two domains separate:
                      `Power` is the true charger rating (CHARGER_POWER_KW)
                      and is used as-is for pconv/phacc/psup/pdem; the one
                      place Energy is decremented
                      (subalgorithm1_fast's inner loop) multiplies by
                      TS_HOURS to convert kW -> kWh for that slot. This is
                      the one place this script's algorithm diverges from
                      04 - lcvf_fast.py's.
  * Busca e ordem    : (2026-10-07; também no pipeline principal) duas
                      núcleo, registradas na docs/13:
                      1. Cada tentativa de Pc parte do estado sem
                      alocações; fica a última tentativa viável (toda a
                      energia dependente de Pc alocada). Antes, a tentativa
                      inviável era mantida e as seguintes só a completavam:
                      se a 1ª tentativa (meio do intervalo) quase cabia, o
                      pico travava nela (até +30% sobre o ótimo no B3).
                      2. Dentro de cada horário, os candidatos são ordenados
                      por menor folga (horários da janela ainda não
                      visitados − horários de que o EV ainda precisa), não
                      por Start2 decrescente. Com milhares de EVs na mesma
                      janela (B3, turnos de 8 h), a ordem antiga era
                      arbitrária e deixava EVs um horário aquém.
"""

import re
import sys
import time
from pathlib import Path, PurePath

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


# ─── Project paths ──────────────────────────────────────────────────────────
_BASE     = Path(__file__).resolve().parents[2]      # …/Codes/E_V2G (cópia em scripts/generalização_32dias)
path_root = _BASE.parents[1]                          # …/power-charging-ves

EV_DIR   = path_root / 'Datasets' / 'ProcessedData' / 'Vehicles' / 'V2G'
CITY_DIR = path_root / 'Datasets' / 'ProcessedData' / 'Cities' / 'CampoGrande'

# WebP lossless: pixels idênticos ao PNG, ~60-80% menor (sem pil_kwargs o savefig
# usa WebP com perdas por padrão — nunca omitir estas opções).
# Generalização: 150 dpi e method=4 (sem perdas). Medido em 2026-10-03: ~57% menor que 360 dpi e
# ~15× mais rápido que method=6, que dá o mesmo tamanho que method=4.
DPI              = 150
WEBP_KWARGS      = {"lossless": True, "quality": 100, "method": 4}  # 4 figuras finais/instância
# method=2 dá o MESMO tamanho que method=6 em Fig_03_*_VF_exec (298.2KB nos dois,
# medido empiricamente) só que ~10x mais rápido (7s vs 70s) — usado nas 15
# figuras de iteração/instância, onde method=6 não comprime nada a mais, só
# desperdiça tempo.
WEBP_KWARGS_ITER = {"lossless": True, "quality": 100, "method": 4}

# ─── Non-interactive config ─────────────────────────────────────────────────
CITY_NAME        = 'CampoGrande'

# SEASON via argv[1] (default 'baseline', preserva o comportamento original)
# — mesmo mapeamento SEASON -> arquivo de cidade usado em multiobj_c.c e em
# 08 - v2g_preprocessing.py.
SEASON_TO_FILE = {
    'baseline': 'pc_cgr_20220611_baseline.csv',
    'winter':   'pc_cgr_20220707_winter_thu.csv',
    'spring':   'pc_cgr_20221005_spring_wed.csv',
    'summer':   'pc_cgr_20230104_summer_wed.csv',
    'autumn':   'pc_cgr_20230405_autumn_wed.csv',
}
SEASON    = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
if SEASON not in SEASON_TO_FILE:
    raise ValueError(f"SEASON deve ser um de {list(SEASON_TO_FILE)}, recebi {SEASON!r}")
CITY_FILE        = CITY_DIR / SEASON_TO_FILE[SEASON]
EV_FILE_PATTERN  = 'pp_*.csv'
# [] -> todos os pp_*.csv encontrados; senão, lista de nomes (ex. "pp_3000_bld_s2.csv").
# argv[2:], se fornecido, sobrescreve este default (ver docstring do módulo).
PP_FILE_FILTER   = sys.argv[2:] if len(sys.argv) > 2 else []
ITERATION_COUNT  = 15
DECIMAL_DIGITS   = 5
HEURISTIC_ALIAS  = 'lcvf_v2g'

# Fixed charger power — matches CHARGER_POWER_KW in v2g_preprocessing.py /
# v2g_stable.py, so both pipelines charge every EV at the same rate.
CHARGER_POWER_KW    = 7.0
TOTAL_TIMESLOTS     = 144
TS_HOURS            = round(24 / TOTAL_TIMESLOTS, 5)                    # 0.16667 h
ENERGY_PER_SLOT_KWH = round(CHARGER_POWER_KW * TS_HOURS, 5)             # 1.16667 kWh/TS

# Same charge target as v2g_stable.py's Stage 3 (full-charge), so the two
# pipelines manage the same total energy per fleet.
SOC_FULL_TARGET = 0.90

RESULTS_ROOT = path_root / 'Results' / 'V2G' / CITY_NAME / 'generalization' / HEURISTIC_ALIAS

# Rename map: pp_*.csv (lcvf_-prefixed, from v2g_preprocessing.py) -> names
# expected by the subalgorithm1_fast / subalgorithm2_fast implementation.
COLUMN_RENAME = {
    'lcvf_start_2':      'Start2',
    'lcvf_end_2':        'End2',
    'lcvf_power_kW':     'Power',
    'lcvf_energy_kWhTS': 'Energy',
    'lcvf_ts_remaining': 'TSRemaining',
}

PP_RE = re.compile(r'^pp_(\d+)_(.+)$', re.IGNORECASE)


def parse_pp_token(pp_path):
    """pp_18000_bld_s1.csv -> ('18000', 'bld_s1')"""
    m = PP_RE.match(PurePath(pp_path).stem)
    if not m:
        raise ValueError(f'Formato inesperado (pp_*.csv): {pp_path}')
    return m.group(1), m.group(2)


def extrair_power_consumption_schema(selected_city_file):
    p = PurePath(selected_city_file)
    fname = p.name
    if not (fname.lower().startswith('pc_') and p.suffix.lower() == '.csv'):
        raise ValueError(f'Formato inesperado (cidade): {fname}')
    return p.stem[3:]


def ensure_results_dir(results_path):
    p = Path(results_path)
    if p.exists():
        if p.is_dir():
            print(f'[ok] Pasta já existe: {p.resolve()}')
            return p
        raise FileExistsError(f'[erro] Já existe um arquivo com este nome: {p}')
    p.mkdir(parents=True, exist_ok=True)
    print(f'[criado] Pasta criada: {p.resolve()}')
    return p


# ─── EV demand initialisation (fast) ────────────────────────────────────────

def sum_energy_by_timeslot_fast(df, total_timeslots=144):
    starts     = np.clip(df['Start2'].values.astype(int) - 1, 0, total_timeslots)
    remainings = df['TSRemaining'].values.astype(int)
    powers     = df['Power'].values.astype(float)
    ends       = np.minimum(starts + remainings, total_timeslots)
    # o que passa da meia-noite continua na madrugada (dia periódico, até wrap_end_ts)
    wrap       = df['wrap_end_ts'].values.astype(int) if 'wrap_end_ts' in df else np.zeros(len(df), int)
    restos     = np.minimum(np.maximum(0, starts + remainings - total_timeslots), wrap)

    result = np.zeros(total_timeslots)
    for i in range(len(df)):
        s, e = starts[i], ends[i]
        if s < e:
            result[s:e] += powers[i]
        if restos[i] > 0:
            result[:restos[i]] += powers[i]
    return result


# ─── Visualisation helpers (identical to lcvf_fast.py) ──────────────────────

def grafico_VF(df_plot, Pc, pc_counter, remaining_charge, output_path,
                pct_demanda_total=None, evs_abaixo_alvo=None, n_evs_total=None):
    df_plot = df_plot.sort_values('TS').reset_index(drop=True)

    title_text = (
        f'LCVF – Interaction #{pc_counter} '
        f'| Unallocated EV loads: {remaining_charge:,.2f} kWh'
    )
    if pct_demanda_total is not None and evs_abaixo_alvo is not None and n_evs_total is not None:
        n_atendidos = n_evs_total - evs_abaixo_alvo
        title_text += (
            f'\n{pct_demanda_total:.2f}% da demanda total atendida'
            f'  |  EVs a 90% SoC: {n_atendidos:,}/{n_evs_total:,} atendidos'
        )
    categories  = list(df_plot['TS'])
    values1     = df_plot['Pconv']
    values2     = df_plot['PHacc']
    values_orig = df_plot['Pconv_backup']
    x           = np.arange(len(categories))

    fig, ax = plt.subplots(figsize=(24, 13))

    FONT_TICK   = 24
    FONT_LABEL  = 30
    FONT_TITLE  = 36
    FONT_LEGEND = 24

    ax.grid(color='#95a5a6', linestyle='--', linewidth=0.5, axis='y', alpha=0.7, zorder=0)
    ax.yaxis.grid(which='minor', color='#d3d3d3', linestyle='--', linewidth=0.2, zorder=0)
    ax.yaxis.set_minor_locator(plt.MultipleLocator(1000))

    y_ticks_interval = 6000
    y_ticks = np.arange(
        min(values_orig) * 0.9,
        max(values1 + values2) * 1.1,
        y_ticks_interval,
    )
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([f'{int(y/1000)}k' for y in y_ticks], fontsize=FONT_TICK)

    bars_prev = ax.bar(x, values1, color='green', edgecolor='white', linewidth=0.3,
                       hatch='\\\\', label='Previous EV Charging Allocation')
    bars_curr = ax.bar(x, values2, bottom=values1, color='red', edgecolor='white',
                       linewidth=0.3, hatch='xx', label='Current EV Charging Allocation')
    bars_conv = ax.bar(x, values_orig, color='blue', edgecolor='white', linewidth=0.3,
                       hatch='', label='Power Consumption Conventional')

    correcaoLinhaAzul = max(values_orig)
    minConv           = min(values_orig)

    line_max = ax.axhline(correcaoLinhaAzul, color='blue', linestyle='--', linewidth=1.5,
                          label=f'Max Conv: {correcaoLinhaAzul:,.2f} kW')
    line_min = ax.axhline(minConv, color='black', linestyle='solid', linewidth=1.5,
                          label=f'Min Conv: {minConv:,.2f} kW')
    line_pc  = ax.axhline(Pc, color='green', linestyle='solid', linewidth=2.0,
                          label=f'Pc: {Pc:,.2f} kW')

    step = max(1, len(categories) // 24)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(
        [categories[i] for i in range(0, len(categories), step)],
        rotation=45, ha='right', fontsize=FONT_TICK,
    )
    ax.set_xlabel('Time slot', fontsize=FONT_LABEL, labelpad=10)
    ax.set_ylabel('Power (kW)', fontsize=FONT_LABEL, labelpad=10)
    ax.tick_params(axis='both', labelsize=FONT_TICK)

    legend_handles = [line_max, bars_conv, line_min, bars_prev, line_pc, bars_curr]
    ax.legend(legend_handles, [h.get_label() for h in legend_handles],
              loc='lower center', bbox_to_anchor=(0.5, 1.00), ncol=3,
              fontsize=FONT_LEGEND, framealpha=0.9, edgecolor='#cccccc',
              borderpad=0.7, handlelength=1.8, handletextpad=0.6, columnspacing=1.4)

    _title_y = 1.32 if '\n' in title_text else 1.25  # linha extra de métricas precisa de mais espaço acima da legenda
    ax.set_title(title_text, fontsize=FONT_TITLE, y=_title_y, pad=0)
    plt.xlim(-2, len(categories) + 1)
    plt.ylim(min(values_orig) * 0.9, max(values1 + values2) * 1.1)
    plt.tight_layout(rect=(0, 0, 1, 0.85))

    base_path = output_path / f'Fig_03_{pc_counter}_VF_exec'
    p = base_path.with_suffix('.webp')
    plt.savefig(p, format='webp', dpi=DPI, bbox_inches='tight', pil_kwargs=WEBP_KWARGS_ITER)
    print(f'[salvo] {p.resolve()}')

    plt.close()


# ─── Optimised subalgorithm 1 (identical to lcvf_fast.py) ───────────────────

def subalgorithm1_fast(df_en_in, df_ev_in, Pc, Pc_counter, tabela_resultados,
                       results_path, decimal_digits, window_mask, allocated_in,
                       target_total=None, manter=False):
    start_time = time.time()
    T = len(df_en_in)
    N = len(df_ev_in)
    TOLERANCE = 1e-2

    ts_arr     = df_en_in['TS'].values.astype(int)
    pconv      = df_en_in['Pconv'].values.copy()
    pconv_bkp  = df_en_in['Pconv_backup'].values.copy()
    phacc2_arr = df_en_in['PHacc2'].values.copy()

    ev_start2  = df_ev_in['Start2'].values.astype(int)
    ev_power   = df_ev_in['Power'].values.copy()
    ev_energy  = df_ev_in['Energy'].values.copy()
    ev_ts_rem  = df_ev_in['TSRemaining'].values.astype(int).copy()

    pconv_state_bkp   = pconv.copy()
    ev_energy_bkp     = ev_energy.copy()
    ev_ts_rem_bkp     = ev_ts_rem.copy()

    allocated = allocated_in.copy()

    phacc     = np.zeros(T)
    remaining = np.ones(T, dtype=bool)

    active = ev_energy > TOLERANCE
    pdem   = window_mask @ (ev_power * active)
    psup   = (Pc - pconv).round(decimal_digits)

    print(f'Initial value of Pc: {Pc}')

    n_remaining = T
    while n_remaining > 0:
        with np.errstate(divide='ignore', invalid='ignore'):
            index_ts = np.where(
                remaining & (pdem > 0),
                psup / pdem,
                -np.inf,
            )
        H = int(np.argmax(index_ts))

        cand_mask = (
            window_mask[H, :] &
            (ev_energy > TOLERANCE) &
            (ev_ts_rem > 0) &
            ~allocated[H, :]
        )
        cand_idx = np.where(cand_mask)[0]

        if cand_idx.size > 0:
            # Menor folga primeiro: horários da janela ainda não visitados
            # (inclui H) menos os horários de que o EV ainda precisa.
            folga    = window_mask[remaining][:, cand_idx].sum(axis=0) - ev_ts_rem[cand_idx]
            cand_idx = cand_idx[np.argsort(folga, kind='stable')]
            psup_h   = psup[H]

            # Vetorizado: mesma semântica do loop Python original ("aloca o
            # próximo candidato, na ordem de cand_idx, enquanto psup_h - power
            # > 0 após a alocação"), mas resolvido em O(k) numpy em vez de um
            # loop Python por candidato. Como a alocação é sequencial e cada
            # candidato só reduz psup_h (nunca aumenta), o corte é o maior k
            # tal que soma-acumulada-das-k-primeiras-potências < psup_h —
            # exatamente o que np.searchsorted acha em cumsum, sem laço.
            # Válido mesmo se ev_power variar por EV (não assume valor único).
            powers_sorted = ev_power[cand_idx]
            cumsum        = np.cumsum(powers_sorted)
            k = int(np.searchsorted(cumsum, psup_h, side='left'))

            if k > 0:
                alloc_idx     = cand_idx[:k]
                alloc_powers  = powers_sorted[:k]

                allocated[H, alloc_idx] = True
                phacc[H] += cumsum[k - 1]
                psup_h    = psup_h - cumsum[k - 1]

                # ev_power is power (kW); the battery's remaining Energy
                # (kWh) only drains by power x TS_HOURS in one timeslot —
                # keeps the power and energy domains dimensionally separate
                # (see module docstring).
                ev_energy[alloc_idx] -= alloc_powers * TS_HOURS
                ev_ts_rem[alloc_idx] -= 1

                drained = ev_energy[alloc_idx] <= TOLERANCE
                if drained.any():
                    drained_idx    = alloc_idx[drained]
                    drained_powers = alloc_powers[drained]
                    ev_energy[drained_idx] = 0.0
                    pdem -= (window_mask[:, drained_idx] * drained_powers).sum(axis=1)

            psup[H] = psup_h

        remaining[H] = False
        n_remaining  -= 1

    ev_energy[ev_energy < TOLERANCE] = 0.0
    sum_energy = float(ev_energy.sum())

    # EVs que esgotaram a janela (ev_ts_rem<=0) mas ainda têm energia residual:
    # nenhum Pc, por maior que seja, entrega essa parcela — é um limite de
    # tempo (janela de estacionamento), não de potência. Separar essa parcela
    # do total evita que ela poluia o critério de convergência (Flag) abaixo,
    # que faria Pc crescer sem necessidade buscando algo que Pc não resolve.
    # UnallocatedLoad (reportado) continua sendo o total real, sem esconder
    # nada — só o Flag/EVs_abaixo_alvo passam a distinguir as duas causas.
    stuck_mask         = (ev_ts_rem <= 0) & (ev_energy > TOLERANCE)
    structural_energy  = float(ev_energy[stuck_mask].sum())
    pc_dependent_energy = sum_energy - structural_energy
    evs_abaixo_alvo     = int((ev_energy > TOLERANCE).sum())
    n_evs_total          = len(ev_energy)
    pct_demanda_total    = (100.0 * (1.0 - sum_energy / target_total)) if target_total else None

    df_plot = pd.DataFrame({
        'TS':           ts_arr,
        'Pconv':        pconv,
        'PHacc':        phacc,
        'Pconv_backup': pconv_bkp,
    })
    grafico_VF(df_plot, Pc, Pc_counter, sum_energy, output_path=results_path,
               pct_demanda_total=pct_demanda_total,
               evs_abaixo_alvo=evs_abaixo_alvo, n_evs_total=n_evs_total)

    # Tentativa viável (Flag=1) é mantida como candidata a solução; inviável
    # (Flag=0) é descartada. subalgorithm2_fast chama cada tentativa a partir
    # do estado sem alocações, então nada se acumula entre tentativas.
    Flag = 1 if abs(pc_dependent_energy) < TOLERANCE else 0
    if Flag == 1 or manter:
        phacc2_arr  += phacc
        pconv       += phacc
        allocated_out = allocated
    else:
        pconv        = pconv_state_bkp
        ev_energy    = ev_energy_bkp
        ev_ts_rem    = ev_ts_rem_bkp
        allocated_out = allocated_in

    exec_time = round(time.time() - start_time, 2)
    print(f'Lines 01 to 50: {exec_time} seconds to execute')
    print(f'SUM_ENERGY: {sum_energy}  (estrutural: {structural_energy:.2f}  |  dependente de Pc: {pc_dependent_energy:.2f})')

    tabela_resultados.at[Pc_counter - 1, 'ExecTime']                    = exec_time
    tabela_resultados.at[Pc_counter - 1, 'Pc']                          = round(Pc, 2)
    tabela_resultados.at[Pc_counter - 1, 'UnallocatedLoad']             = round(sum_energy, 2)
    tabela_resultados.at[Pc_counter - 1, 'UnallocatedLoad_estrutural']  = round(structural_energy, 2)
    tabela_resultados.at[Pc_counter - 1, 'EVs_abaixo_90pct']            = evs_abaixo_alvo
    tabela_resultados.at[Pc_counter - 1, 'Flag']                        = Flag
    # 'EVs_abaixo_90pct_estado' é preenchida por subalgorithm2_fast com o estado
    # da melhor solução viável até esta iteração (a última linha é a final).

    df_en_out = df_en_in.copy()
    df_en_out['Pconv']        = pconv
    df_en_out['PHacc']        = phacc
    df_en_out['PHacc2']       = phacc2_arr
    df_en_out['Pconv_backup'] = pconv_bkp

    df_ev_out = df_ev_in.copy()
    df_ev_out['Energy']      = ev_energy
    df_ev_out['TSRemaining'] = ev_ts_rem

    return Flag, df_en_out, df_ev_out, tabela_resultados, allocated_out


# ─── Optimised subalgorithm 2 (identical to lcvf_fast.py) ───────────────────

def subalgorithm2_fast(df_en, df_ev, Upper_Pc, Lower_Pc, qtd_while,
                       tabela_resultados, results_path, decimal_digits):
    start_time = time.time()
    print(f'\nSub-algorithm 2: Searching for minimum solution of criterion Pc:')
    print(f'Upper_Pc: {Upper_Pc}')
    print(f'Lower_Pc: {Lower_Pc}')

    T = len(df_en)
    N = len(df_ev)

    ts_arr      = df_en['TS'].values.astype(int)
    ev_start2   = df_ev['Start2'].values.astype(int)
    ev_end2     = df_ev['End2'].values.astype(int)
    window_mask = (
        (ev_start2[np.newaxis, :] <= ts_arr[:, np.newaxis]) &
        (ts_arr[:, np.newaxis]    <= ev_end2[np.newaxis, :])
    )
    # dia periódico: sessão que passa da meia-noite continua em [1, wrap_end_ts]
    if 'wrap_end_ts' in df_ev:
        window_mask |= ts_arr[:, np.newaxis] <= df_ev['wrap_end_ts'].values.astype(int)[np.newaxis, :]

    allocated = np.zeros((T, N), dtype=bool)

    # Piso estrutural FIXO: calculado uma única vez a partir da Energy/TSRemaining
    # de entrada (antes de qualquer alocação), não recalculado a cada trial de Pc
    # como a versão "dinâmica" usada no critério de Flag (subalgorithm1_fast).
    # Isso evita a ambiguidade de iterações iniciais (Pc baixo) onde a versão
    # dinâmica ainda não "revelou" o piso completo — o piso estático já é
    # conhecido e constante desde a entrada, dando uma métrica de progresso
    # limpa e monotônica em direção a 100%.
    _orig_ts_rem = df_ev['TSRemaining'].values.astype(int)
    _orig_energy = df_ev['Energy'].values.astype(float)
    _orig_power  = df_ev['Power'].values.astype(float)
    max_deliverable        = _orig_ts_rem * _orig_power * TS_HOURS
    structural_floor_total = float(np.maximum(0.0, _orig_energy - max_deliverable).sum())
    target_total            = float(_orig_energy.sum())
    achievable_total         = max(target_total - structural_floor_total, 1e-9)
    print(f'[piso] energia-alvo total: {target_total:.2f} kWh  |  '
          f'piso estrutural (nenhum Pc resolve): {structural_floor_total:.2f} kWh  |  '
          f'alocável (Pc-dependente): {achievable_total:.2f} kWh')

    # Cada tentativa parte do estado sem alocações (df_en0/df_ev0); a solução é
    # a última tentativa viável. Se nenhuma for viável, roda no teto inicial.
    df_en0, df_ev0 = df_en, df_ev
    best = None
    Pc_counter = 1
    for j in range(qtd_while):
        print(f'CALCULATION OF {Pc_counter}th PC...')
        Pc = (Upper_Pc + Lower_Pc) / 2

        Flag, df_en_t, df_ev_t, tabela_resultados, _ = subalgorithm1_fast(
            df_en0, df_ev0, Pc, Pc_counter, tabela_resultados,
            results_path, decimal_digits, window_mask, allocated,
            target_total=target_total,
        )
        if Flag == 1:
            best = (df_en_t, df_ev_t)
        estado = (best[1] if best else df_ev0)['Energy'].values
        tabela_resultados.at[Pc_counter - 1, 'EVs_abaixo_90pct_estado'] = int((estado > 1e-2).sum())

        # Métricas de progresso "justas": descontam o piso estrutural fixo do
        # não-alocado total, e expressam o restante como % do que É alcançável.
        unalloc            = tabela_resultados.at[Pc_counter - 1, 'UnallocatedLoad']
        pc_dependent_norm  = max(0.0, unalloc - structural_floor_total)
        pct_alocavel       = 100.0 * (1.0 - pc_dependent_norm / achievable_total)
        # Visão complementar: % sobre a demanda TOTAL (sem descontar o piso do
        # denominador) — mais honesta sobre o impacto real do piso na frota,
        # ao custo de nunca bater 100% quando existe piso estrutural > 0
        # (teto = 100% - structural_floor_total/target_total, fixo por instância).
        pct_demanda_total  = 100.0 * (1.0 - unalloc / target_total)
        tabela_resultados.at[Pc_counter - 1, 'UnallocatedLoad_dependente_de_Pc'] = round(pc_dependent_norm, 2)
        tabela_resultados.at[Pc_counter - 1, 'Pct_alocavel_entregue']           = round(pct_alocavel, 2)
        tabela_resultados.at[Pc_counter - 1, 'Pct_demanda_total_atendida']      = round(pct_demanda_total, 2)

        print('End of Subalgorithm 1')
        print(f'--- {time.time() - start_time:.2f} seconds ---')

        if Flag == 1:
            Upper_Pc = Pc
        else:
            Lower_Pc = Pc

        print(f'Upper_Pc: {Upper_Pc}')
        Pc_counter += 1

    if best is None:
        print(f'Nenhuma tentativa viável: rodando no teto inicial {Upper_Pc}')
        extra = pd.DataFrame({'Iteration': [Pc_counter]})
        tabela_resultados = pd.concat([tabela_resultados, extra], ignore_index=True)
        _f, df_en_t, df_ev_t, tabela_resultados, _ = subalgorithm1_fast(
            df_en0, df_ev0, Upper_Pc, Pc_counter, tabela_resultados,
            results_path, decimal_digits, window_mask, allocated,
            target_total=target_total, manter=True,
        )
        if _f == 0:   # nem o teto inicial aloca tudo: mantém a tentativa mesmo assim
            print('AVISO: energia dependente de Pc não alocada nem no teto inicial')
        best = (df_en_t, df_ev_t)
        tabela_resultados.at[Pc_counter - 1, 'EVs_abaixo_90pct_estado'] = int((df_ev_t['Energy'].values > 1e-2).sum())
    df_en, df_ev = best

    file_path = f'{results_path}.csv'
    tabela_resultados.to_csv(file_path, index=False)
    print(f'DataFrame salvo em {file_path}.')

    # Per-timeslot energy allocated (kWh/TS), cumulative across all Flag=0
    # binary-search iterations — PHacc2 — for cross-pipeline comparison
    # against v2g_stable.py's en_profile_all_stages.csv. PHacc2 is power
    # (kW, same domain as Pconv); multiply by TS_HOURS to get the energy
    # (kWh) actually delivered during each 10-min slot.
    profile_cols = ['TS', 'Pconv_backup', 'Pconv', 'PHacc2']
    if 'Time' in df_en.columns:
        profile_cols.insert(1, 'Time')
    profile = df_en[profile_cols].rename(columns={
        'Pconv_backup': 'Pconv_inicial',
        'Pconv':        'Pconv_final',
        'PHacc2':       'Energia_alocada_kWh',
    })
    profile['Energia_alocada_kWh'] = (profile['Energia_alocada_kWh'] * TS_HOURS).round(decimal_digits)
    profile_path = results_path / 'en_profile_final.csv'
    profile.to_csv(profile_path, index=False)
    print(f'[salvo] {profile_path.resolve()}')


# ─── Main execution ──────────────────────────────────────────────────────────

def main():
    decimal_digits = DECIMAL_DIGITS

    if not CITY_FILE.exists():
        raise FileNotFoundError(f'Arquivo de cidade não encontrado: {CITY_FILE}')

    pp_files = sorted(EV_DIR.rglob(EV_FILE_PATTERN))
    if PP_FILE_FILTER:
        pp_files = [f for f in pp_files if f.name in PP_FILE_FILTER]
    if not pp_files:
        raise FileNotFoundError(f"Nenhum arquivo '{EV_FILE_PATTERN}' em: {EV_DIR}")

    city_schema = extrair_power_consumption_schema(CITY_FILE)

    for pp_path in pp_files:
        print(f'\n{"="*60}')
        print(f'Processando: {pp_path.name}')

        n_evs, scenario_token = parse_pp_token(pp_path)
        inst_name = f'inst_{n_evs}_{scenario_token}_{city_schema}'
        print(inst_name)

        data_ev = pd.read_csv(pp_path).rename(columns=COLUMN_RENAME)
        data_en = pd.read_csv(CITY_FILE).round(decimal_digits)

        missing = [c for c in ('Start2', 'End2', 'Power', 'Energy', 'TSRemaining',
                               'battery_capacity_kWh', 'energy_adjusted_kWh',
                               'start_ts', 'end_ts')
                   if c not in data_ev.columns]
        if missing:
            raise ValueError(f'Colunas ausentes após rename em {pp_path.name}: {missing}')

        # Window: use the EV's real parking window (start_ts/end_ts — the
        # same columns v2g_stable.py's build_avail() uses), not the legacy
        # Start2/End2 from the original evp_*.csv dataset. Start2/End2 is a
        # much narrower "flexibility window" left over from the classic
        # LCVF notebooks (mean ~13 slots vs. ~27 for the EV's actual
        # parking duration) and, combined with the SoC-based Energy target
        # below, caused 61% of EVs to be window-clipped before an EV ever
        # got a fair shot at the day's valleys — start_ts/end_ts brings
        # that down to <1%.
        data_ev['Start2'] = data_ev['start_ts']
        data_ev['End2']   = data_ev['end_ts']

        # Fixed V2G charger rate (CHARGER_POWER_KW = 7.0) and a charge
        # target matching v2g_stable.py's Stage 3 (arrival SoC -> 90% of
        # battery_capacity_kWh), instead of the legacy per-session
        # Power/Energy/TSRemaining from the original evp_*.csv dataset.
        # TSRemaining is capped at the EV's own parking window so it never
        # asks the algorithm to deliver more energy than the window allows.
        # Dia periódico (2026-10-08): sessões que passam da meia-noite continuam nos
        # primeiros slots do dia, [1, wrap_end_ts] (mesma janela de v2g_stable.build_avail
        # e de multiobj_c.c). pp_*.csv sem a coluna não têm essas sessões (wrap = 0).
        if 'wrap_end_ts' not in data_ev:
            data_ev['wrap_end_ts'] = 0
        data_ev['wrap_end_ts'] = data_ev['wrap_end_ts'].fillna(0).astype(int)
        window_len = ((data_ev['End2'] - np.maximum(data_ev['Start2'], 1) + 1).clip(lower=0)
                      + data_ev['wrap_end_ts'])
        energy_target = np.maximum(
            0.0,
            data_ev['battery_capacity_kWh'] * SOC_FULL_TARGET - data_ev['energy_adjusted_kWh'],
        )
        ts_needed = np.ceil(energy_target / ENERGY_PER_SLOT_KWH)

        # Power is the true charger rating (kW) — this is what gets summed
        # into pconv/phacc/psup/pdem (all power-domain, kW). Energy (kWh)
        # is tracked separately in subalgorithm1_fast, decremented by
        # Power * TS_HOURS each allocated slot — see module docstring.
        data_ev['Power']       = CHARGER_POWER_KW
        data_ev['Energy']      = energy_target.round(decimal_digits)
        data_ev['TSRemaining'] = np.minimum(ts_needed, window_len).astype(int)

        results_dir  = RESULTS_ROOT / scenario_token / inst_name
        results_path = ensure_results_dir(results_dir)

        # ── Update PEv and Pmax_Tuc ───────────────────────────────────────
        timeslot_sums       = sum_energy_by_timeslot_fast(data_ev)
        data_en['PEv']      = timeslot_sums
        data_en['Pmax_Tuc'] = data_en['Pconv'] + data_en['PEv']
        data_en             = data_en.round(decimal_digits)

        # ── Figure 01b ───────────────────────────────────────────────────
        time_slots               = list(data_en['TS'])
        conventional_consumption = data_en['Pconv']
        vehicles_consumption     = data_en['PEv']

        fig, ax = plt.subplots(figsize=(20, 8))
        ax.grid(color='#95a5a6', linestyle='--', linewidth=0.5, axis='y', alpha=0.7)
        ax.bar(time_slots, conventional_consumption, color='#00f', label='Conventional Load')

        maxConv  = max(conventional_consumption)
        maxTotal = max(conventional_consumption + vehicles_consumption)
        minConv  = min(conventional_consumption)

        ax.axhline(maxConv, color='blue', linestyle='--', label='Max Conv')
        ax.axhline(minConv, color='black', linestyle='solid', label='Min Conv')

        min_index = np.argmin(conventional_consumption)
        max_index = np.argmax(conventional_consumption)
        ax.annotate('Valley', xy=(min_index + 1, minConv),
                    xytext=(min_index - 5, minConv * 1.1),
                    arrowprops=dict(facecolor='red', arrowstyle='->', linewidth=2, edgecolor='red'),
                    fontsize=24, color='red')
        ax.annotate('Peak', xy=(max_index + 1, maxConv),
                    xytext=(max_index + 1, maxConv * 1.1),
                    arrowprops=dict(facecolor='red', arrowstyle='->', linewidth=2, edgecolor='red'),
                    fontsize=24, color='red')

        ax.set_xticks(np.arange(1, len(time_slots) + 1, 5))
        ax.legend(fontsize=16, loc='upper left', bbox_to_anchor=(0.05, 0.95))
        ax.set_xlabel('Time of Day (Time Slots)', fontsize=24)
        ax.set_ylabel('Power (kW)', fontsize=24)
        ax.set_title('Original Data: Conventional + Electric Vehicles Consumptions', fontsize=28)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: '{:.0f}k'.format(x / 1000)))
        ax.tick_params(axis='y', labelsize=18)
        ax.tick_params(axis='x', labelsize=18)
        plt.xlim(0, len(time_slots) + 1)
        plt.ylim(minConv * 0.9, maxTotal * 1.1)
        fig_path = results_path / 'Fig_01b_EN_All_EVs.webp'
        plt.savefig(fig_path, dpi=DPI, bbox_inches='tight', pil_kwargs=WEBP_KWARGS)
        print(f'[salvo] {fig_path.resolve()}')
        plt.close()

        # ── Figure 01 ────────────────────────────────────────────────────
        time_slots_time = list(data_en['Time'])
        y_pos = np.arange(len(data_en['Time']))

        fig, ax = plt.subplots(figsize=(20, 8))
        ax.grid(color='#95a5a6', linestyle='--', linewidth=0.5, axis='y', alpha=0.7)
        ax.bar(time_slots_time, conventional_consumption, color='#00f', label='Conventional Load')
        ax.bar(time_slots_time, vehicles_consumption, bottom=conventional_consumption,
               color='#FF0000', label='EVs Uncoordinated Charging Loads')

        ax.axhline(maxConv,  color='blue',  linestyle='--',   label=f'Max Conv: {maxConv:.3f} kW')
        ax.axhline(maxTotal, color='red',   linestyle='--',   label=f'Max Total: {maxTotal:.3f} kW')
        ax.axhline(minConv,  color='black', linestyle='solid', label=f'Min Conv: {minConv:.3f} kW')

        ax.set_xticks(y_pos)
        ax.set_xticklabels(time_slots_time, rotation=90)
        ax.legend()
        ax.set_xlabel('Time of Day (Time Slots)')
        ax.set_ylabel('Power (kW)')
        ax.legend(loc='upper left', bbox_to_anchor=(0, 0.85))
        ax.set_title('Original Data: Conventional + Electric Vehicles Consumptions', fontsize=16)
        plt.xlim(-1.0, len(time_slots_time))
        plt.ylim(minConv * 0.9, maxTotal * 1.1)
        fig_path = results_path / 'Fig_01_EN_All_EVs.webp'
        plt.savefig(fig_path, dpi=DPI, bbox_inches='tight', pil_kwargs=WEBP_KWARGS)
        print(f'[salvo] {fig_path.resolve()}')
        plt.close()

        # ── Figure inst_name ─────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(20, 8))
        ax.grid(color='#95a5a6', linestyle='--', linewidth=0.5, axis='y', alpha=0.7)
        ax.bar(time_slots, conventional_consumption, color='#00f', label='Conventional Load')
        ax.bar(time_slots, vehicles_consumption, bottom=conventional_consumption,
               color='#FF0000', label='EVs Uncoordinated Charging Loads')
        ax.axhline(maxConv,  color='blue',  linestyle='--')
        ax.axhline(maxTotal, color='red',   linestyle='--')
        ax.axhline(minConv,  color='black', linestyle='solid')
        ax.set_xticks(np.arange(1, len(time_slots) + 1, 5))
        ax.legend(fontsize=16, loc='upper left', bbox_to_anchor=(0.0, 0.85))
        ax.set_xlabel('Time of Day (Time Slots)', fontsize=24)
        ax.set_ylabel('Power (kW)', fontsize=24)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: '{:.0f}k'.format(x / 1000)))
        ax.tick_params(axis='y', labelsize=20)
        ax.tick_params(axis='x', labelsize=20)
        ax.legend(loc='upper left', bbox_to_anchor=(0, 0.815), fontsize=20)
        plt.xlim(0, len(time_slots) + 2)
        plt.ylim(minConv * 0.9, maxTotal * 1.1)
        plt.tight_layout()
        fig_path = results_path / f'{inst_name}.webp'
        plt.savefig(fig_path, dpi=DPI, bbox_inches='tight', pil_kwargs=WEBP_KWARGS)
        print(f'[salvo] {fig_path.resolve()}')
        plt.close()

        # ── Figure 02 — before VF ────────────────────────────────────────
        upper_pc = max(data_en['Pmax_Tuc'])
        lower_pc = min(data_en['Pconv'])
        pc_init  = (upper_pc + lower_pc) / 2

        fig, ax = plt.subplots(figsize=(20, 8))
        ax.grid(color='#95a5a6', linestyle='--', linewidth=0.5, axis='y', alpha=0.7)
        ax.bar(time_slots, conventional_consumption, color='#00f', label='Conventional Load')
        ax.bar(time_slots, vehicles_consumption, bottom=conventional_consumption,
               color='#FF0000', label='EVs Uncoordinated Charging Loads')
        ax.axhline(maxConv,  color='blue',  linestyle='--',   label=f'Max Conv: {maxConv:.3f} kW')
        ax.axhline(maxTotal, color='red',   linestyle='--',   label=f'Max Total: {maxTotal:.3f} kW')
        ax.axhline(minConv,  color='black', linestyle='solid', label=f'Min Conv: {minConv:.3f} kW')
        ax.axhline(pc_init,  color='green', linestyle='solid', label=f'Pc: {pc_init:.3f} kW')

        y_pos2 = np.arange(1, len(data_en['TS']) + 1)
        ax.set_xticks(y_pos2)
        ax.set_xticklabels(time_slots, rotation=90)
        ax.legend()
        ax.set_xlabel('Time of Day (Time Slots)')
        ax.set_ylabel('Power (kW)')
        ax.legend(loc='upper left', bbox_to_anchor=(0, 0.85))
        ax.set_title('Before Valley-Filling Execution', fontsize=16)
        plt.xlim(0, len(time_slots) + 2)
        plt.ylim(minConv * 0.9, maxTotal * 1.1)
        plt.tight_layout()
        fig_path = results_path / 'Fig_02_EN_All_EVs.webp'
        plt.savefig(fig_path, dpi=DPI, pil_kwargs=WEBP_KWARGS)
        print(f'[salvo] {fig_path.resolve()}')
        plt.close()

        # ── Prepare df_ev / df_en for VF ────────────────────────────────
        data_ev = data_ev.sort_values(by=['Start2', 'End2']).reset_index(drop=True)
        data_ev['Intervalos_Usados'] = [[] for _ in range(len(data_ev))]

        df_ev = data_ev.copy()
        df_en = data_en.copy()
        df_en['Pconv_backup'] = df_en['Pconv']
        df_en['PHacc2']       = 0.0
        df_en['PHacc']        = 0.0

        Upper_Pc = max(df_en['Pmax_Tuc'])
        Lower_Pc = min(df_en['Pconv'])

        tabela_resultados                    = pd.DataFrame()
        tabela_resultados['Iteration']                        = range(1, ITERATION_COUNT + 1)
        tabela_resultados['ExecTime']                         = np.nan
        tabela_resultados['Pc']                               = np.nan
        tabela_resultados['UnallocatedLoad']                   = np.nan
        tabela_resultados['UnallocatedLoad_estrutural']        = np.nan  # (dinâmico, por trial) parte do UnallocatedLoad que nenhum Pc resolve nesta iteração — usado no critério de Flag
        tabela_resultados['EVs_abaixo_90pct']                  = np.nan  # EVs que não atingiram o alvo de 90% SoC nesta TENTATIVA (desfeita se Flag=1)
        tabela_resultados['Flag']                              = np.nan  # 1 = tentativa desfeita (teto generoso demais); 0 = mantida
        tabela_resultados['EVs_abaixo_90pct_estado']           = np.nan  # EVs abaixo de 90% no estado mantido após a iteração (última linha = final)
        tabela_resultados['UnallocatedLoad_dependente_de_Pc']  = np.nan  # (piso ESTÁTICO fixo) energia ainda resolvível aumentando Pc — converge a 0
        tabela_resultados['Pct_alocavel_entregue']             = np.nan  # % da energia fisicamente alocável (exclui o piso estrutural fixo) já entregue — bate 100% quando o algoritmo termina
        tabela_resultados['Pct_demanda_total_atendida']        = np.nan  # % da demanda TOTAL (inclui o piso estrutural) — teto < 100% se houver piso, mostra o impacto real na frota

        # ── Run optimised VF ─────────────────────────────────────────────
        subalgorithm2_fast(
            df_en, df_ev, Upper_Pc, Lower_Pc, ITERATION_COUNT,
            tabela_resultados, results_path, decimal_digits,
        )


if __name__ == '__main__':
    main()
