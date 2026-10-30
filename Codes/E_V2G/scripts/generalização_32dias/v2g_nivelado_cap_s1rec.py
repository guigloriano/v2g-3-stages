#!/usr/bin/env python3
"""
v2g_nivelado_cap_s1rec.py — Stage 1 "consciente da recuperação" + recarga com capacidade em todos os slots
(revisão interna R4/N20, 2026-10-29).

Com a capacidade respeitada em todos os slots (v2g_nivelado_cap), a completude caiu (mediana de 7,8% dos EVs
abaixo de 90%, contra 0,5% antes): o Stage 1 descarregava veículos que, depois, não têm como voltar a 90% — SoC
alto na chegada (pouca cota de recarga antes da descarga) e janela que termina logo depois do pico.

Mudança (só no Stage 1; cópia de v2g_stable.stage1): um EV só é elegível para descarregar mais um slot se, com a
descarga já atribuída mais este slot, ainda couber na própria janela a recarga até a meta de 90%:

    min(cota_i, slots livres antes do último bloco de descarga) + slots livres depois dele  >=  slots necessários

com cota_i = floor((B_i - E_i^0)/slot) (a mesma regra de capacidade de v2g_nivelado_cap) e slots necessários =
ceil((0,9 B_i - energia depois da descarga)/slot). É uma condição de janela, não de pico: na disputa por slots de
recarga o EV ainda pode ficar abaixo da meta. O resto do Stage 1 (descida flat-top, bloco contíguo, ordem da frota)
e a recarga (v2g_nivelado_cap.stage_charging_nivelado_cap) não mudam.

Uso: v2g_nivelado_cap.preparar(fleet); v2g_stable.stage1 = stage1_rec;
     v2g_stable.stage_charging_unified = v2g_nivelado_cap.stage_charging_nivelado_cap   (ver run_v2g_cap_s1rec.py)
"""
import time

import numpy as np
import pandas as pd

import v2g_stable as vs
import v2g_nivelado_cap as vc


def _folga_ok(c, H, run_start, run_end, nd, pos, L, cota, e0, cap):
    """Elegibilidade de recuperação dos candidatos c para descarregar no slot H."""
    E = vs.ENERGY_PER_SLOT_KWH
    tem = run_start[c] >= 0
    rs = np.where(tem, np.minimum(run_start[c], H), H)
    re = np.where(tem, np.maximum(run_end[c], H), H)
    prs, pre_ = pos[c, rs], pos[c, re]
    unico = pre_ >= prs                                   # bloco único na ordem da sessão
    antes = np.where(unico, prs, prs - pre_ - 1)          # sessão de 24 h partida: o que fica entre os dois blocos
    depois = np.where(unico, L[c] - 1 - pre_, 0)
    e_dep = e0[c] - (nd[c] + 1) * E
    precisa = np.maximum(0, np.ceil((vs.SOC_FULL_TARGET * cap[c] - e_dep) / E - 1e-9)).astype(int)
    return np.minimum(cota[c], antes) + depois >= precisa


def stage1_rec(pconv_0, avail, discharge_cap, out_dir, K=vs.N_ITER_S1, n_total=0):
    """v2g_stable.stage1 com o filtro de recuperação (mesma assinatura e retorno)."""
    E, P = vs.ENERGY_PER_SLOT_KWH, vs.CHARGER_POWER_KW
    t0 = time.perf_counter()
    n_evs_flt = len(discharge_cap)
    n_ts = pconv_0.shape[0]
    pos = vc.ordem_sessao(n_ts)
    L = avail.sum(axis=1)
    cota = vc.cota_pre()
    e0, cap = vc.CTX["e0"], vc.CTX["cap"]

    Upper_Pc = float(pconv_0.max())
    can_per_slot = (avail & (discharge_cap[:, None] >= E)).sum(axis=0)
    Lower_Pc_slot = float(np.maximum(pconv_0 - can_per_slot * P, 0.0).max())
    E_fleet = float(discharge_cap.sum())
    lo_e, hi_e = float(pconv_0.min()), Upper_Pc
    for _ in range(64):
        mid_e = (lo_e + hi_e) / 2
        if float(np.maximum(0.0, pconv_0 - mid_e).sum()) * vs.TS_HOURS > E_fleet:
            lo_e = mid_e
        else:
            hi_e = mid_e
    ev_slot_budget = int(np.floor(discharge_cap / E).sum())
    lo_s, hi_s = float(pconv_0.min()), Upper_Pc
    for _ in range(64):
        mid_s = (lo_s + hi_s) / 2
        if int(np.floor(np.maximum(0.0, pconv_0 - mid_s) / P).sum()) > ev_slot_budget:
            lo_s = mid_s
        else:
            hi_s = mid_s
    Lower_Pc = max(Lower_Pc_slot, hi_e, hi_s)
    step = (Upper_Pc - Lower_Pc) / K
    print(f"[S1 rec] Upper_Pc={Upper_Pc:.1f}  Lower_Pc={Lower_Pc:.1f}  step={step:.1f} kW/iter")

    pconv_curr = pconv_0.copy()
    reserve = discharge_cap.copy()
    alloc = np.zeros((n_evs_flt, n_ts), dtype=bool)
    run_start = np.full(n_evs_flt, -1, dtype=int)
    run_end = np.full(n_evs_flt, -1, dtype=int)
    nd = np.zeros(n_evs_flt, dtype=int)
    barrados = 0
    for j in range(K):
        Pc_target = max(Upper_Pc - (j + 1) * step, Lower_Pc)
        for H in np.argsort(pconv_curr)[::-1]:
            if pconv_curr[H] <= Pc_target:
                break
            n_needed = int((pconv_curr[H] - Pc_target) / P)
            if n_needed == 0:
                continue
            has_run = run_start >= 0
            adjacent = (H == run_start - 1) | (H == run_end + 1)
            can = avail[:, H] & (reserve >= E) & ~alloc[:, H] & (~has_run | adjacent)
            c = np.flatnonzero(can)
            if c.size:
                ok = _folga_ok(c, H, run_start, run_end, nd, pos, L, cota, e0, cap)
                barrados += int((~ok).sum())
                c = c[ok]
            ev_H = c[:n_needed]
            reserve[ev_H] -= E
            alloc[ev_H, H] = True
            nd[ev_H] += 1
            novo = run_start[ev_H] < 0
            run_start[ev_H] = np.where(novo, H, np.minimum(run_start[ev_H], H))
            run_end[ev_H] = np.where(novo, H, np.maximum(run_end[ev_H], H))
            pconv_curr[H] -= len(ev_H) * P
        print(f"  Iter {j+1:2d} | Target={Pc_target:8.1f} | Pico={pconv_curr.max():8.1f} | "
              f"{int((nd > 0).sum()):,} EVs")

    elapsed = round(time.perf_counter() - t0, 2)
    n_disch = int((nd > 0).sum())
    en_disch = float(alloc.sum() * E)
    print(f"\n[S1 rec] Pico original: {pconv_0.max():.3f} kW  →  após S1: {pconv_curr.max():.3f} kW | "
          f"EVs descarregaram: {n_disch:,} | Energia: {en_disch:.1f} kWh | candidaturas barradas: {barrados:,} | {elapsed:.2f}s")
    results = pd.DataFrame([{"Iteration": K, "Pf_resultante_kW": round(float(pconv_curr.max()), 3),
                             "Energia_descarga_kWh": round(en_disch, 2), "EVs_descarregaram": n_disch,
                             "ExecTime": elapsed}])
    return pconv_curr, reserve, alloc, results
