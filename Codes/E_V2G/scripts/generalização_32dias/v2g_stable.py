#!/usr/bin/env python3
"""
CÓPIA CONGELADA de Codes/E_V2G/scripts/v2g_stable.py (commit ef994364 + correções de 2026-10-03), usada
só pela generalização de 32 dias (run_generalizacao.py). Caminhos ajustados para esta pasta e
saídas padrão em Results/V2G/CampoGrande/generalization/. Correções no pipeline principal não
chegam aqui sozinhas: repita-as e registre na docs/13.

v2g_stable.py — V2G 3-Stage Algorithm (stable version)

Reads pre-processed EV fleet CSV files (pp_*.csv) produced by v2g_preprocessing.py
and runs the 3-stage V2G optimisation.

Algorithm stages
----------------
  S1  Peak-shaving via V2G discharge (binary search over discharge floor Pf)
  S2  Minimum-charge guarantee — restore all EVs to soc_min_pct  (LCVF, binary search over Pc)
  S3  Full-charge maximisation — fill batteries up to capacity    (LCVF, binary search over Pc)

Configuration
-------------
EV_PREPROCESSED_DIR : folder with pp_*.csv files (output of v2g_preprocessing.py)
CITY_DIR_CHOICES    : list of city dir indices to iterate ([] → uses CITY_DIR_CHOICE)
CITY_DIR_CHOICE     : 1=CampoGrande | 2=Tetuan
CITYFILE_CHOICE     : 1..N | 0=menu  (used only if RUN_ALL_CITY_FILES=False)
RUN_ALL_CITY_FILES  : True → process every CSV in the city folder
SAVE_ITER_FIGS      : True → save one figure per binary-search iteration (slow for 50k EVs)
"""

import re
import time
from pathlib import Path, PurePath

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import AutoMinorLocator


# ─── Project paths ────────────────────────────────────────────────────────────
_BASE     = Path(__file__).resolve().parents[2]   # …/Codes/E_V2G (cópia em scripts/generalização_32dias)
path_root = _BASE.parents[1]           # …/power-charging-ves

BASE_DIR = path_root / "Datasets" / "ProcessedData" / "Cities"


# ─── Config ───────────────────────────────────────────────────────────────────
# INTERACTIVE_MENU = True  → exibe menu de seleção antes de rodar (uso manual)
# INTERACTIVE_MENU = False → usa as variáveis abaixo diretamente (uso automatizado)
INTERACTIVE_MENU    = False

EV_PREPROCESSED_DIR = path_root / "Datasets" / "ProcessedData" / "Vehicles" / "V2G"

CITY_DIR_CHOICES    = [1]        # lista de dirs a iterar ([] → usa CITY_DIR_CHOICE) — só CampoGrande (Tetuan só se pedido explicitamente)
CITY_DIR_CHOICE     = 1          # 1=CampoGrande | 2=Tetuan
CITYFILE_CHOICE     = 1          # 1..N | 0=menu  (used when RUN_ALL_CITY_FILES=False)
RUN_ALL_CITY_FILES  = False      # False → apenas CITYFILE_CHOICE (baseline)
PP_FILE_FILTER      = (
    [f"pp_{n}_bld_s{s}.csv" for s in (2, 3, 4, 5, 6) for n in (3000, 9000, 18000, 39000, 50336)]
    + [f"pp_{n}_nor_s1.csv" for n in (3000, 9000, 18000)]
)   # Boulder s2-s6 (5 tamanhos cada) + Norway sub-amostrado (pp_32330 já rodado) — ver docs/04
SAVE_ITER_FIGS      = True
S3_FLAT_FILL        = True    # S3: alocação proporcional à disponibilidade (elimina dips)
SOC_FULL_TARGET     = 0.90    # SoC alvo do S3 (1.0 = 100%, 0.9 = 90%)
FILE_PATTERN        = "*.csv"
# Frota para a qual Pconv é escalado de modo que EV_peak = EV_PCONV_SHARE × Pconv_peak.
# "" desabilita. Afeta apenas a frota indicada (pelo stem do pp_file).
EV_PCONV_SCALE_PP   = "pp_100_01_data_ev_reduce"
EV_PCONV_SHARE      = 0.20
CITY_FILE_PATTERNS  = {}       # padrão por cidade (sobrescreve FILE_PATTERN)


# ─── Algorithm parameters ─────────────────────────────────────────────────────
TOTAL_TIMESLOTS      = 144
TS_HOURS             = round(24 / TOTAL_TIMESLOTS, ndigits=5)   # 0.16667 h
CHARGER_POWER_KW     = 7.0
ENERGY_PER_SLOT_KWH  = round(CHARGER_POWER_KW * TS_HOURS, ndigits=5)   # 1.16667 kWh
COINCIDENCE_FACTOR   = 0.6   # Fix C: fraction of plugged EVs drawing power simultaneously
TOLERANCE            = 1e-2
N_ITER_S1            = 15
N_ITER_S2            = 15
N_ITER_S3            = 15
DECIMAL_DIGITS       = 5

# Figure style
# Generalização: 150 dpi e method=4 (sem perdas). Medido em 2026-10-03: ~57% menor que 360 dpi e
# ~15× mais rápido que method=6, que dá o mesmo tamanho que method=4.
DPI          = 150   # summary figures
DPI_ITER     = 150   # per-iteration figures (só a última do S1 é gravada)
# WebP lossless: pixels idênticos ao PNG, ~60-80% menor (sem pil_kwargs o savefig
# usa WebP com perdas por padrão — nunca omitir estas opções).
WEBP_KWARGS  = {"lossless": True, "quality": 100, "method": 4}
FIGSIZE      = (16, 6)
FONT_TITLE   = 16
FONT_LABEL   = 13
FONT_TICK    = 11
FONT_LEGEND  = 11
GRID_STYLE       = dict(color="#95a5a6", linestyle="--", linewidth=0.5, alpha=0.7)
MINOR_GRID_STYLE = dict(color="#d3d3d3", linestyle="--", linewidth=0.2, alpha=0.6)

# Layout fixo compartilhado: garante área útil (axes) idêntica em Fig_01 e Fig_02
_FIG_LEFT   = 0.09     # margem esquerda (fração da figura) — acomoda rótulo+tick do eixo Y
_FIG_RIGHT  = 0.99     # margem direita
_FIG_BOTTOM = 0.13     # margem inferior — acomoda rótulo do eixo X
_FIG_TOP    = 0.82     # topo do axes — grid mais próximo da legenda
_LEG_ANCHOR = (0.5, 1.04)    # legenda: centralizada, logo acima do topo do axes
_LEG_LOC    = "lower center"
_TITLE_Y    = 1.19     # título: próximo à borda superior, em coordenadas do axes


# ─── City catalogue ───────────────────────────────────────────────────────────
CITIES = {
    1: "CampoGrande",
    2: "Tetuan",
}


# ─── City selection helpers ───────────────────────────────────────────────────

def choose_city(choice: int):
    """Resolve a city index to (name, path); prompts interactively if choice is invalid/0."""
    def resolve(c):
        """Look up CITIES[c] and validate that its data folder exists."""
        name = CITIES[c]
        p = BASE_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"Pasta da cidade não encontrada: {p}")
        return name, p

    if choice in CITIES:
        return resolve(choice)

    print("\n=== Selecione a cidade ===")
    for k, name in sorted(CITIES.items()):
        p = BASE_DIR / name
        print(f"  {k}. {name}  →  {p}")
    print("")
    while True:
        try:
            c = int(input(f"Escolha [{min(CITIES)}-{max(CITIES)}]: ").strip())
            if c not in CITIES:
                raise ValueError
            return resolve(c)
        except ValueError:
            print("Entrada inválida. Tente novamente.")


def choose_city_file(city_path: Path, file_choice: int, pattern="*.csv"):
    """Resolve file_choice (1-based index) to a city CSV path; prompts interactively if 0/falsy."""
    files = sorted(city_path.glob(pattern))
    if not files:
        raise FileNotFoundError(f"Nenhum '{pattern}' em: {city_path}")

    def resolve(idx):
        """Validate idx is within range and return the corresponding file."""
        if not (1 <= idx <= len(files)):
            raise ValueError(f"Opção inválida: {idx}. Use 1–{len(files)}.")
        return files[idx - 1]

    if file_choice:
        return resolve(int(file_choice))

    print("\n=== Selecione o arquivo da cidade ===")
    w = max(len(p.name) for p in files)
    for i, p in enumerate(files, 1):
        print(f"  {i:>2}. {p.name:<{w}}")
    print("")
    while True:
        try:
            return resolve(int(input(f"Escolha [1-{len(files)}]: ").strip()))
        except ValueError:
            print("Entrada inválida. Tente novamente.")


# ─── Results path helpers ─────────────────────────────────────────────────────

SCENARIO_RE = re.compile(r'^evp_(\w+?)_(s\d+|minimum|reduced)(?:_|$)', re.IGNORECASE)


def _stem_lower(path_or_name):
    """Return the lowercased filename stem (no directory, no extension)."""
    return PurePath(path_or_name).stem.lower()


def extrair_token_scenario(path_ou_nome):
    """Extract the '{fleet}_{scenario}' token from a pp_*/evp_* fleet filename.

    Strips a leading 'pp_' prefix, then matches SCENARIO_RE (evp_<fleet>_<s\\d+|minimum|reduced>).
    Falls back to the full stem when the pattern doesn't match (e.g. '01_data_ev_reduce').
    """
    name = _stem_lower(path_ou_nome)
    if name.startswith("pp_"):
        name = name[3:]
    m = SCENARIO_RE.match(name)
    if not m:
        return name  # fallback: usa o stem completo (ex.: 01_data_ev_reduce)
    return f'{m.group(1)}_{m.group(2)}'


def extrair_power_consumption_schema(selected_city_file):
    """Strip the 'pc_' prefix from a city load-profile filename to get its schema token."""
    p = PurePath(selected_city_file)
    fname = p.name
    if not (fname.lower().startswith('pc_') and p.suffix.lower() == '.csv'):
        return p.stem  # fallback: usa o stem completo (ex.: 01_data_en_reduce)
    return p.stem[3:]


def build_instance_name(selected_file, selected_city_file, prefix='inst_'):
    """Build the instance directory name '{prefix}{fleet}_{scenario}_{city_schema}'."""
    token  = extrair_token_scenario(selected_file)
    schema = extrair_power_consumption_schema(selected_city_file)
    return f'{prefix}{token}_{schema}'


def build_results_path(city_name: str, scenario_name: str, inst_name: str) -> Path:
    """Build the output path Results/V2G/{city_name}/{scenario_name}/{inst_name}."""
    return path_root / "Results" / "V2G" / city_name / "generalization" / "v2g_stable" / scenario_name / inst_name


def ensure_results_dir(p: Path) -> Path:
    """Create the results directory (parents included) if missing and return it.

    If p has a file suffix, its parent is used instead. Raises if a non-directory
    file already occupies that path.
    """
    if p.suffix:
        p = p.parent
    if p.exists() and not p.is_dir():
        raise FileExistsError(f"Já existe um arquivo com este nome: {p}")
    p.mkdir(parents=True, exist_ok=True)
    print(f"[dir] {p.resolve()}")
    return p


def _pp_scenario_name(pp_file: Path) -> str:
    """Derive the scenario name from a pp_{N}_{scenario}.csv fleet filename."""
    stem = pp_file.stem
    m = re.match(r'^pp_\d{3,}_(.+)$', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    if stem.lower().startswith("pp_"):
        return stem[3:]
    return stem


# ─── Fleet loader (reads preprocessed CSV) ───────────────────────────────────

def load_preprocessed_fleet(pp_file: Path) -> pd.DataFrame:
    """Load a pp_*.csv fleet file and validate that all required V2G columns are present."""
    fleet = pd.read_csv(pp_file)
    required = {
        "battery_capacity_kWh", "energy_adjusted_kWh", "energy_to_full_kWh",
        "soc_current_pct", "soc_min_pct", "energy_min_kWh",
        "ts_to_full", "start_ts", "end_ts",
    }
    missing = required - set(fleet.columns)
    if missing:
        raise ValueError(f"Colunas ausentes em {pp_file.name}: {missing}")
    return fleet.reset_index(drop=True)


def wrap_end_of(fleet: pd.DataFrame) -> np.ndarray:
    """Último slot (1-based) da continuação da sessão na madrugada; 0 = sem continuação.

    Dia periódico (2026-10-08): uma sessão que passa da meia-noite continua nos primeiros
    slots do mesmo dia, que fazem as vezes da madrugada seguinte, em vez de ser cortada no
    slot 144. A janela do EV é [start_ts, end_ts] ∪ [1, wrap_end_ts]. A coluna vem do
    pré-processamento; pp_*.csv sem ela (frotas sem sessões após a meia-noite) valem 0."""
    if "wrap_end_ts" in fleet.columns:
        return fleet["wrap_end_ts"].fillna(0).values.astype(int)
    return np.zeros(len(fleet), dtype=int)


def greedy_ev_demand(fleet: pd.DataFrame, n_ts: int) -> np.ndarray:
    """Uncoordinated charging profile: each EV charges from start_ts for ts_to_full slots
    (o que passa da meia-noite continua na madrugada, até wrap_end_ts)."""
    starts  = fleet["start_ts"].values.astype(int) - 1      # start_ts 1-based → índice 0-based
    ts_full = fleet["ts_to_full"].values.astype(int)
    ends_g  = np.minimum(starts + ts_full, n_ts)
    resto   = np.minimum(np.maximum(0, starts + ts_full - n_ts), wrap_end_of(fleet))
    ts      = np.arange(n_ts)
    active  = ((starts[:, None] <= ts[None, :]) & (ts[None, :] < ends_g[:, None])) | (ts[None, :] < resto[:, None])
    return active.sum(axis=0) * CHARGER_POWER_KW * COINCIDENCE_FACTOR


def greedy_min_demand(fleet: pd.DataFrame, energy_to_min: np.ndarray, n_ts: int) -> np.ndarray:
    """Greedy demand para carga até soc_min apenas (upper bound para S2).

    Usa o deficit individual de cada EV (energy_to_min), não a capacidade total.
    EVs já acima do soc_min (deficit=0) não contribuem para a demanda.
    """
    starts  = fleet["start_ts"].values.astype(int) - 1      # start_ts 1-based → índice 0-based
    ts_min  = np.ceil(energy_to_min / ENERGY_PER_SLOT_KWH).astype(int)
    ends_g  = np.minimum(starts + ts_min, n_ts)
    ts      = np.arange(n_ts)
    active  = (starts[:, None] <= ts[None, :]) & (ts[None, :] < ends_g[:, None])
    return active.sum(axis=0) * CHARGER_POWER_KW


# ─── Availability matrix ──────────────────────────────────────────────────────

def build_avail(starts: np.ndarray, ends: np.ndarray, n_ts: int,
                wrap_end: np.ndarray = None) -> np.ndarray:
    """avail[i, t] = True if EV i is available at timeslot t+1 (1-indexed): [start, end]
    e, se wrap_end for dado, também [1, wrap_end] (continuação na madrugada)."""
    ts = np.arange(1, n_ts + 1)
    avail = (starts[:, None] <= ts[None, :]) & (ts[None, :] <= ends[:, None])
    if wrap_end is not None:
        avail |= ts[None, :] <= wrap_end[:, None]
    return avail


# ─── S1 — Discharge (peak-shaving) ───────────────────────────────────────────

def stage1(pconv_0: np.ndarray, avail: np.ndarray, discharge_cap: np.ndarray,
           out_dir: Path, K: int = N_ITER_S1, n_total: int = 0,
           ) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """S1: descarga V2G com bisseção flat-top.

    Encontra o menor teto Pf onde os EVs disponíveis conseguem shavar todos
    os picos acima de Pf (flat-top). A figura por iteração sempre exibe o
    melhor Pf factível encontrado até então (best_Pc), que é garantidamente
    monotonicamente não-crescente — sem oscilação visual.
    """
    t0         = time.perf_counter()
    n_evs_flt  = len(discharge_cap)
    n_ts       = pconv_0.shape[0]

    Upper_Pc = float(pconv_0.max())

    # ── Lower_Pc baseado nos limites reais da frota ───────────────────────────
    # LB 1 (por slot): floor mínimo pela capacidade de descarga em cada slot
    can_per_slot  = (avail & (discharge_cap[:, None] >= ENERGY_PER_SLOT_KWH)).sum(axis=0)
    Lower_Pc_slot = float(np.maximum(pconv_0 - can_per_slot * CHARGER_POWER_KW, 0.0).max())

    # LB 2 (energia total): floor mínimo pela energia total disponível na frota (kWh)
    E_fleet = float(discharge_cap.sum())
    lo_e, hi_e = float(pconv_0.min()), Upper_Pc
    for _ in range(64):
        mid_e = (lo_e + hi_e) / 2
        if float(np.maximum(0.0, pconv_0 - mid_e).sum()) * TS_HOURS > E_fleet:
            lo_e = mid_e
        else:
            hi_e = mid_e
    Lower_Pc_energy = hi_e

    # LB 3 (orçamento de EV-slots): floor mínimo pelo total de atribuições (EV × slot)
    # que a frota consegue fazer — captura o esgotamento de reserva entre slots consecutivos
    ev_slot_budget = int(np.floor(discharge_cap / ENERGY_PER_SLOT_KWH).sum())
    lo_s, hi_s = float(pconv_0.min()), Upper_Pc
    for _ in range(64):
        mid_s = (lo_s + hi_s) / 2
        ev_slots_needed = int(
            np.floor(np.maximum(0.0, pconv_0 - mid_s) / CHARGER_POWER_KW).sum())
        if ev_slots_needed > ev_slot_budget:
            lo_s = mid_s
        else:
            hi_s = mid_s
    Lower_Pc_evslot = hi_s

    Lower_Pc = max(Lower_Pc_slot, Lower_Pc_energy, Lower_Pc_evslot)
    # ─────────────────────────────────────────────────────────────────────────

    step = (Upper_Pc - Lower_Pc) / K

    print(f"[S1] Upper_Pc={Upper_Pc:.1f}  Lower_Pc={Lower_Pc:.1f}  step={step:.1f} kW/iter"
          f"  (LB_slot={Lower_Pc_slot:.1f}"
          f"  LB_energy={Lower_Pc_energy:.1f}"
          f"  LB_evslot={Lower_Pc_evslot:.1f})")

    # ── Descida flat-top incremental com reservas acumuladas ─────────────────
    # Cada iteração baixa o alvo de descarga em 'step' kW (linear).
    # Atribui apenas os EVs incrementais necessários para o novo patamar.
    # As reservas são acumuladas — iter j+1 continua de onde iter j parou.
    # Resultado: perfil flat-top crescentemente profundo, sem oscilação.
    #
    # Descarga SEQUENCIAL por EV: uma vez que um EV abre um bloco de
    # descarga (run_start[i]..run_end[i]), só pode ser recrutado de novo no
    # slot imediatamente anterior ao início do bloco ou imediatamente
    # posterior ao fim — nunca um slot não-adjacente (o que abriria um
    # segundo bloco). Reflete o comportamento físico esperado de um EV
    # prestando serviço V2G (um único intervalo contínuo de descarga, sem
    # ciclos de liga/desliga do inversor). Ver docs/08_variantes_pipeline_v2g.md
    # §5.8 — medido como neutro pro pico final (±0,8pp em 6 cenários), não é
    # uma correção de desempenho, é realismo do modelo.
    pconv_curr  = pconv_0.copy()
    reserve     = discharge_cap.copy()
    alloc       = np.zeros((n_evs_flt, n_ts), dtype=bool)
    run_start   = np.full(n_evs_flt, -1, dtype=int)
    run_end     = np.full(n_evs_flt, -1, dtype=int)
    prev_ph_acc = np.zeros(n_ts)

    for j in range(K):
        t_iter    = time.perf_counter()
        Pc_target = max(Upper_Pc - (j + 1) * step, Lower_Pc)

        total_unshaved_kW = 0.0
        # Ordena pelo perfil ATUAL (acumulado) do maior para o menor
        for H in np.argsort(pconv_curr)[::-1]:
            if pconv_curr[H] <= Pc_target:
                break
            headroom = pconv_curr[H] - Pc_target
            n_needed = int(headroom / CHARGER_POWER_KW)
            if n_needed == 0:
                continue
            has_run = run_start >= 0
            adjacent = (H == run_start - 1) | (H == run_end + 1)
            can  = avail[:, H] & (reserve >= ENERGY_PER_SLOT_KWH) & ~alloc[:, H] & (~has_run | adjacent)
            ev_H = np.where(can)[0][:n_needed]
            n_got = len(ev_H)
            reserve[ev_H]  -= ENERGY_PER_SLOT_KWH
            alloc[ev_H, H]  = True
            for ev in ev_H:
                if run_start[ev] < 0:
                    run_start[ev] = run_end[ev] = H
                elif H == run_start[ev] - 1:
                    run_start[ev] = H
                else:
                    run_end[ev] = H
            pconv_curr[H]  -= n_got * CHARGER_POWER_KW
            total_unshaved_kW += (n_needed - n_got) * CHARGER_POWER_KW

        curr_peak   = float(pconv_curr.max())
        curr_ph_acc = np.maximum(0.0, pconv_0 - pconv_curr)
        n_disch_i   = int((alloc.sum(axis=1) > 0).sum())
        elapsed_i   = round(time.perf_counter() - t_iter, 2)

        print(f"  Iter {j+1:2d} | Target={Pc_target:8.1f} | Pico={curr_peak:8.1f}"
              f" | Unshaved={total_unshaved_kW:8.1f} kW | {n_disch_i:,} EVs | {elapsed_i}s")

        if SAVE_ITER_FIGS:
            _save_discharge_fig(
                pconv_0, pconv_curr, curr_peak, j + 1, out_dir,
                n_evs=n_disch_i, n_total=n_total,
                prev_ph_discharge=prev_ph_acc,
            )
        prev_ph_acc = curr_ph_acc.copy()

    best_pconv   = pconv_curr
    best_reserve = reserve
    best_alloc   = alloc
    best_Pc      = float(pconv_curr.max())
    best_ph_acc  = prev_ph_acc
    # ─────────────────────────────────────────────────────────────────────────

    elapsed  = round(time.perf_counter() - t0, 2)
    n_disch  = int((best_alloc.sum(axis=1) > 0).sum())
    en_disch = float(best_alloc.sum() * ENERGY_PER_SLOT_KWH)
    pico_red = float(pconv_0.max() - best_pconv.max())
    print(f"\n[S1] Pico original: {pconv_0.max():.3f} kW  →  após S1: {best_pconv.max():.3f} kW")
    print(f"     EVs descarregaram: {n_disch:,}  |  Energia: {en_disch:.1f} kWh"
          f"  |  Redução: {pico_red:.1f} kW  |  {elapsed:.2f}s")
    print(f"[S1] Tempo total: {elapsed:.2f}s")

    results = pd.DataFrame([{
        "Iteration": K, "Pf_resultante_kW": round(float(best_pconv.max()), 3),
        "Energia_descarga_kWh": round(en_disch, 2),
        "EVs_descarregaram": n_disch, "ExecTime": elapsed,
    }])
    return best_pconv, best_reserve, best_alloc, results


# ─── S2 / S3 — Charging (LCVF) ───────────────────────────────────────────────

def _run_charging_iter(pconv_0: np.ndarray, avail: np.ndarray, energy_rem: np.ndarray,
                       blocked: np.ndarray, Pc: float,
                       ev_slack: np.ndarray = None,
                       valley_fill: bool = False,
                       n_passes: int = 1,
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """One LCVF charging trial at a fixed ceiling Pc (used inside the S2/S3 bisection).

    Repeatedly picks the slot H with either the smallest headroom-to-demand ratio
    (LCVF, default) or the deepest valley (valley_fill=True), and fills it with as
    many eligible EVs as Pc-headroom and per-EV energy remaining allow. ev_slack, if
    given, breaks ties among candidate EVs by ascending slack (least time left first).

    Returns (pconv, energy_rem, ph_acc, alloc_new, unallocated_kWh), where unallocated_kWh
    is the total energy that could not be placed under this Pc (used by the caller to
    set Flag = 1 if unallocated_kWh < TOLERANCE else 0).
    """
    n_ts      = pconv_0.shape[0]
    pconv     = pconv_0.copy()
    energy    = energy_rem.copy()
    ph_acc    = np.zeros(n_ts)
    alloc_new = np.zeros_like(blocked)

    for _pass in range(n_passes):
        processed = np.zeros(n_ts, dtype=bool)   # reset a cada passe

        for _ in range(n_ts):
            Psup = np.maximum(0.0, Pc - pconv)
            Psup[processed] = 0.0
            if Psup.max() < TOLERANCE:
                break

            can  = avail & (energy[:, None] > TOLERANCE) & ~blocked & ~alloc_new
            Pdem = can.sum(axis=0) * CHARGER_POWER_KW

            eligible = (Psup > TOLERANCE) & (Pdem > 0)
            if not eligible.any():
                break

            if valley_fill:
                # Slot com menor pconv atual (valle mais profundo)
                H = int(np.argmin(np.where(eligible, pconv, np.inf)))
            else:
                with np.errstate(divide="ignore", invalid="ignore"):
                    IndexTS = np.where(eligible, Psup / Pdem, 0.0)
                H = int(np.argmax(IndexTS))
                if IndexTS[H] == 0:
                    break

            n_can      = int(Psup[H] // CHARGER_POWER_KW)
            candidates = np.where(can[:, H])[0]
            if ev_slack is not None and len(candidates) > n_can:
                candidates = candidates[np.argsort(ev_slack[candidates])]
            ev_H    = candidates[:n_can]
            n_alloc = len(ev_H)

            energy[ev_H]       -= ENERGY_PER_SLOT_KWH
            pconv[H]           += n_alloc * CHARGER_POWER_KW
            ph_acc[H]          += n_alloc * CHARGER_POWER_KW
            alloc_new[ev_H, H]  = True
            processed[H]        = True

    unallocated = float(energy.clip(min=0).sum())
    return pconv, energy, ph_acc, alloc_new, unallocated


def _run_flat_fill_iter(pconv_0: np.ndarray, avail: np.ndarray, energy_rem: np.ndarray,
                        blocked: np.ndarray, Pc: float,
                        ev_slack: np.ndarray = None,
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Flat-fill S3: distribui carga dos EVs proporcionalmente à disponibilidade por slot.

    Phase 1 — EV-major LSF:
      Calcula quota proporcional por slot (n_avail[h] × total_needed / total_avail).
      Processa EVs em ordem crescente de folga (LSF). Para cada EV, atribui seus
      timeslots necessários nos slots com maior déficit (quota - alocado), respeitando
      capacidade de potência. Slots já no limite da quota são deixados para Phase 2.

    Phase 2 — LCVF cleanup:
      LCVF original (Psup/Pdem) para EVs que não encontraram slots com déficit
      positivo na Phase 1 (janelas estreitas ou EVs residuais).
    """
    n_ts  = pconv_0.shape[0]
    pconv = pconv_0.copy()
    energy = energy_rem.copy()
    ph_acc = np.zeros(n_ts)
    alloc_new = np.zeros_like(blocked, dtype=bool)

    # ── Phase 1: Proportional quota allocation ────────────────────────────────

    avail_unblocked = avail & ~blocked                               # (n_evs, n_ts)
    headroom        = np.maximum(0.0, Pc - pconv_0)                  # (n_ts,)
    slot_cap        = (headroom // CHARGER_POWER_KW).astype(int)     # max EVs per slot

    # Proportional target per slot
    n_avail_slot = avail_unblocked.sum(axis=0).astype(float)         # (n_ts,)
    total_needed = float((energy / ENERGY_PER_SLOT_KWH).clip(min=0).sum())
    total_avail  = float(n_avail_slot.sum())
    if total_avail > 0:
        prop_target = n_avail_slot * (total_needed / total_avail)
    else:
        prop_target = np.zeros(n_ts)
    prop_target = np.minimum(prop_target, slot_cap.astype(float))    # cap by power

    slot_remaining = slot_cap.copy()                                  # decremented per assignment
    alloc_count    = np.zeros(n_ts, dtype=float)

    # LSF ordering: EVs com menor folga = mais urgentes = processados primeiro
    ts_to_full = np.ceil(energy.clip(min=0) / ENERGY_PER_SLOT_KWH).astype(int)
    if ev_slack is not None:
        ev_order = np.argsort(ev_slack)
    else:
        window_len = avail_unblocked.sum(axis=1).astype(int)
        ev_order   = np.argsort(window_len - ts_to_full)

    for ev_idx in ev_order:
        if energy[ev_idx] <= TOLERANCE:
            continue

        ts_needed = int(np.ceil(energy[ev_idx] / ENERGY_PER_SLOT_KWH))

        # Eligible: available, unblocked, not yet allocated here, has power capacity
        eligible = (avail_unblocked[ev_idx] & ~alloc_new[ev_idx] & (slot_remaining > 0))
        avail_h  = np.where(eligible)[0]
        if len(avail_h) == 0:
            continue

        # Só atribui a slots ainda abaixo da quota (déficit positivo)
        deficit  = prop_target[avail_h] - alloc_count[avail_h]
        pos_mask = deficit > 0
        if not pos_mask.any():
            continue  # todos os slots nesta janela já atingiram quota → Phase 2

        avail_h_pos = avail_h[pos_mask]
        deficit_pos = deficit[pos_mask]
        best_slots  = avail_h_pos[np.argsort(-deficit_pos)[:ts_needed]]

        n_alloc = len(best_slots)
        energy[ev_idx]        -= n_alloc * ENERGY_PER_SLOT_KWH
        pconv[best_slots]     += CHARGER_POWER_KW
        ph_acc[best_slots]    += CHARGER_POWER_KW
        alloc_new[ev_idx, best_slots] = True
        alloc_count[best_slots] += 1
        slot_remaining[best_slots] -= 1

    # ── Phase 2: LCVF cleanup ─────────────────────────────────────────────────
    processed = np.zeros(n_ts, dtype=bool)
    for _ in range(n_ts):
        Psup = np.maximum(0.0, Pc - pconv)
        Psup[processed] = 0.0
        if Psup.max() < TOLERANCE:
            break

        can  = avail & (energy[:, None] > TOLERANCE) & ~blocked & ~alloc_new
        Pdem = can.sum(axis=0) * CHARGER_POWER_KW

        eligible_lc = (Psup > TOLERANCE) & (Pdem > 0)
        if not eligible_lc.any():
            break

        with np.errstate(divide="ignore", invalid="ignore"):
            IndexTS = np.where(eligible_lc, Psup / Pdem, 0.0)
        H = int(np.argmax(IndexTS))
        if IndexTS[H] == 0:
            break

        n_can_H = int(Psup[H] // CHARGER_POWER_KW)
        cands_H = np.where(can[:, H])[0]
        if ev_slack is not None and len(cands_H) > n_can_H:
            cands_H = cands_H[np.argsort(ev_slack[cands_H])]
        ev_H    = cands_H[:n_can_H]
        n_alloc = len(ev_H)

        energy[ev_H]       -= ENERGY_PER_SLOT_KWH
        pconv[H]           += n_alloc * CHARGER_POWER_KW
        ph_acc[H]          += n_alloc * CHARGER_POWER_KW
        alloc_new[ev_H, H]  = True
        processed[H]        = True

    unallocated = float(energy.clip(min=0).sum())
    return pconv, energy, ph_acc, alloc_new, unallocated


def stage_charging(label: str, pconv_0: np.ndarray, avail: np.ndarray,
                   energy_rem: np.ndarray, blocked: np.ndarray,
                   upper_pc: float, out_dir: Path, K: int = N_ITER_S2,
                   ref_pconv_orig: np.ndarray = None,
                   fig_base: np.ndarray = None,
                   prior_charge: np.ndarray = None,
                   n_evs_prior: int = 0,
                   n_total: int = 0,
                   ev_slack: np.ndarray = None,
                   valley_fill: bool = False,
                   n_passes: int = 1,
                   flat_fill: bool = False,
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """Run the S2/S3 LCVF binary search over the charging ceiling Pc for K iterations.

    Each iteration calls `_run_flat_fill_iter` (if flat_fill) or `_run_charging_iter`
    at the midpoint Pc; a feasible trial (Flag=1, all energy allocated) tightens
    Upper_Pc and is kept as the new best, otherwise Lower_Pc rises and the trial is
    kept only if it beats the current best on unallocated energy. If SAVE_ITER_FIGS,
    also renders a per-iteration figure via `_save_charging_fig`.

    Returns (best_pconv, best_energy_rem, best_alloc, results_df) — the best feasible
    (or least-infeasible) allocation found and a per-iteration convergence log.
    """
    Upper_Pc  = upper_pc
    Lower_Pc  = float(pconv_0.min())
    print(f"[{label}] Upper_Pc={Upper_Pc:.3f}  Lower_Pc={Lower_Pc:.3f}")

    # Figura incremental: referência original e base dos layers
    _ref  = ref_pconv_orig if ref_pconv_orig is not None else pconv_0
    _base = fig_base       if fig_base       is not None else pconv_0

    results      = []
    best_pconv   = pconv_0.copy()
    best_energy  = energy_rem.copy()
    best_alloc   = np.zeros_like(blocked)
    best_unalloc = float("inf")
    best_ph_acc  = np.zeros(pconv_0.shape[0])   # melhor alocação acumulada (Flag=1)

    for j in range(K):
        Pc = (Upper_Pc + Lower_Pc) / 2
        t0 = time.perf_counter()
        if flat_fill:
            pconv_new, en_new, ph_acc, alloc_new, unalloc = _run_flat_fill_iter(
                pconv_0, avail, energy_rem, blocked, Pc, ev_slack=ev_slack)
        else:
            pconv_new, en_new, ph_acc, alloc_new, unalloc = _run_charging_iter(
                pconv_0, avail, energy_rem, blocked, Pc,
                ev_slack=ev_slack, valley_fill=valley_fill, n_passes=n_passes)
        elapsed = round(time.perf_counter() - t0, 2)
        Flag    = 1 if unalloc < TOLERANCE else 0

        results.append(dict(Iteration=j+1, Pc=round(Pc, 3),
                            Unallocated_kWh=round(unalloc, 2), Flag=Flag, ExecTime=elapsed))
        print(f"  Iter {j+1:2d} | Pc={Pc:8.3f} | Unalloc={unalloc:8.2f} kWh | Flag={Flag} | {elapsed}s")

        if SAVE_ITER_FIGS:
            n_evs_iter = int(alloc_new.any(axis=1).sum())
            _save_charging_fig(_ref, _base, ph_acc, Pc, j + 1, label, out_dir,
                               prior_charge, n_evs=n_evs_iter,
                               n_evs_prior=n_evs_prior, n_total=n_total,
                               prev_ph_acc=best_ph_acc,
                               y_max=Upper_Pc * 1.05)

        if Flag == 1:
            Upper_Pc     = Pc
            best_pconv   = pconv_new
            best_energy  = en_new
            best_alloc   = alloc_new
            best_unalloc = unalloc
            best_ph_acc  = ph_acc.copy()
        else:
            Lower_Pc = Pc
            if unalloc < best_unalloc:
                best_pconv   = pconv_new
                best_energy  = en_new
                best_alloc   = alloc_new
                best_unalloc = unalloc
                best_ph_acc  = ph_acc.copy()

    return best_pconv, best_energy, best_alloc, pd.DataFrame(results)


# ─── S2+S3 unificado — preenchimento LCVF real (recálculo dinâmico + memória) ──
#
# Adotado em produção 2026-09-29 (docs/08_variantes_pipeline_v2g.md §5.7/§5.9),
# depois de validado em 35 instâncias contra o `stage_charging` acima (0
# regressões, ganhos grandes nos piores casos). Substitui as chamadas SEPARADAS
# de S2 (carga mínima) e S3 (carga plena) por UM único preenchimento até o alvo
# de 90%, na mesma lógica do `subalgorithm1_fast`/`subalgorithm2_fast` do LCVF
# clássico (`lcvf_fast_v2g.py`): prioridade psup/pdem recalculada a cada slot
# preenchido (nunca uma quota estática pré-computada) e bissecção externa com
# MEMÓRIA (uma tentativa Flag=0 mantém o progresso pra próxima iteração
# continuar em cima, só Flag=1 desfaz). Ver `Codes/E_V2G/scripts/variantes/
# v2g_lcvf_style_fill.py` para a investigação original e o histórico de testes.
#
# Efeito colateral aceito: como não existe mais uma fase de carga mínima
# separada, "Após S2" nos CSVs/figuras de saída passa a ser idêntico a
# "Após S1" (ver main()) — decisão explícita do usuário (2026-09-29), já que
# a versão unificada teve desempenho muito melhor que preservar as duas
# chamadas separadas (ver docs/08 §5.9).

def _run_lcvf_style_iter(pconv_base: np.ndarray, avail: np.ndarray, energy_base: np.ndarray,
                         blocked: np.ndarray, Pc: float,
                         priority_key: np.ndarray) -> tuple:
    """Uma passada no estilo `subalgorithm1_fast` do LCVF real — percorre os T
    slots em ordem de prioridade psup/pdem RECALCULADA a cada slot preenchido
    (nunca pula um slot sem candidato). `pconv_base`/`energy_base`/`blocked`
    são o estado JÁ ACUMULADO de tentativas anteriores — esta função só
    ADICIONA alocação em cima deles. Desempate dentro de cada slot: menor
    `priority_key` primeiro (por padrão, `-start_ts`, chegada mais tarde tem
    prioridade — mesmo desempate do LCVF real)."""
    n_ts  = pconv_base.shape[0]
    pconv = pconv_base.copy()
    energy = energy_base.copy()
    ph_acc = np.zeros(n_ts)
    alloc_new = np.zeros_like(blocked, dtype=bool)

    can0 = avail & ~blocked
    active = energy > TOLERANCE
    pdem = (can0 & active[:, None]).sum(axis=0).astype(float) * CHARGER_POWER_KW
    psup = np.maximum(0.0, Pc - pconv)

    remaining = np.ones(n_ts, dtype=bool)
    n_remaining = n_ts
    while n_remaining > 0:
        with np.errstate(divide="ignore", invalid="ignore"):
            index_ts = np.where(remaining & (pdem > 0), psup / pdem, -np.inf)
        H = int(np.argmax(index_ts))

        cand_mask = can0[:, H] & (energy > TOLERANCE) & ~alloc_new[:, H]
        cand_idx = np.where(cand_mask)[0]

        if cand_idx.size > 0:
            cand_idx = cand_idx[np.argsort(priority_key[cand_idx])]
            n_can = int(psup[H] // CHARGER_POWER_KW)
            if n_can > 0:
                alloc_idx = cand_idx[:n_can]
                alloc_new[alloc_idx, H] = True
                ph_acc[H] += len(alloc_idx) * CHARGER_POWER_KW
                pconv[H]  += len(alloc_idx) * CHARGER_POWER_KW
                psup[H]   -= len(alloc_idx) * CHARGER_POWER_KW

                energy[alloc_idx] -= ENERGY_PER_SLOT_KWH
                drained = energy[alloc_idx] <= TOLERANCE
                if drained.any():
                    drained_idx = alloc_idx[drained]
                    energy[drained_idx] = 0.0
                    pdem -= (can0[drained_idx, :].sum(axis=0)) * CHARGER_POWER_KW

        remaining[H] = False
        n_remaining -= 1

    unallocated = float(energy.clip(min=0).sum())
    return pconv, energy, ph_acc, alloc_new, unallocated


def stage_charging_unified(label: str, pconv_0: np.ndarray, avail: np.ndarray,
                           energy_rem: np.ndarray, blocked: np.ndarray,
                           upper_pc: float, out_dir: Path, K: int,
                           priority_key: np.ndarray,
                           ref_pconv_orig: np.ndarray = None,
                           n_total: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """Preenchimento S2+S3 unificado (ver cabeçalho da seção acima). Bissecção
    externa ACUMULATIVA COM MEMÓRIA: Flag=0 (não alocou tudo sob aquele teto)
    MANTÉM o progresso e a próxima iteração constrói em cima; Flag=1 (alocou
    tudo, teto generoso demais) DESFAZ e testa um teto mais baixo."""
    Upper_Pc, Lower_Pc = upper_pc, float(pconv_0.min())
    _ref = ref_pconv_orig if ref_pconv_orig is not None else pconv_0

    pconv_acc  = pconv_0.copy()
    energy_acc = energy_rem.copy()
    alloc_acc  = np.zeros_like(blocked, dtype=bool)
    prev_ph_acc = np.zeros(pconv_0.shape[0])
    results = []

    for j in range(K):
        Pc = (Upper_Pc + Lower_Pc) / 2
        t0 = time.perf_counter()
        blocked_now = blocked | alloc_acc
        pconv_new, energy_new, ph_acc_trial, alloc_trial, unalloc = _run_lcvf_style_iter(
            pconv_acc, avail, energy_acc, blocked_now, Pc, priority_key)
        elapsed = round(time.perf_counter() - t0, 2)
        Flag = 1 if unalloc < TOLERANCE else 0

        results.append(dict(Iteration=j+1, Pc=round(Pc, 3),
                            Unallocated_kWh=round(unalloc, 2), Flag=Flag, ExecTime=elapsed))
        print(f"  Iter {j+1:2d} | Pc={Pc:8.3f} | Unalloc={unalloc:8.2f} kWh | Flag={Flag} | {elapsed}s")

        if Flag == 1:
            # teto generoso demais: desfaz esta tentativa, aperta o teto
            Upper_Pc = Pc
            ph_show = pconv_acc + ph_acc_trial - pconv_0   # visualização apenas (não persiste)
        else:
            # mantém o progresso desta tentativa, testa um teto mais alto
            Lower_Pc = Pc
            pconv_acc  = pconv_new
            energy_acc = energy_new
            alloc_acc  = alloc_acc | alloc_trial
            ph_show = pconv_acc - pconv_0

        if SAVE_ITER_FIGS:
            n_evs_iter = int(alloc_acc.any(axis=1).sum())
            _save_charging_fig(_ref, pconv_0, ph_show, Pc, j + 1, "S3", out_dir,
                               prior_charge=None, n_evs=n_evs_iter,
                               n_evs_prior=0, n_total=n_total,
                               prev_ph_acc=prev_ph_acc,
                               y_max=upper_pc * 1.05)
            prev_ph_acc = ph_show.copy()

    return pconv_acc, energy_acc, alloc_acc, pd.DataFrame(results)


# ─── Summary ──────────────────────────────────────────────────────────────────

def compute_summary(fleet: pd.DataFrame,
                    pconv_stages: dict, soc_stages: dict) -> pd.DataFrame:
    """Build the per-stage summary table (peak/valley/variance of Pconv, mean SoC, EV counts)."""
    rows = []
    for label, pconv in pconv_stages.items():
        soc = soc_stages[label]
        rows.append(dict(
            Estágio            = label,
            Pico_Pconv_kW      = round(float(pconv.max()), 3),
            Vale_Pconv_kW      = round(float(pconv.min()), 3),
            Var_Carga_kW2      = round(float(pconv.var()), 3),
            SoC_medio_pct      = round(float(soc.mean()), 3),
            EVs_acima_min      = int((soc >= fleet["soc_min_pct"].values - 0.01).sum()),
            EVs_carga_completa = int((soc >= SOC_FULL_TARGET * 100 - 0.1).sum()),
        ))
    return pd.DataFrame(rows)


# ─── Figures ──────────────────────────────────────────────────────────────────

def _ts_hours(n_ts: int = TOTAL_TIMESLOTS) -> np.ndarray:
    """Timeslot index → hour-of-day axis (10-minute slots)."""
    return np.arange(n_ts) * 10 / 60


def _fmt_n(n: int) -> str:
    """Format an integer with thousands separators for figure/print labels."""
    return f"{n:,}"


def plot_initial_profile(pconv_0: np.ndarray, power_ev: np.ndarray,
                         out_dir: Path, n_evs: int) -> None:
    """Save Fig_01_perfil_inicial.webp: conventional load + uncoordinated EV demand stacked."""
    hours = _ts_hours()
    fig, ax = plt.subplots(figsize=FIGSIZE)
    b_conv = ax.bar(hours, pconv_0, width=10/60 * 0.9, color="blue",
                    edgecolor="white", linewidth=0.3,
                    label="Carga convencional (Pconv)", align="edge")
    b_ev   = ax.bar(hours, power_ev, bottom=pconv_0, width=10/60 * 0.9,
                    color="red", edgecolor="white", linewidth=0.3, hatch="xx", alpha=0.85,
                    label="EVs (carga não-coordenada)", align="edge")
    l_max  = ax.axhline(pconv_0.max(), color="blue", ls="--", lw=1.5,
                        label=f"Pico Conv: {pconv_0.max():.0f} kW")
    l_tot  = ax.axhline((pconv_0 + power_ev).max(), color="red", ls="--", lw=1.5,
                        label=f"Pico Total: {(pconv_0+power_ev).max():.0f} kW")
    l_min  = ax.axhline(pconv_0.min(), color="black", ls="-", lw=1.5,
                        label=f"Min Conv: {pconv_0.min():.0f} kW")
    ax.legend([l_max, b_conv, l_min, b_ev, l_tot],
              [h.get_label() for h in [l_max, b_conv, l_min, b_ev, l_tot]],
              loc=_LEG_LOC, bbox_to_anchor=_LEG_ANCHOR, ncol=3,
              fontsize=FONT_LEGEND, framealpha=0.9, edgecolor="#cccccc")
    ax.set_title(f"Perfil inicial — {_fmt_n(n_evs)} EVs (antes do V2G)",
                 fontsize=FONT_TITLE, y=_TITLE_Y)
    ax.set_xlabel("Hora", fontsize=FONT_LABEL)
    ax.set_ylabel("Potência (kW)", fontsize=FONT_LABEL)
    ax.set_xlim(0, 24); ax.xaxis.set_major_locator(plt.MultipleLocator(2))
    # y-axis floor matches lcvf_fast_v2g.py's convention (min(Pconv) * 0.9)
    # so the two pipelines' figures share the same vertical scale.
    ax.set_ylim(bottom=pconv_0.min() * 0.9)
    ax.tick_params(axis="both", labelsize=FONT_TICK)
    ax.yaxis.grid(**GRID_STYLE, zorder=0)
    ax.yaxis.grid(which="minor", **MINOR_GRID_STYLE, zorder=0)
    ax.yaxis.set_minor_locator(AutoMinorLocator())
    fig.subplots_adjust(left=_FIG_LEFT, right=_FIG_RIGHT,
                        bottom=_FIG_BOTTOM, top=_FIG_TOP)
    out = out_dir / "Fig_01_perfil_inicial.webp"
    fig.savefig(out, dpi=DPI, pil_kwargs=WEBP_KWARGS); plt.close(fig)
    print(f"[salvo] {out.name}")


def plot_stages(pconv_stages: dict, out_dir: Path, n_evs: int, y_min: float = None,
                summary: pd.DataFrame = None) -> None:
    """Save Fig_02_v2g_estagios.webp: overlaid load curve per stage.

    Layout follows lcvf_fast_v2g.py's iteration figures (Fig_03_*_VF_exec):
    same figure size and font sizes, legend above the axes, y ticks in "k",
    x axis in 1-indexed time slots. Each curve's peak is marked on the curve
    and repeated in the legend; a curve identical to the previous stage
    (e.g. "Após S2", since S2+S3 are solved jointly) is not redrawn.
    """
    F_TICK, F_LABEL, F_TITLE, F_LEGEND = 24, 30, 36, 24
    style = {
        "Inicial":       dict(color="blue",  ls="-",  lw=3.0),
        "Após S1 (V2G)": dict(color="red",   ls="--", lw=3.0),
        "Após S2 (mín)": dict(color="green", ls="-.", lw=3.0),
        "Após S3 (máx)": dict(color="black", ls="-",  lw=3.0),
    }
    label = {
        "Inicial":       "Initial (no EV)",
        "Após S1 (V2G)": "After S1 (V2G discharge)",
        "Após S2 (mín)": "After S2 (min SoC)",
        "Após S3 (máx)": "After S2+S3 (charging to 90% SoC)",
    }
    x = np.arange(1, TOTAL_TIMESLOTS + 1)
    fig, ax = plt.subplots(figsize=(24, 13))

    handles, prev = [], None
    for name, pc in pconv_stages.items():
        if prev is not None and np.allclose(pc, prev):
            continue
        prev = pc
        st = style.get(name, dict(color="gray", ls="-", lw=3.0))
        peak_i = int(np.argmax(pc))
        line, = ax.plot(x, pc, color=st["color"], ls=st["ls"], lw=st["lw"], zorder=3,
                        label=f"{label.get(name, name)} — max: {pc.max():,.2f} kW")
        ax.axhline(pc.max(), color=st["color"], ls=":", lw=1.5, alpha=0.8, zorder=2)
        ax.plot(x[peak_i], pc[peak_i], "o", ms=16, color=st["color"],
                markeredgecolor="white", markeredgewidth=2.5, zorder=4)
        # S1 fica abaixo da curva inicial no pico: rótulo abaixo do marcador
        below = name == "Após S1 (V2G)"
        ax.annotate(f"{pc.max() / 1000:,.1f}k", (x[peak_i], pc[peak_i]),
                    textcoords="offset points", xytext=(0, -22 if below else 18),
                    ha="center", va="top" if below else "bottom",
                    fontsize=F_TICK, fontweight="bold", color=st["color"], zorder=5,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
        handles.append(line)

    pc0 = next(iter(pconv_stages.values()))
    line_min = ax.axhline(pc0.min(), color="black", ls="-", lw=1.5,
                          label=f"Min Conv: {pc0.min():,.2f} kW")
    handles.append(line_min)

    ax.set_xlabel("Time slot", fontsize=F_LABEL, labelpad=10)
    ax.set_ylabel("Power (kW)", fontsize=F_LABEL, labelpad=10)
    step = max(1, TOTAL_TIMESLOTS // 24)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(x[::step], rotation=45, ha="right", fontsize=F_TICK)
    ax.set_xlim(-1, TOTAL_TIMESLOTS + 2)
    y_lo = y_min if y_min is not None else pc0.min() * 0.9
    y_hi = max(p.max() for p in pconv_stages.values()) * 1.1
    ax.set_ylim(y_lo, y_hi)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    ax.tick_params(axis="both", labelsize=F_TICK)
    ax.yaxis.grid(color="#95a5a6", linestyle="--", linewidth=0.5, alpha=0.7, zorder=0)
    ax.yaxis.grid(which="minor", color="#d3d3d3", linestyle="--", linewidth=0.2, zorder=0)
    ax.yaxis.set_minor_locator(AutoMinorLocator())

    ax.legend(handles, [h.get_label() for h in handles],
              loc="lower center", bbox_to_anchor=(0.5, 1.00), ncol=2,
              fontsize=F_LEGEND, framealpha=0.9, edgecolor="#cccccc",
              borderpad=0.7, handlelength=2.4, handletextpad=0.6, columnspacing=1.4)

    title = f"V2G – 3 stages | {_fmt_n(n_evs)} EVs"
    if summary is not None and len(summary) >= 4:
        s0, s1, s3 = summary.iloc[0], summary.iloc[1], summary.iloc[-1]
        red = (1 - s1.Pico_Pconv_kW / s0.Pico_Pconv_kW) * 100
        title += (f"\nS1 peak reduction: {red:.2f}%  |  "
                  f"EVs at 90% SoC: {int(s3.EVs_carga_completa):,}/{n_evs:,}  |  "
                  f"EVs ≥ min SoC: {int(s3.EVs_acima_min):,}/{n_evs:,}")
    n_leg_rows = int(np.ceil(len(handles) / 2))
    ax.set_title(title, fontsize=F_TITLE, pad=0,
                 y=1.07 + 0.075 * n_leg_rows + (0.08 if "\n" in title else 0))
    plt.tight_layout(rect=(0, 0, 1, 0.85))

    out = out_dir / "Fig_02_v2g_estagios.webp"
    fig.savefig(out, dpi=DPI, bbox_inches="tight", pil_kwargs=WEBP_KWARGS); plt.close(fig)
    print(f"[salvo] {out.name}")


def plot_soc(soc_inicial: np.ndarray, soc_final: np.ndarray,
             soc_min: np.ndarray, out_dir: Path, n_evs: int) -> None:
    """Save Fig_03_soc_distribuicao.webp: SoC histogram (initial vs. post-S3) + per-EV scatter."""
    fig, axes = plt.subplots(1, 2, figsize=FIGSIZE)
    bins = np.linspace(0, 100, 51)

    axes[0].hist(soc_inicial, bins=bins, color="#6b7280", alpha=.85, label="SoC inicial")
    axes[0].hist(soc_final,   bins=bins, color="#22c55e", alpha=.70, label="SoC final (pós S3)")
    axes[0].axvline(soc_min.mean(), color="#f59e0b", lw=1.5, ls="--",
                    label=f"soc_min médio ({soc_min.mean():.0f}%)")
    axes[0].set_xlabel("SoC (%)", fontsize=FONT_LABEL)
    axes[0].set_ylabel("Nº de EVs", fontsize=FONT_LABEL)
    axes[0].set_title(f"Distribuição de SoC — {_fmt_n(n_evs)} EVs", fontsize=FONT_TITLE)
    axes[0].legend(fontsize=FONT_LEGEND)
    axes[0].grid(**GRID_STYLE)
    axes[0].tick_params(axis="both", labelsize=FONT_TICK)

    sample = min(5000, n_evs)
    axes[1].scatter(range(sample), soc_inicial[:sample], s=1, alpha=.3,
                    color="#6b7280", label="SoC inicial")
    axes[1].scatter(range(sample), soc_final[:sample],   s=1, alpha=.3,
                    color="#22c55e", label="SoC final")
    axes[1].set_xlabel(f"Índice EV (primeiros {_fmt_n(sample)})", fontsize=FONT_LABEL)
    axes[1].set_ylabel("SoC (%)", fontsize=FONT_LABEL)
    axes[1].set_title("SoC por EV (amostra)", fontsize=FONT_TITLE)
    axes[1].legend(fontsize=FONT_LEGEND, markerscale=6)
    axes[1].grid(**GRID_STYLE)
    axes[1].tick_params(axis="both", labelsize=FONT_TICK)

    fig.tight_layout()
    out = out_dir / "Fig_03_soc_distribuicao.webp"
    fig.savefig(out, dpi=DPI, bbox_inches="tight", pil_kwargs=WEBP_KWARGS); plt.close(fig)
    print(f"[salvo] {out.name}")


def _ev_label(n: int, total: int, prefix: str) -> str:
    """Format EV count as 'prefix — XX/YY — ZZ,ZZ%'."""
    if total > 0:
        pct = f"{n / total * 100:.2f}".replace(".", ",")
        return f"{prefix} — {n}/{_fmt_n(total)} — {pct}%"
    return f"{prefix} — {n} EVs"


def _save_discharge_fig(pconv_orig: np.ndarray, pconv_new: np.ndarray,
                        Pf: float, iteration: int, out_dir: Path,
                        n_evs: int = 0, n_total: int = 0,
                        prev_ph_discharge: np.ndarray = None) -> None:
    """S1 iteration figure — verde=iters anteriores, vermelho=iteração atual.

    Layers (bottom → top):
      Blue   : pconv_new         — potência restante após descarga
      Verde  : ph_stable         — descarga confirmada em iters anteriores (Flag=1)
      Vermelho: ph_new           — descarga adicional nesta iteração
    """
    hours      = _ts_hours()
    discharged = np.maximum(0.0, pconv_orig - pconv_new)
    prev       = prev_ph_discharge if prev_ph_discharge is not None else np.zeros_like(discharged)

    ph_stable = np.minimum(discharged, prev)
    ph_new    = np.maximum(0.0, discharged - prev)

    fig, ax = plt.subplots(figsize=FIGSIZE)

    # Layer 1 — blue: pconv restante após descarga
    b_conv = ax.bar(hours, pconv_new, width=10/60*0.9, color="blue",
                    edgecolor="white", linewidth=0.3,
                    label="Pconv pós-descarga", align="edge")

    # Layer 2 — vermelho: descarga incremental desta iteração (novo corte no piso)
    b_curr = ax.bar(hours, ph_new, bottom=pconv_new, width=10/60*0.9,
                    color="#B71C1C", edgecolor="white", linewidth=0.3,
                    hatch="xx", alpha=0.85,
                    label=_ev_label(n_evs, n_total, "Descarga iter atual S1"),
                    align="edge")

    # Layer 3 — verde: descarga acumulada de iterações anteriores (acima do corte atual)
    b_stable = ax.bar(hours, ph_stable, bottom=pconv_new + ph_new, width=10/60*0.9,
                      color="#388E3C", edgecolor="white", linewidth=0.3,
                      hatch="--", alpha=0.85,
                      label=_ev_label(n_evs, n_total, "Descarga iters ant. S1"),
                      align="edge")

    l_max = ax.axhline(pconv_orig.max(), color="blue", ls="--", lw=1.5,
                       label=f"Pico original: {pconv_orig.max():.1f} kW")
    l_min = ax.axhline(pconv_orig.min(), color="black", ls="-", lw=1.5,
                       label=f"Min Conv: {pconv_orig.min():.1f} kW")

    handles = [l_max, b_conv, l_min, b_stable, b_curr]
    ncol = (len(handles) + 1) // 2   # força exatamente 2 linhas
    ax.legend(handles, [h.get_label() for h in handles],
              loc="lower center", bbox_to_anchor=(0.5, 1.04), ncol=ncol,
              fontsize=FONT_LEGEND, framealpha=0.9, edgecolor="#cccccc")
    ax.set_title(f"S1 — Iteração {iteration:02d} | Pf={Pf:.2f} kW",
                 fontsize=FONT_TITLE, y=1.25)
    ax.set_xlabel("Hora", fontsize=FONT_LABEL)
    ax.set_ylabel("Potência (kW)", fontsize=FONT_LABEL)
    ax.set_xlim(0, 24)
    ax.xaxis.set_major_locator(plt.MultipleLocator(2))
    ax.tick_params(axis="both", labelsize=FONT_TICK)
    ax.yaxis.grid(**GRID_STYLE, zorder=0)
    ax.yaxis.grid(which="minor", **MINOR_GRID_STYLE, zorder=0)
    ax.yaxis.set_minor_locator(AutoMinorLocator())
    fig.subplots_adjust(left=_FIG_LEFT, right=_FIG_RIGHT,
                        bottom=_FIG_BOTTOM, top=0.78)
    fig.savefig(out_dir / f"Fig_S1_iter{iteration:02d}_discharge.webp", dpi=DPI_ITER, pil_kwargs=WEBP_KWARGS)
    plt.close(fig)


def _save_charging_fig(pconv_orig_ref: np.ndarray,
                       pconv_stage_base: np.ndarray,
                       ph_acc: np.ndarray,
                       Pc: float,
                       iteration: int,
                       label: str,
                       out_dir: Path,
                       prior_charge: np.ndarray = None,
                       n_evs: int = 0,
                       n_evs_prior: int = 0,
                       n_total: int = 0,
                       prev_ph_acc: np.ndarray = None,
                       y_max: float = None) -> None:
    """S2/S3 incremental iteration figure.

    Paleta por estágio — verde = já alocado (iters anteriores), vermelho = iteração atual:
      S2: verde=#388E3C  |  vermelho=#C62828
      S3: verde=#388E3C  |  vermelho=#E64A19  (prior S2 também em verde)

    Layers (bottom → top):
      Blue        : pconv_stage_base          — convencional pós-S1
      Verde prior : prior_charge              — S2 alocado (figs S3 apenas)
      Verde stable: min(ph_acc, prev_ph_acc)  — estágio atual, iters anteriores
      Vermelho    : max(0, ph_acc−prev)       — estágio atual, iteração corrente
      Gray        : s1_freed                  — espaço liberado S1 (topo)
    """
    _COLORS = {
        "S2": {"prior": "#6A1B9A", "verde": "#388E3C", "vermelho": "#C62828", "pc": "#1B5E20"},
        "S3": {"prior": "#6A1B9A", "verde": "#388E3C", "vermelho": "#E64A19", "pc": "#BF360C"},
    }
    c    = _COLORS.get(label, {"prior": "#6A1B9A", "verde": "green", "vermelho": "red", "pc": "green"})
    prev = prev_ph_acc if prev_ph_acc is not None else np.zeros_like(ph_acc)

    # divide ph_acc em porção estável (verde) e nova nesta iteração (vermelho)
    ph_stable = np.minimum(ph_acc, prev)
    ph_new    = np.maximum(0.0, ph_acc - prev)

    hours    = _ts_hours()
    s1_freed = np.maximum(0.0, pconv_orig_ref - pconv_stage_base)
    fig, ax  = plt.subplots(figsize=FIGSIZE)

    # Layer 1 — blue base
    b_conv = ax.bar(hours, pconv_stage_base, width=10/60*0.9, color="blue",
                    edgecolor="white", linewidth=0.3,
                    label="Pconv base (pós S1)", align="edge")

    # Layer 2 — roxo: S2 completo (apenas figuras S3)
    bottom = pconv_stage_base.copy()
    if prior_charge is not None:
        b_prior = ax.bar(hours, prior_charge, bottom=bottom, width=10/60*0.9,
                         color=c["prior"], edgecolor="white", linewidth=0.3,
                         hatch="\\\\", alpha=0.85,
                         label=_ev_label(n_evs_prior, n_total, "Alocação S2"),
                         align="edge")
        bottom = bottom + prior_charge
    else:
        b_prior = None

    # Layer 3 — verde stable: alocação do estágio atual confirmada em iters anteriores
    b_stable = ax.bar(hours, ph_stable, bottom=bottom, width=10/60*0.9,
                      color=c["verde"], edgecolor="white", linewidth=0.3,
                      hatch="--", alpha=0.85,
                      label=_ev_label(n_evs, n_total, f"Iters ant. {label}"),
                      align="edge")
    bottom = bottom + ph_stable

    # Layer 4 — vermelho: alocação nova nesta iteração
    b_curr = ax.bar(hours, ph_new, bottom=bottom, width=10/60*0.9,
                    color=c["vermelho"], edgecolor="white", linewidth=0.3,
                    hatch="xx", alpha=0.85,
                    label=_ev_label(n_evs, n_total, f"Iter atual {label}"),
                    align="edge")

    # Layer 5 — amarelo: S1 freed space (topo, semi-transparente)
    b_s1 = ax.bar(hours, s1_freed, bottom=pconv_stage_base, width=10/60*0.9,
                  color="#FFB300", edgecolor="white", linewidth=0.3,
                  hatch="//", alpha=0.65,
                  label="Espaço liberado S1", align="edge")

    # Pico real das barras (pode divergir de Pc durante a bissecção)
    bar_top = pconv_stage_base.copy()
    if prior_charge is not None:
        bar_top = bar_top + prior_charge
    bar_top     = bar_top + ph_stable + ph_new
    actual_peak = float(bar_top.max())

    l_max  = ax.axhline(pconv_orig_ref.max(), color="blue", ls="--", lw=1.5,
                        label=f"Pico original: {pconv_orig_ref.max():.1f} kW")
    l_min  = ax.axhline(pconv_orig_ref.min(), color="black", ls="-", lw=1.5,
                        label=f"Min Conv: {pconv_orig_ref.min():.1f} kW")
    l_pc   = ax.axhline(Pc, color=c["pc"], ls="-", lw=2.0,
                        label=f"Pc: {Pc:.2f} kW")
    l_peak = ax.axhline(actual_peak, color=c["vermelho"], ls=":", lw=2.0,
                        label=f"Pico atribuído: {actual_peak:.1f} kW")

    handles = [l_max, b_conv, l_min, b_s1]
    if b_prior is not None:
        handles.append(b_prior)
    handles += [b_stable, l_pc, b_curr, l_peak]
    ncol = (len(handles) + 1) // 2   # força exatamente 2 linhas
    ax.legend(handles, [h.get_label() for h in handles],
              loc="lower center", bbox_to_anchor=(0.5, 1.04), ncol=ncol,
              fontsize=FONT_LEGEND, framealpha=0.9, edgecolor="#cccccc")
    ax.set_title(f"{label} — Iteração {iteration:02d} | Pc={Pc:.2f} kW",
                 fontsize=FONT_TITLE, y=1.25)
    ax.set_xlabel("Hora", fontsize=FONT_LABEL)
    ax.set_ylabel("Potência (kW)", fontsize=FONT_LABEL)
    ax.set_xlim(0, 24)
    _y_max = y_max if y_max is not None else pconv_orig_ref.max() * 1.05
    ax.set_ylim(0, _y_max)   # eixo fixo em Upper_Pc — consistente entre iterações, sem corte
    ax.xaxis.set_major_locator(plt.MultipleLocator(2))
    ax.tick_params(axis="both", labelsize=FONT_TICK)
    ax.yaxis.grid(**GRID_STYLE, zorder=0)
    ax.yaxis.grid(which="minor", **MINOR_GRID_STYLE, zorder=0)
    ax.yaxis.set_minor_locator(AutoMinorLocator())
    fig.subplots_adjust(left=_FIG_LEFT, right=_FIG_RIGHT,
                        bottom=_FIG_BOTTOM, top=0.78)
    fig.savefig(out_dir / f"Fig_{label}_iter{iteration:02d}_charging.webp", dpi=DPI_ITER, pil_kwargs=WEBP_KWARGS)
    plt.close(fig)


# ─── Interactive menu ─────────────────────────────────────────────────────────

def _ask(prompt: str, default: str) -> str:
    """Prompt for input, returning default on empty input or EOF (non-interactive run)."""
    try:
        val = input(prompt).strip()
        return val if val else default
    except EOFError:
        return default


def _run_menu():
    """Exibe menu interativo e sobrescreve as variáveis globais de config."""
    global CITY_DIR_CHOICES, RUN_ALL_CITY_FILES, CITYFILE_CHOICE
    global PP_FILE_FILTER, SAVE_ITER_FIGS, EV_PCONV_SCALE_PP

    sep = "═" * 65
    print(f"\n{sep}")
    print("  V2G — Menu de Configuração")
    print(sep)

    # ── Cidades ───────────────────────────────────────────────────────────────
    print("\n[1] Cidades disponíveis:")
    for idx, name in CITIES.items():
        print(f"      {idx}  {name}")
    raw = _ask("    Selecione (ex: 0 1 2 | Enter = todas): ", "")
    if raw:
        CITY_DIR_CHOICES = [int(x) for x in raw.split() if x.isdigit() and int(x) in CITIES]
    else:
        CITY_DIR_CHOICES = list(CITIES.keys())

    # ── Perfis de cidade ──────────────────────────────────────────────────────
    print("\n[2] Perfis de cidade — rodar todos os arquivos de cada cidade?")
    raw = _ask("    (S = todos | N = escolher um por cidade) [S]: ", "S")
    RUN_ALL_CITY_FILES = raw.upper() != "N"
    if not RUN_ALL_CITY_FILES:
        print("    Qual posição (ordem alfabética) do arquivo?")
        raw = _ask("    Número (1 = primeiro, 2 = segundo…) [1]: ", "1")
        CITYFILE_CHOICE = int(raw) if raw.isdigit() else 1

    # ── Frotas ────────────────────────────────────────────────────────────────
    pp_files = sorted(EV_PREPROCESSED_DIR.rglob("pp_*.csv"))
    print(f"\n[3] Frotas disponíveis ({len(pp_files)}):")
    for i, f in enumerate(pp_files, 1):
        print(f"      {i}  {f.name}")
    raw = _ask("    Selecione (ex: 1 3 | Enter = todas): ", "")
    if raw:
        idxs = [int(x) - 1 for x in raw.split() if x.isdigit() and 1 <= int(x) <= len(pp_files)]
        PP_FILE_FILTER = [pp_files[i].name for i in idxs]
    else:
        PP_FILE_FILTER = []

    # ── Figuras de iteração ───────────────────────────────────────────────────
    print("\n[4] Salvar figuras por iteração? (lento para frotas grandes)")
    raw = _ask("    (S/N) [N]: ", "N")
    SAVE_ITER_FIGS = raw.upper() == "S"

    # ── Escala Pconv / 100 EVs ────────────────────────────────────────────────
    print("\n[5] Escala Pconv para frota 100 EVs (EV_peak = 20% do pico)?")
    raw = _ask("    (S/N) [S]: ", "S")
    EV_PCONV_SCALE_PP = "pp_100_01_data_ev_reduce" if raw.upper() != "N" else ""

    # ── Resumo ────────────────────────────────────────────────────────────────
    cidades_sel = [CITIES[i] for i in CITY_DIR_CHOICES]
    frotas_sel  = PP_FILE_FILTER if PP_FILE_FILTER else [f.name for f in pp_files]
    print(f"\n{sep}")
    print(f"  Cidades  : {', '.join(cidades_sel)}")
    print(f"  Frotas   : {', '.join(frotas_sel)}")
    print(f"  Perfis   : {'todos' if RUN_ALL_CITY_FILES else f'posição {CITYFILE_CHOICE}'}")
    print(f"  Iter figs: {'sim' if SAVE_ITER_FIGS else 'não'}")
    print(f"  Escala EV: {'sim (20%)' if EV_PCONV_SCALE_PP else 'não'}")
    print(sep)
    _ask("\n  Pressione Enter para iniciar…", "")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    """Entry point: run the 3-stage V2G pipeline over every selected fleet × city combination.

    For each pp_*.csv fleet and each selected city file: loads the fleet, builds the
    availability matrix, runs S1 (discharge) → S2 (min-charge) → S3 (full-charge),
    computes the per-stage summary, saves figures (Fig_01-03) and result CSVs to
    Results/V2G/{city}/{scenario}/{instance}/.
    """
    if INTERACTIVE_MENU:
        _run_menu()

    t_global = time.perf_counter()

    # ── Preprocessed EV fleet files ───────────────────────────────────────────
    pp_files = sorted(EV_PREPROCESSED_DIR.rglob("pp_*.csv"))
    if PP_FILE_FILTER:
        pp_files = [f for f in pp_files if f.name in PP_FILE_FILTER]
    if not pp_files:
        raise FileNotFoundError(
            f"Nenhum 'pp_*.csv' em: {EV_PREPROCESSED_DIR}\n"
            "Execute v2g_preprocessing.py primeiro."
        )
    print(f"Frotas pré-processadas: {len(pp_files)}")

    # ── City dirs ─────────────────────────────────────────────────────────────
    city_dir_idxs = CITY_DIR_CHOICES if CITY_DIR_CHOICES else [CITY_DIR_CHOICE]
    city_entries = []
    for idx in city_dir_idxs:
        city_name, city_path = choose_city(idx)
        pattern = CITY_FILE_PATTERNS.get(idx, FILE_PATTERN)
        files = (sorted(city_path.glob(pattern)) if RUN_ALL_CITY_FILES
                 else [choose_city_file(city_path, CITYFILE_CHOICE)])
        if not files:
            print(f"[aviso] Nenhum CSV em: {city_path}. Pulando.")
            continue
        city_entries.append((city_name, files))
        print(f"Cidade: {city_name}  ({len(files)} arquivo(s))")

    if not city_entries:
        raise FileNotFoundError("Nenhum arquivo de cidade encontrado.")

    n_combos = sum(len(f) for _, f in city_entries) * len(pp_files)
    print(f"\nTotal de combinações: {n_combos}")

    for pp_file in pp_files:
        scenario_name = _pp_scenario_name(pp_file)
        print(f"\n{'#'*65}")
        print(f"Frota: {pp_file.name}  →  cenário: {scenario_name}")
        print(f"{'#'*65}")

        fleet = load_preprocessed_fleet(pp_file)
        n_evs = len(fleet)
        print(f"[ok] {n_evs:,} EVs carregados de {pp_file.name}")

        soc_current    = fleet["soc_current_pct"].values.copy()
        soc_min_arr    = fleet["soc_min_pct"].values.copy()
        capacity       = fleet["battery_capacity_kWh"].values.copy()
        energy_adj     = fleet["energy_adjusted_kWh"].values.copy()
        energy_to_full = fleet["energy_to_full_kWh"].values.copy()

        starts = fleet["start_ts"].values.astype(int)
        ends   = fleet["end_ts"].values.astype(int)

        n_above_min = int((soc_current > soc_min_arr).sum())
        print(f"[ok] {n_above_min:,}/{n_evs:,} EVs acima do soc_min (disponíveis para S1)")
        print(f"[ok] Energia total para carga completa: {energy_to_full.sum():.0f} kWh")

        discharge_cap = np.maximum(0.0, (soc_current - soc_min_arr) / 100 * capacity)

        print(f"\n[mat] Construindo matriz de disponibilidade ({n_evs:,} × {TOTAL_TIMESLOTS})…")
        t0    = time.perf_counter()
        avail = build_avail(starts, ends, TOTAL_TIMESLOTS, wrap_end_of(fleet))
        print(f"[mat] {avail.nbytes / 1024**2:.1f} MB  |  {time.perf_counter()-t0:.2f}s")

        # ── Loop over city dirs and their files ───────────────────────────────
        for city_name, city_files in city_entries:
          for city_file in city_files:
            print(f"\n{'='*65}")
            print(f"Processando: {city_file.name}")
            print("=" * 65)

            df_en_raw = pd.read_csv(city_file)
            pconv_0   = df_en_raw["Pconv"].values.copy()
            df_ts     = df_en_raw[["TS", "Time"]].copy()

            if len(pconv_0) != TOTAL_TIMESLOTS:
                print(f"[aviso] CSV tem {len(pconv_0)} timeslots (esperado {TOTAL_TIMESLOTS}). Pulando.")
                continue

            power_ev     = greedy_ev_demand(fleet, TOTAL_TIMESLOTS)

            if EV_PCONV_SCALE_PP and pp_file.stem == EV_PCONV_SCALE_PP:
                _scale = float(power_ev.max()) / (EV_PCONV_SHARE * float(pconv_0.max()))
                pconv_0 = pconv_0 * _scale
                print(f"[escala] Pconv × {_scale:.4f} → EV_peak = {EV_PCONV_SHARE*100:.0f}% Pconv_peak")

            upper_pc_s3  = float((pconv_0 + power_ev).max())   # S3: carga total

            inst_name   = build_instance_name(pp_file, city_file)
            print(inst_name)
            results_dir = build_results_path(city_name, scenario_name, inst_name)
            results_dir = ensure_results_dir(results_dir)

            # ── Stage 002 — initial figures ───────────────────────────────────
            plot_initial_profile(pconv_0, power_ev, results_dir, n_evs)

            pconv_stages   = {"Inicial": pconv_0.copy()}
            energy_current = energy_adj.copy()

            # ── S1 ────────────────────────────────────────────────────────────
            print(f"\n{'='*65}")
            print("ESTÁGIO 1 — Descarga V2G (peak-shaving)")
            print("=" * 65)
            t0 = time.perf_counter()
            pconv_s1, _, alloc_s1, res_s1 = stage1(
                pconv_0, avail, discharge_cap, results_dir, K=N_ITER_S1, n_total=n_evs)
            print(f"[S1] Tempo total: {time.perf_counter()-t0:.2f}s")

            energy_current -= alloc_s1.sum(axis=1) * ENERGY_PER_SLOT_KWH
            soc_s1 = energy_current / capacity * 100
            pconv_stages["Após S1 (V2G)"] = pconv_s1.copy()

            # ── S2+S3 unificado (preenchimento LCVF real, ver stage_charging_unified) ──
            # Não há mais uma fase de carga mínima separada — "Após S2" abaixo
            # reflete o mesmo estado de "Após S1" (ver cabeçalho da função).
            print(f"\n{'='*65}")
            print("ESTÁGIO 2+3 — Carga até 90% SoC (preenchimento LCVF unificado)")
            print("=" * 65)
            energy_to_min_now = np.maximum(0.0, soc_min_arr / 100 * capacity - energy_current)
            n_needs_s2 = int((energy_to_min_now > TOLERANCE).sum())
            print(f"EVs que precisam de carga mínima: {n_needs_s2:,}")

            pconv_s2 = pconv_s1.copy()
            alloc_s2 = np.zeros_like(alloc_s1)
            soc_s2   = soc_s1.copy()
            pconv_stages["Após S2 (mín)"] = pconv_s2.copy()

            energy_to_full_now = np.maximum(0.0, capacity * SOC_FULL_TARGET - energy_current)
            print(f"Energia total a alocar (S2+S3): {energy_to_full_now.sum():.0f} kWh")

            priority_key = -starts.astype(float)   # chegada mais tarde tem prioridade (LCVF real)

            t0 = time.perf_counter()
            pconv_s3, _, alloc_s3, res_s3 = stage_charging_unified(
                "S3", pconv_s1, avail, energy_to_full_now, alloc_s1,
                upper_pc=upper_pc_s3, out_dir=results_dir, K=N_ITER_S3,
                priority_key=priority_key, ref_pconv_orig=pconv_0, n_total=n_evs)
            print(f"[S2+S3] Tempo total: {time.perf_counter()-t0:.2f}s")

            energy_current = np.minimum(
                energy_current + alloc_s3.sum(axis=1) * ENERGY_PER_SLOT_KWH, capacity)
            soc_s3 = energy_current / capacity * 100
            n_soc_min_ok = int((soc_s3 >= soc_min_arr - 0.01).sum())
            n_full = int((soc_s3 >= 99.9).sum())
            print(f"\n[S2+S3] EVs com soc_min satisfeito: {n_soc_min_ok:,}/{n_evs:,}")
            print(f"[S2+S3] Pico resultante: {pconv_s3.max():.3f} kW")
            print(f"[S2+S3] EVs com carga completa: {n_full:,}/{n_evs:,}")
            pconv_stages["Após S3 (máx)"] = pconv_s3.copy()

            # ── Summary ───────────────────────────────────────────────────────
            soc_stages = {
                "Inicial":       soc_current,
                "Após S1 (V2G)": soc_s1,
                "Após S2 (mín)": soc_s2,
                "Após S3 (máx)": soc_s3,
            }
            summary = compute_summary(fleet, pconv_stages, soc_stages)

            print("\n=== RESUMO FINAL ===")
            print(summary.to_string(index=False))

            # ── Figures ───────────────────────────────────────────────────────
            # y-axis floor matches lcvf_fast_v2g.py's convention (min(Pconv) *
            # 0.9) so the two pipelines' figures share the same vertical scale.
            plot_stages(pconv_stages, results_dir, n_evs, y_min=pconv_0.min() * 0.9,
                        summary=summary)
            plot_soc(soc_current, soc_s3, soc_min_arr, results_dir, n_evs)

            # ── Save CSVs ─────────────────────────────────────────────────────
            summary.to_csv(results_dir / "summary_3stage.csv", index=False)
            res_s1.to_csv(results_dir / "results_s1_discharge.csv", index=False)
            # Não há mais uma fase S2 separada (preenchimento unificado, ver
            # stage_charging_unified) — arquivo mantido vazio (mesmas colunas
            # de res_s3) só por compatibilidade de schema, não inventa dado.
            pd.DataFrame(columns=res_s3.columns).to_csv(
                results_dir / "results_s2_mincharge.csv", index=False)
            res_s3.to_csv(results_dir / "results_s3_fullcharge.csv", index=False)

            en_final = df_ts.copy()
            en_final["power_conventional_kW"] = pconv_s3.round(DECIMAL_DIGITS)
            en_final.to_csv(results_dir / "en_profile_3stage_final.csv", index=False)

            # Per-timeslot energy allocated by each stage (kWh/TS), for
            # cross-pipeline comparison against lcvf_fast_v2g.py's PHacc2.
            # alloc_sX is the (n_evs, TOTAL_TIMESLOTS) bool matrix of which
            # EV was active in which slot for that stage; summing over EVs
            # and scaling by ENERGY_PER_SLOT_KWH gives kWh delivered (S2/S3)
            # or discharged (S1) in that slot.
            profile_all_stages = df_ts.copy()
            profile_all_stages["Pconv_inicial"]     = pconv_0.round(DECIMAL_DIGITS)
            profile_all_stages["Pconv_apos_S1"]     = pconv_s1.round(DECIMAL_DIGITS)
            profile_all_stages["Pconv_apos_S2"]     = pconv_s2.round(DECIMAL_DIGITS)
            profile_all_stages["Pconv_apos_S3"]     = pconv_s3.round(DECIMAL_DIGITS)
            profile_all_stages["Energia_S1_discharge_kWh"] = (
                alloc_s1.sum(axis=0) * ENERGY_PER_SLOT_KWH).round(DECIMAL_DIGITS)
            profile_all_stages["Energia_S2_mincharge_kWh"] = (
                alloc_s2.sum(axis=0) * ENERGY_PER_SLOT_KWH).round(DECIMAL_DIGITS)
            profile_all_stages["Energia_S3_fullcharge_kWh"] = (
                alloc_s3.sum(axis=0) * ENERGY_PER_SLOT_KWH).round(DECIMAL_DIGITS)
            profile_all_stages.to_csv(results_dir / "en_profile_all_stages.csv", index=False)

            for f in results_dir.glob("*.csv"):
                print(f"[salvo] {f.name}")

    elapsed_total = time.perf_counter() - t_global
    print(f"\n{'='*65}")
    print(f"Concluído em {elapsed_total:.1f}s")
    print("=" * 65)


if __name__ == "__main__":
    main()
