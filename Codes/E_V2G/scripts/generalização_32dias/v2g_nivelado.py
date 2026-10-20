#!/usr/bin/env python3
"""
CÓPIA CONGELADA de Codes/E_V2G/scripts/v2g_nivelado.py (commit ef994364 + correções de 2026-10-03), usada
só pela generalização de 32 dias (run_generalizacao.py). Caminhos ajustados para esta pasta e
saídas padrão em Results/V2G/CampoGrande/generalization/. Correções no pipeline principal não
chegam aqui sozinhas: repita-as e registre na docs/13.

v2g_nivelado.py — V2G 3 estágios com S2+S3 nivelado.

Mesmo pipeline de `v2g_stable.py` (S1 descarga sequencial, figuras e CSVs de
saída idênticos), trocando apenas o preenchimento S2+S3 unificado por:

1. Critério de Flag descontando o PISO ESTRUTURAL — energia que nenhum teto
   aloca (EVs com janela curta demais), calculada num passe sem teto com o
   mesmo `blocked` do S1. Em `v2g_stable.stage_charging_unified` o Flag só
   vira 1 com `unalloc < TOLERANCE`; com piso > 0 isso nunca acontece, a
   bissecção só sobe o teto e a 1ª tentativa (teto alto demais) fica
   congelada no perfil (serrilhado). O LCVF real (`lcvf_fast_v2g.py`,
   `structural_floor_total`) já desconta esse piso.
2. Pós-processamento de NIVELAMENTO (`level_charging`): move recarga de cada
   EV do slot mais carregado para o menos carregado da própria janela
   (energia por EV preservada, nunca em slot de descarga, pico nunca sobe).

Diagnóstico e teste (bld_s4 × 39.000, 2026-10-01): pico 107.414 → 92.373 kW
(gap vs LCVF +13,84% → −2,10%), oscilação média slot a slot 7.053 → 411 kW.

Reaproveita `v2g_stable` por import (fonte única do S1, figuras e CSVs) e
grava em Results/V2G/<cidade>/v2g_nivelado/<cenário>/<instância>/, sem
sobrescrever a produção. Roda todas as instâncias de Campo Grande:
bld_s1 nas 5 estações × 5 tamanhos + demais frotas no baseline.

Uso: python3 v2g_nivelado.py
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd

import v2g_stable as vs

SAVE_ITER_FIGS = False   # figuras por iteração do S1 (o S2+S3 nivelado não as gera)
RESULTS_SUBDIR = "v2g_nivelado"

_PP_BLD_S1 = [f"pp_{n}_bld_s1.csv" for n in (3000, 9000, 18000, 39000, 50336)]
_PP_OUTROS = (
    [f"pp_{n}_bld_s{s}.csv" for s in (2, 3, 4, 5, 6) for n in (3000, 9000, 18000, 39000, 50336)]
    + [f"pp_{n}_nor_s1.csv" for n in (3000, 9000, 18000, 32330)]
    + [f"pp_{n}_wscc.csv" for n in (3000, 9000, 18000, 39000, 50336)]
)


def stage_charging_floor_aware(pconv_0, avail, energy_rem, blocked, upper_pc, K, priority_key):
    # piso estrutural: o que sobra num passe sem teto
    _, _, _, _, floor = vs._run_lcvf_style_iter(
        pconv_0, avail, energy_rem, blocked, 1e12, priority_key)
    Upper_Pc, Lower_Pc = upper_pc, float(pconv_0.min())
    pconv_acc, energy_acc = pconv_0.copy(), energy_rem.copy()
    alloc_acc = np.zeros_like(blocked, dtype=bool)
    rows = []
    for j in range(K):
        Pc = (Upper_Pc + Lower_Pc) / 2
        pconv_new, energy_new, _, alloc_trial, unalloc = vs._run_lcvf_style_iter(
            pconv_acc, avail, energy_acc, blocked | alloc_acc, Pc, priority_key)
        Flag = 1 if unalloc - floor < vs.TOLERANCE else 0
        rows.append(dict(Iteration=j + 1, Pc=round(Pc, 3), Unallocated_kWh=round(unalloc, 2),
                         Floor_kWh=round(floor, 2), Flag=Flag))
        if Flag == 1:
            Upper_Pc = Pc
        else:
            Lower_Pc = Pc
            pconv_acc, energy_acc = pconv_new, energy_new
            alloc_acc = alloc_acc | alloc_trial
    # fecha a alocação: a última tentativa aceita pode ter deixado energia
    # alocável pra trás; um passe final no último teto Flag=1 (Upper_Pc) a completa
    pconv_fin, energy_fin, _, alloc_fin, unalloc = vs._run_lcvf_style_iter(
        pconv_acc, avail, energy_acc, blocked | alloc_acc, Upper_Pc, priority_key)
    alloc_acc = alloc_acc | alloc_fin
    rows.append(dict(Iteration="final", Pc=round(Upper_Pc, 3), Unallocated_kWh=round(unalloc, 2),
                     Floor_kWh=round(floor, 2), Flag=int(unalloc - floor < vs.TOLERANCE)))
    return pconv_fin, energy_fin, alloc_acc, pd.DataFrame(rows), floor


def level_charging(pconv_base, alloc, avail, blocked):
    """Pós-processamento de nivelamento (amortiza o serrilhado): move unidades
    de recarga de um EV do slot mais carregado `hi` para um slot menos
    carregado `lo` da MESMA janela do EV (disponível, sem descarga e sem
    recarga já alocada ali). Cada movimento preserva a energia do EV e só
    ocorre se load[lo] + k·P <= load[hi] − k·P, então o pico nunca sobe.

    Vetorizado (2026-10-01; versão anterior, laço duplo hi×lo, levava ~5 min
    em 39.000 EVs, esta ~0,6 s com o mesmo pico): para o slot mais
    carregado `hi`, conta de uma vez (sobre só os EVs que carregam em `hi`)
    quantos EVs podem migrar para CADA slot `lo`, e escolhe o `lo` de menor
    carga com candidatos. Slots sem movimento possível ficam marcados como
    travados até voltarem a receber carga (só então podem destravar)."""
    P = vs.CHARGER_POWER_KW
    alloc = alloc.copy()
    free = avail & ~blocked
    load = pconv_base + alloc.sum(axis=0) * P
    stuck = np.zeros(load.shape[0], dtype=bool)
    n_moves = 0
    while not stuck.all():
        hi = int(np.argmax(np.where(stuck, -np.inf, load)))
        rows = np.flatnonzero(alloc[:, hi])
        if rows.size == 0:
            stuck[hi] = True
            continue
        movable = free[rows] & ~alloc[rows]                 # (n_rows, T)
        counts = movable.sum(axis=0)
        ok = (counts > 0) & (load < load[hi] - 2 * P)
        if not ok.any():
            stuck[hi] = True
            continue
        lo = int(np.argmin(np.where(ok, load, np.inf)))
        k = min(int((load[hi] - load[lo]) // (2 * P)), int(counts[lo]))
        sel = rows[np.flatnonzero(movable[:, lo])[:k]]
        alloc[sel, hi] = False
        alloc[sel, lo] = True
        load[hi] -= k * P
        load[lo] += k * P
        stuck[lo] = False
        n_moves += k
    return load, alloc, n_moves


def stage_charging_nivelado(label, pconv_0, avail, energy_rem, blocked, upper_pc,
                            out_dir, K, priority_key, ref_pconv_orig=None, n_total=0):
    """Substitui `vs.stage_charging_unified` (mesma assinatura e retorno):
    bissecção com piso estrutural + nivelamento."""
    _, energy_fin, alloc, res, floor = stage_charging_floor_aware(
        pconv_0, avail, energy_rem, blocked, upper_pc, K, priority_key)
    peak_pre = float((pconv_0 + alloc.sum(axis=0) * vs.CHARGER_POWER_KW).max())
    t0 = time.perf_counter()
    load, alloc, n_moves = level_charging(pconv_0, alloc, avail, blocked)
    print(f"[{label}] piso estrutural={floor:.2f} kWh | pico antes do nivelamento="
          f"{peak_pre:.1f} → depois={load.max():.1f} kW | {n_moves:,} movimentos "
          f"em {time.perf_counter() - t0:.2f}s")
    res = pd.concat([res, pd.DataFrame([dict(Iteration="nivelamento", Pc=round(float(load.max()), 3),
                                             Unallocated_kWh=res.Unallocated_kWh.iloc[-1],
                                             Floor_kWh=round(floor, 2), Flag=1)])],
                    ignore_index=True)
    return load, energy_fin, alloc, res


def _results_path(city_name, scenario_name, inst_name):
    return vs.path_root / "Results" / "V2G" / city_name / "generalization" / RESULTS_SUBDIR / scenario_name / inst_name


def main():
    vs.stage_charging_unified = stage_charging_nivelado
    vs.build_results_path = _results_path
    vs.SAVE_ITER_FIGS = SAVE_ITER_FIGS
    vs.INTERACTIVE_MENU = False
    vs.CITY_DIR_CHOICES = [1]            # só Campo Grande
    t0 = time.perf_counter()
    for pp_filter, all_seasons in [(_PP_BLD_S1, True), (_PP_OUTROS, False)]:
        vs.PP_FILE_FILTER = pp_filter
        vs.RUN_ALL_CITY_FILES = all_seasons
        vs.main()
    print(f"\n[v2g_nivelado] concluído em {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
