#!/usr/bin/env python3
"""
sensibilidade_madrugada_n1.py — sensibilidade do dia periódico na N1 (nor_s1) à carga da madrugada.

O dia periódico (docs/13; 08 - v2g_preprocessing.py, coluna wrap_end_ts) completa as sessões que passam
da meia-noite nos primeiros slots do mesmo dia, supondo que a carga convencional da madrugada seguinte é
igual à do dia escalonado. Este teste troca os slots 1–48 (00:00–08:00) de cada dia da seleção pela carga
real do dia seguinte (o resto do dia fica igual) e reescalona a N1 com 18.000 e 32.330 EVs pelo V2G
nivelado e pelo LCVF (mesmo executor da grade, run_generalizacao._worker). O último dia da seleção
(31/05/2023) não tem dia seguinte nos dados e fica de fora: 31 dias × 2 tamanhos × 2 técnicas = 124 execuções.

A troca também muda a carga vista pelas sessões da manhã do próprio dia, então o teste mede a
sensibilidade à premissa, não modela dois dias seguidos. O Gurobi não é rodado (o LCVF fica a até
0,02% do ótimo na N1).

Saídas:
  generalization/_sens_madrugada_n1/{cargas,v2g_nivelado,lcvf_v2g}/   (execuções; fora do git)
  generalization/analise/sensibilidade_madrugada_n1.csv               (uma linha por N e dia: picos e
                                                                        completude com a premissa e no teste)
Uso:
  python3 sensibilidade_madrugada_n1.py            # roda o que falta (retomável) e grava o resumo
  python3 sensibilidade_madrugada_n1.py --so-resumo
"""
import argparse
import datetime as dt
import json
import multiprocessing as mp
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_generalizacao as rg          # noqa: E402
import analise_generalizacao as ag      # noqa: E402

GEN = rg.DEFAULT_OUT
OUT = GEN / "_sens_madrugada_n1"
RESUMO = GEN / "analise" / "sensibilidade_madrugada_n1.csv"
TAMANHOS = (18000, 32330)
SLOTS_MADRUGADA = 48            # 00:00–08:00


def cargas() -> list:
    """Uma cópia de cada dia da seleção com a madrugada trocada pela do dia seguinte (mesmo nome de arquivo)."""
    arquivos = {re.search(r"pc_cgr_(\d{8})_", f.name).group(1): f for f in rg.selecao_dias.ALL_DAYS_DIR.glob("pc_cgr_*.csv")}
    (OUT / "cargas").mkdir(parents=True, exist_ok=True)
    saida = []
    for f in rg.selecao_dias.select_days():
        d = re.search(r"pc_cgr_(\d{8})_", f.name).group(1)
        seg = (dt.datetime.strptime(d, "%Y%m%d") + dt.timedelta(days=1)).strftime("%Y%m%d")
        if seg not in arquivos:
            continue
        a, b = pd.read_csv(f), pd.read_csv(arquivos[seg])
        assert len(a) == len(b) == 144
        a.loc[:SLOTS_MADRUGADA - 1, "Pconv"] = b.loc[:SLOTS_MADRUGADA - 1, "Pconv"].values
        out = OUT / "cargas" / f.name
        a.to_csv(out, index=False)
        saida.append(out)
    return saida


def rodar(jobs: int):
    pp = {p.name: p for p in rg.EV_DIR.rglob("pp_*.csv")}
    tarefas = [(t, pp[f"pp_{n}_nor_s1.csv"], c, rg.inst_dir(OUT, t, n, "nor_s1", c))
               for n in TAMANHOS for t in ("v2g", "lcvf") for c in cargas()]
    tarefas = [x for x in tarefas if not (x[3] / "concluido.json").exists()]
    print(f"{len(tarefas)} execuções pendentes → {OUT}", flush=True)
    if not tarefas:
        return
    with mp.get_context("fork").Pool(jobs, maxtasksperchild=1) as pool:
        for row in pool.imap_unordered(rg._worker, tarefas):
            d = Path(row.pop("dir"))
            if row["status"] == "ok":
                (d / "concluido.json").write_text(json.dumps(row, ensure_ascii=False, indent=1))
            print(f"[{row['status']}] {row['tecnica']} {row['instancia']} {row['wall_s']:.1f}s {row['erro']}", flush=True)


def resumo():
    sens = ag.parear(ag.consolidar(OUT))
    base = pd.read_csv(GEN / "analise" / "pareado.csv", dtype={"dia": str})
    sens["dia"] = sens.dia.astype(str)
    m = sens.merge(base, on=["frota", "N", "dia"], suffixes=("_teste", "_premissa"))
    cols = ["frota", "N", "dia"] + [f"{c}_{s}" for c in ("pico_final_kW_v2g", "pico_final_kW_lcvf", "gap_pico_v2g_vs_lcvf_pct",
                                                          "frac_abaixo_90_pct_v2g", "frac_abaixo_90_pct_lcvf")
                                    for s in ("premissa", "teste")]
    m[cols].round(4).to_csv(RESUMO, index=False)
    for n, g in m.groupby("N"):
        gt, gp = g.gap_pico_v2g_vs_lcvf_pct_teste, g.gap_pico_v2g_vs_lcvf_pct_premissa
        print(f"N = {n} ({len(g)} dias): V2G × LCVF mediana {gt.median():+.2f}% (premissa {gp.median():+.2f}%), "
              f"V2G perde em {(gt > 0.005).sum()}, Wilcoxon p = {wilcoxon(g.pico_final_kW_v2g_teste, g.pico_final_kW_lcvf_teste).pvalue:.1e}")
        for t in ("v2g", "lcvf"):
            d = 100 * (g[f"pico_final_kW_{t}_teste"] / g[f"pico_final_kW_{t}_premissa"] - 1)
            print(f"   pico {t}: mudança mediana {d.median():+.2f}%, de {d.min():+.2f}% a {d.max():+.2f}%")
        print(f"   abaixo de 90%: V2G {g.frac_abaixo_90_pct_v2g_teste.median():.2f}% (premissa {g.frac_abaixo_90_pct_v2g_premissa.median():.2f}%), "
              f"LCVF {g.frac_abaixo_90_pct_lcvf_teste.median():.2f}% (premissa {g.frac_abaixo_90_pct_lcvf_premissa.median():.2f}%)")
    print(f"→ {RESUMO}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--so-resumo", action="store_true")
    a = ap.parse_args()
    if not a.so_resumo:
        rodar(a.jobs)
    resumo()
