#!/usr/bin/env python3
"""
v2g_nivelado_cap.py — V2G nivelado com o limite de capacidade em TODOS os slots (revisão interna R4/N20, 2026-10-29).

Problema (verificar_soc_intermediario.py, 768 instâncias): o S2+S3 dimensiona a recarga de cada EV pelo déficit
pós-S1 e só impõe a capacidade na saída. O LCVF e o nivelamento podem pôr recarga ANTES da sessão de descarga
(num vale da madrugada, p. ex.), e a energia do EV passava de B_i no meio da janela em todas as instâncias
(mediana de 14,7% da frota, excesso de até 42,7% de B_i).

Correção: a descarga de cada EV é uma única sessão contígua no relógio (S1), então a energia só pode passar de B_i antes
de um bloco de descarga; depois do último bloco a trajetória sobe até a energia de saída, limitada pelo alvo de 90%.
Basta limitar a recarga posicionada ANTES do último bloco de descarga (na ordem da sessão, inclusive a continuação
na madrugada; numa sessão de 24 h da N1 a descarga pode ficar em dois blocos) a uma cota de slots por EV:

    cota_i = floor((B_i - E_i^0) / ENERGY_PER_SLOT_KWH)

A cota é respeitada:
  1. no passe LCVF (`_passe_lcvf_cap`, cópia de v2g_stable._run_lcvf_style_iter com a cota): o EV que esgota a
     cota deixa de ser candidato nos slots anteriores à sua descarga, e o pdem desses slots é descontado;
  2. na bissecção com piso estrutural (`_floor_aware_cap`, cópia de v2g_nivelado.stage_charging_floor_aware):
     cada tentativa parte da cota que sobra depois da alocação já aceita;
  3. no nivelamento (`_nivelar_cap`, cópia de v2g_nivelado.level_charging): um movimento que levaria recarga de
     depois para antes da descarga só ocorre se o EV ainda tem cota.
Ao fim, `_conferir` monta a trajetória E_{i,t} e falha se algum EV passar de B_i em algum slot.

EVs sem descarga e a parte depois da descarga seguem exatamente o pipeline original. Os módulos congelados
(v2g_stable.py, v2g_nivelado.py) não mudam; este módulo só é usado por run_v2g_cap.py, que grava em
Results/V2G/CampoGrande/generalization_cap/ (nunca nos resultados publicados).

Uso: `preparar(fleet)` com a frota da instância e `v2g_stable.stage_charging_unified = stage_charging_nivelado_cap`.
"""
import json
import time

import numpy as np
import pandas as pd

import v2g_stable as vs

CTX = {}  # dados por EV da instância corrente (preparar)
TOL_KWH = 1e-6


def preparar(fleet):
    """Guarda os dados por EV que o S2+S3 não recebe na assinatura original."""
    CTX.clear()
    CTX.update(e0=fleet["energy_adjusted_kWh"].values.astype(float),
               cap=fleet["battery_capacity_kWh"].values.astype(float),
               emin=(fleet["soc_min_pct"].values / 100 * fleet["battery_capacity_kWh"].values).astype(float),
               starts=fleet["start_ts"].values.astype(int), ends=fleet["end_ts"].values.astype(int),
               wrap=vs.wrap_end_of(fleet))


def ordem_sessao(T):
    """pos[i, t] = posição do slot t (0-based) na sessão do EV i (start..end, depois 1..wrap_end); -1 fora."""
    ts = np.arange(1, T + 1)[None, :]
    s, e, w = CTX["starts"][:, None], CTX["ends"][:, None], CTX["wrap"][:, None]
    return np.where((s <= ts) & (ts <= e), ts - s, np.where(ts <= w, (T - s + 1) + ts - 1, -1))


def mascara_pre(alloc_s1):
    """pre[i, t]: slot da janela do EV i anterior ao início do ÚLTIMO bloco de descarga, na ordem da sessão
    (False se não descarrega).

    A descarga do S1 é contígua no relógio, mas numa sessão de 24 h da N1 (chega e sai no mesmo horário do dia
    periódico) ela pode ficar partida em dois blocos na ordem da sessão: um no começo e outro no fim. A energia
    pode passar de B_i antes de cada bloco; limitar a recarga antes do último bloco à cota (B_i - E_i^0)/slot
    cobre os dois, porque a recarga antes do primeiro é parte dela (critério conservador: não credita a energia
    já descarregada no primeiro bloco)."""
    T = alloc_s1.shape[1]
    pos = ordem_sessao(T)
    ordem = np.argsort(np.where(pos < 0, 10 * T, pos), axis=1, kind="stable")
    dis = np.take_along_axis(alloc_s1, ordem, axis=1)                  # descarga na ordem da sessão
    inicio_bloco = dis & ~np.concatenate([np.zeros((dis.shape[0], 1), bool), dis[:, :-1]], axis=1)
    ultimo = np.where(inicio_bloco, np.arange(T)[None, :], -1).max(axis=1)   # -1: não descarrega
    return (pos >= 0) & (pos < ultimo[:, None])


def cota_pre():
    return np.maximum(0, np.floor((CTX["cap"] - CTX["e0"]) / vs.ENERGY_PER_SLOT_KWH + 1e-9)).astype(int)


def _passe_lcvf_cap(pconv_base, avail, energy_base, blocked, Pc, priority_key, pre, sobra):
    """v2g_stable._run_lcvf_style_iter + cota de recarga antes da descarga (sobra = cota ainda disponível)."""
    P, E, TOL = vs.CHARGER_POWER_KW, vs.ENERGY_PER_SLOT_KWH, vs.TOLERANCE
    n_ts = pconv_base.shape[0]
    pconv = pconv_base.copy()
    energy = energy_base.copy()
    sobra = sobra.copy()
    ph_acc = np.zeros(n_ts)
    alloc_new = np.zeros_like(blocked, dtype=bool)

    can0 = avail & ~blocked
    esgotado = sobra <= 0
    can0[esgotado] &= ~pre[esgotado]
    active = energy > TOL
    pdem = (can0 & active[:, None]).sum(axis=0).astype(float) * P
    psup = np.maximum(0.0, Pc - pconv)

    remaining = np.ones(n_ts, dtype=bool)
    n_remaining = n_ts
    while n_remaining > 0:
        with np.errstate(divide="ignore", invalid="ignore"):
            index_ts = np.where(remaining & (pdem > 0), psup / pdem, -np.inf)
        H = int(np.argmax(index_ts))

        cand_mask = can0[:, H] & (energy > TOL) & ~alloc_new[:, H]
        cand_idx = np.where(cand_mask)[0]

        if cand_idx.size > 0:
            cand_idx = cand_idx[np.argsort(priority_key[cand_idx])]
            n_can = int(psup[H] // P)
            if n_can > 0:
                alloc_idx = cand_idx[:n_can]
                alloc_new[alloc_idx, H] = True
                ph_acc[H] += len(alloc_idx) * P
                pconv[H] += len(alloc_idx) * P
                psup[H] -= len(alloc_idx) * P

                energy[alloc_idx] -= E
                drained = energy[alloc_idx] <= TOL
                if drained.any():
                    drained_idx = alloc_idx[drained]
                    energy[drained_idx] = 0.0
                    pdem -= (can0[drained_idx, :].sum(axis=0)) * P

                # cota de recarga antes da descarga
                no_pre = alloc_idx[pre[alloc_idx, H]]
                if no_pre.size:
                    sobra[no_pre] -= 1
                    esg = no_pre[(sobra[no_pre] <= 0) & (energy[no_pre] > TOL)]  # os esvaziados já saíram do pdem
                    if esg.size:
                        tira = can0[esg] & pre[esg]
                        pdem -= tira.sum(axis=0) * P
                        can0[esg] &= ~pre[esg]

        remaining[H] = False
        n_remaining -= 1

    unallocated = float(energy.clip(min=0).sum())
    return pconv, energy, ph_acc, alloc_new, unallocated


def _floor_aware_cap(pconv_0, avail, energy_rem, blocked, upper_pc, K, priority_key, pre, cota):
    """v2g_nivelado.stage_charging_floor_aware com a cota (blocked = alocação do S1)."""
    sobra0 = cota.copy()
    _, _, _, _, floor = _passe_lcvf_cap(pconv_0, avail, energy_rem, blocked, 1e12, priority_key, pre, sobra0)
    Upper_Pc, Lower_Pc = upper_pc, float(pconv_0.min())
    pconv_acc, energy_acc = pconv_0.copy(), energy_rem.copy()
    alloc_acc = np.zeros_like(blocked, dtype=bool)
    rows = []
    for j in range(K):
        Pc = (Upper_Pc + Lower_Pc) / 2
        sobra = cota - (alloc_acc & pre).sum(axis=1)
        pconv_new, energy_new, _, alloc_trial, unalloc = _passe_lcvf_cap(
            pconv_acc, avail, energy_acc, blocked | alloc_acc, Pc, priority_key, pre, sobra)
        Flag = 1 if unalloc - floor < vs.TOLERANCE else 0
        rows.append(dict(Iteration=j + 1, Pc=round(Pc, 3), Unallocated_kWh=round(unalloc, 2),
                         Floor_kWh=round(floor, 2), Flag=Flag))
        if Flag == 1:
            Upper_Pc = Pc
        else:
            Lower_Pc = Pc
            pconv_acc, energy_acc = pconv_new, energy_new
            alloc_acc = alloc_acc | alloc_trial
    sobra = cota - (alloc_acc & pre).sum(axis=1)
    pconv_fin, energy_fin, _, alloc_fin, unalloc = _passe_lcvf_cap(
        pconv_acc, avail, energy_acc, blocked | alloc_acc, Upper_Pc, priority_key, pre, sobra)
    alloc_acc = alloc_acc | alloc_fin
    rows.append(dict(Iteration="final", Pc=round(Upper_Pc, 3), Unallocated_kWh=round(unalloc, 2),
                     Floor_kWh=round(floor, 2), Flag=int(unalloc - floor < vs.TOLERANCE)))
    return pconv_fin, energy_fin, alloc_acc, pd.DataFrame(rows), floor


def _nivelar_cap(pconv_base, alloc, avail, blocked, pre, cota):
    """v2g_nivelado.level_charging com a cota: mover recarga de depois para antes da descarga só com cota."""
    P = vs.CHARGER_POWER_KW
    alloc = alloc.copy()
    free = avail & ~blocked
    usado = (alloc & pre).sum(axis=1)
    load = pconv_base + alloc.sum(axis=0) * P
    stuck = np.zeros(load.shape[0], dtype=bool)
    n_moves = 0
    while not stuck.all():
        hi = int(np.argmax(np.where(stuck, -np.inf, load)))
        rows = np.flatnonzero(alloc[:, hi])
        if rows.size == 0:
            stuck[hi] = True
            continue
        entra_pre = pre[rows] & ~pre[rows, hi][:, None]           # o movimento aumentaria a recarga pré-descarga
        sem_cota = (usado[rows] >= cota[rows])[:, None]
        movable = free[rows] & ~alloc[rows] & ~(entra_pre & sem_cota)
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
        usado[sel] += pre[sel, lo].astype(int) - pre[sel, hi].astype(int)
        load[hi] -= k * P
        load[lo] += k * P
        stuck[lo] = False
        n_moves += k
    return load, alloc, n_moves


def _conferir(alloc_s1, alloc_s3):
    """Trajetória E_{i,t} na ordem da sessão; falha se algum EV passar de B_i."""
    T = alloc_s1.shape[1]
    pos = ordem_sessao(T)
    ordem = np.argsort(np.where(pos < 0, 10 * T, pos), axis=1, kind="stable")
    delta = (alloc_s3.astype(float) - alloc_s1.astype(float)) * vs.ENERGY_PER_SLOT_KWH
    traj = CTX["e0"][:, None] + np.cumsum(np.take_along_axis(np.where(pos < 0, 0.0, delta), ordem, axis=1), axis=1)
    dis = np.take_along_axis(alloc_s1, ordem, axis=1)
    excesso = traj.max(axis=1) - CTX["cap"]
    falta_min = np.where(dis, CTX["emin"][:, None] - traj, -np.inf).max(axis=1)
    r = dict(n_evs=int(len(excesso)), n_acima_cap=int((excesso > TOL_KWH).sum()),
             excesso_max_kWh=round(float(max(excesso.max(), 0)), 6),
             n_abaixo_min_na_descarga=int((falta_min > TOL_KWH).sum()),
             falta_min_max_kWh=round(float(max(falta_min.max(), 0)), 6),
             n_negativo=int((traj < -TOL_KWH).any(axis=1).sum()))
    assert r["n_acima_cap"] == 0, f"capacidade violada: {r}"
    return r


def stage_charging_nivelado_cap(label, pconv_0, avail, energy_rem, blocked, upper_pc,
                                out_dir, K, priority_key, ref_pconv_orig=None, n_total=0):
    """Substitui `vs.stage_charging_unified` (mesma assinatura e retorno de v2g_nivelado.stage_charging_nivelado)."""
    assert CTX and len(CTX["e0"]) == blocked.shape[0], "chame preparar(fleet) antes"
    pre, cota = mascara_pre(blocked), cota_pre()
    _, energy_fin, alloc, res, floor = _floor_aware_cap(
        pconv_0, avail, energy_rem, blocked, upper_pc, K, priority_key, pre, cota)
    peak_pre = float((pconv_0 + alloc.sum(axis=0) * vs.CHARGER_POWER_KW).max())
    t0 = time.perf_counter()
    load, alloc, n_moves = _nivelar_cap(pconv_0, alloc, avail, blocked, pre, cota)
    print(f"[{label}] (cap) piso estrutural={floor:.2f} kWh | pico antes do nivelamento="
          f"{peak_pre:.1f} → depois={load.max():.1f} kW | {n_moves:,} movimentos "
          f"em {time.perf_counter() - t0:.2f}s")
    res = pd.concat([res, pd.DataFrame([dict(Iteration="nivelamento", Pc=round(float(load.max()), 3),
                                             Unallocated_kWh=res.Unallocated_kWh.iloc[-1],
                                             Floor_kWh=round(floor, 2), Flag=1)])],
                    ignore_index=True)
    chk = _conferir(blocked, alloc)
    chk.update(n_com_descarga=int(blocked.any(axis=1).sum()),
               n_cota_esgotada=int(((alloc & pre).sum(axis=1) >= cota)[blocked.any(axis=1)].sum()))
    CTX["conferencia"] = chk
    if out_dir is not None:
        (out_dir / "conferencia_capacidade.json").write_text(json.dumps(chk, indent=1))
    return load, energy_fin, alloc, res
