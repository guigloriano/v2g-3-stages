#!/usr/bin/env python3
"""
medir_tempos_estagios.py — tempo de cada estágio do V2G nivelado (Estágio 1, recarga conjunta S2+S3 e nivelamento),
sem a geração de figuras, para o texto do relatório (§3, parâmetros, e §5.4, escala).

Roda o mesmo executor da grade (run_generalizacao._run_v2g) com cronômetros em volta de vs.stage1,
vn.stage_charging_floor_aware (busca binária da recarga) e vn.level_charging (nivelamento), e com SAVE_ITER_FIGS
desligado. As figuras finais de cada instância continuam sendo geradas, fora dos cronômetros, numa pasta temporária
(os resultados da grade não são tocados). Uma instância por vez, num só processo.

Saída: <generalization>/tempos_estagios_v2g.csv (uma linha por instância).

Uso: python3 medir_tempos_estagios.py [--frotas bld_s1 ...] [--tamanhos 50336] [--dias YYYYMMDD ...]
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import run_generalizacao as rg   # noqa: E402
import selecao_dias              # noqa: E402
import v2g_stable as vs          # noqa: E402
import v2g_nivelado as vn        # noqa: E402

T = {}


def cronometrar(mod, nome, chave):
    f = getattr(mod, nome)

    def g(*a, **k):
        t0 = time.perf_counter()
        r = f(*a, **k)
        T[chave] = T.get(chave, 0.0) + time.perf_counter() - t0
        return r
    setattr(mod, nome, g)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--frotas", nargs="+", default=["bld_s1", "bld_s2", "bld_s4", "bld_s5"])
    ap.add_argument("--tamanhos", nargs="+", type=int, default=[50336])
    ap.add_argument("--dias", nargs="+")
    ap.add_argument("--saida", type=Path, default=rg.DEFAULT_OUT / "tempos_estagios_v2g.csv")
    args = ap.parse_args()

    cronometrar(vs, "stage1", "s1_s")
    cronometrar(vn, "stage_charging_floor_aware", "recarga_s")
    cronometrar(vn, "level_charging", "nivelamento_s")
    run_v2g = rg._run_v2g
    main_orig = vs.main

    def main_sem_figs_iter():                      # _run_v2g liga SAVE_ITER_FIGS; desliga antes do vs.main
        vs.SAVE_ITER_FIGS = False
        main_orig()
    vs.main = main_sem_figs_iter

    dias = selecao_dias.select_days()
    if args.dias:
        dias = [d for d in dias if d.stem.split("_")[2] in args.dias]
    linhas = []
    with tempfile.TemporaryDirectory() as tmp:
        for frota in args.frotas:
            for n in args.tamanhos:
                pp = rg.EV_DIR / frota / f"pp_{n}_{frota}.csv"
                for dia in dias:
                    T.clear()
                    out = Path(tmp) / frota / rg.inst_name(n, frota, dia)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    t0 = time.perf_counter()
                    run_v2g(pp, dia, out)
                    total = time.perf_counter() - t0
                    linhas.append(dict(frota=frota, N=n, dia=dia.stem.split("_")[2], **{k: round(v, 3) for k, v in T.items()},
                                       algoritmo_s=round(sum(T.values()), 3), total_com_figuras_s=round(total, 1)))
                    print(linhas[-1], flush=True)
    df = pd.DataFrame(linhas)
    df.to_csv(args.saida, index=False)
    print(df.groupby(["frota", "N"])[["s1_s", "recarga_s", "nivelamento_s", "algoritmo_s"]].agg(["median", "max"]).round(2))
    print(f"→ {args.saida}")


if __name__ == "__main__":
    main()
