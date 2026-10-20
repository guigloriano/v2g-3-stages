#!/usr/bin/env python3
"""
adicionar_wrap_end_ts.py — acrescenta a coluna wrap_end_ts (dia periódico) aos pp_*.csv já gerados,
sem refazer o pré-processamento (e, portanto, sem novo sorteio de baterias, SoC ou janelas).

Mesma regra de `Codes/A_PreProcessing/08 - v2g_preprocessing.py` (2026-10-08): uma sessão que
passa da meia-noite nos dados (session_overnight) continua nos primeiros slots do dia, até o slot
anterior à chegada; wrap_end_ts é o último desses slots (0 = sem continuação). A duração usada é a
do pré-processamento: a bruta (session_dur_slots_raw) ou, se menor que o necessário no pior caso,
a estendida por EXTEND_WINDOW_TO_FEASIBLE. Antes de gravar, confere que essa duração reproduz o
end_ts de cada EV (min(start + dur - 1, 144) == end_ts) e para se não reproduzir.

Uso: python3 adicionar_wrap_end_ts.py <pp_*.csv> [...]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

N_TS, ENERGY_PER_SLOT_KWH, ALVO = 144, 7.0 / 6, 0.90

for arq in map(Path, sys.argv[1:]):
    d = pd.read_csv(arq)
    st = d.start_ts.values.astype(int)
    bruta = np.maximum(d.session_dur_slots_raw.values.astype(int), 1)
    e12 = np.abs(d.energy_adjusted_kWh.values - d.energy_min_kWh.values)
    e3 = np.maximum(0.0, d.battery_capacity_kWh.values * ALVO - d.energy_min_kWh.values)
    precisa = np.ceil((e12 + e3) / ENERGY_PER_SLOT_KWH).astype(int)
    dur = np.where(bruta < precisa, precisa, bruta)
    errado = np.minimum(st + dur - 1, N_TS) != d.end_ts.values
    if errado.any():
        sys.exit(f"[erro] {arq.name}: a duração não reproduz end_ts em {int(errado.sum())} EVs")
    overnight = d.session_overnight.astype(str).str.lower().eq("true").values
    d["wrap_end_ts"] = np.where(overnight, np.clip(st + dur - 1 - N_TS, 0, st - 1), 0).astype(int)
    d["can_fully_charge"] = (d.end_ts - d.start_ts + 1 + d.wrap_end_ts) >= d.ts_to_full
    d.to_csv(arq, index=False)
    w = d.wrap_end_ts.values
    print(f"{arq.name}: {(w > 0).mean():.1%} dos EVs com continuação na madrugada "
          f"(mediana {np.median(w[w > 0]) if (w > 0).any() else 0:.0f} slots)")
