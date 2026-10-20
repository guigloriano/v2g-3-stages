#!/usr/bin/env python3
"""
solucao_npz.py — guarda o solucao.csv do Gurobi como matriz de bits (solucao_bits.npz) e o devolve como CSV.

O solucao.csv (EV,TS,Power_kW; uma linha por horário de carga, ordenado por EV e TS) tem a potência sempre igual
(7,0 kW em todas as 768 instâncias da generalização), então a solução é só uma matriz EV × TS de sim/não. Ela é
gravada com np.packbits (18 bytes por EV com 144 horários) num .npz comprimido: o maior CSV, 12,5 MB, vira ~0,1 MB.
A conversão é exata: npz_para_csv devolve o CSV byte a byte igual ao original.

Conteúdo do .npz: bits (uint8, n_ev × ceil(n_ts/8)), n_ev, n_ts e potencia_kW.
O número de EVs vem do nome da pasta (inst_<N>_...), porque o último EV pode não carregar e não aparecer no CSV.

Os solucao.csv ficam fora do git (.gitignore); só os solucao_bits.npz são versionados.

Uso:
  python3 solucao_npz.py converter [pasta]    # todos os solucao.csv sob a pasta (padrão: generalization/exact_gurobi)
  python3 solucao_npz.py restaurar [pasta]    # recria os solucao.csv que faltam a partir dos .npz
  python3 solucao_npz.py conferir  [pasta]    # confere que cada .npz devolve o CSV igual ao do disco

No código: ler_solucao(npz) → DataFrame EV,TS,Power_kW; npz_para_csv(npz[, csv]) grava o CSV.
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

N_TS = 144
NOME_NPZ = "solucao_bits.npz"
NOME_CSV = "solucao.csv"
PASTA_PADRAO = Path(__file__).resolve().parents[4] / "Results" / "V2G" / "CampoGrande" / "generalization" / "exact_gurobi"


def _n_ev(pasta):
    m = re.match(r"inst_(\d+)_", Path(pasta).name)
    if not m:
        raise ValueError(f"não sei o número de EVs de {pasta} (esperado inst_<N>_...)")
    return int(m.group(1))


def csv_para_npz(csv, npz=None, n_ev=None, n_ts=N_TS):
    """Converte um solucao.csv em solucao_bits.npz (ao lado dele, por padrão). Devolve o caminho do .npz."""
    csv = Path(csv)
    npz = Path(npz) if npz else csv.with_name(NOME_NPZ)
    n_ev = n_ev or _n_ev(csv.parent)
    d = pd.read_csv(csv)
    pot = d.Power_kW.unique()
    if len(pot) > 1:
        raise ValueError(f"{csv}: potências diferentes {pot[:5]}; a matriz de bits supõe potência constante")
    if len(d) and (d.EV.max() >= n_ev or d.TS.min() < 1 or d.TS.max() > n_ts):
        raise ValueError(f"{csv}: EV ou TS fora de 0..{n_ev - 1} / 1..{n_ts}")
    m = np.zeros((n_ev, n_ts), bool)
    m[d.EV.values, d.TS.values - 1] = True
    if m.sum() != len(d):
        raise ValueError(f"{csv}: linhas repetidas (EV, TS)")
    np.savez_compressed(npz, bits=np.packbits(m, axis=1), n_ev=n_ev, n_ts=n_ts,
                        potencia_kW=float(pot[0]) if len(pot) else 7.0)
    return npz


def ler_solucao(npz):
    """Lê um solucao_bits.npz e devolve o DataFrame EV,TS,Power_kW (ordenado por EV e TS, como o CSV)."""
    z = np.load(npz)
    n_ts = int(z["n_ts"])
    m = np.unpackbits(z["bits"], axis=1)[:, :n_ts].astype(bool)
    ev, ts = np.nonzero(m)                       # nonzero percorre por linha: já sai ordenado por EV e TS
    return pd.DataFrame({"EV": ev, "TS": ts + 1, "Power_kW": float(z["potencia_kW"])})


def npz_para_csv(npz, csv=None):
    """Grava o solucao.csv (ao lado do .npz, por padrão) no mesmo formato do multiobj_c. Devolve o caminho."""
    npz = Path(npz)
    csv = Path(csv) if csv else npz.with_name(NOME_CSV)
    ler_solucao(npz).to_csv(csv, index=False, float_format="%.4f")
    return csv


def _texto_csv(npz):
    return ler_solucao(npz).to_csv(index=False, float_format="%.4f")


def main():
    acao = sys.argv[1] if len(sys.argv) > 1 else ""
    pasta = Path(sys.argv[2]) if len(sys.argv) > 2 else PASTA_PADRAO
    if acao == "converter":
        fs = sorted(pasta.rglob(NOME_CSV))
        for i, f in enumerate(fs, 1):
            csv_para_npz(f)
            if i % 50 == 0 or i == len(fs):
                print(f"{i}/{len(fs)}", flush=True)
    elif acao == "restaurar":
        n = 0
        for z in sorted(pasta.rglob(NOME_NPZ)):
            if not z.with_name(NOME_CSV).exists():
                npz_para_csv(z)
                n += 1
        print(f"{n} solucao.csv recriados")
    elif acao == "conferir":
        zs = sorted(pasta.rglob(NOME_NPZ))
        ruins = [z for z in zs if not z.with_name(NOME_CSV).exists()
                 or _texto_csv(z) != z.with_name(NOME_CSV).read_text()]
        print(f"{len(zs) - len(ruins)}/{len(zs)} iguais ao CSV")
        for z in ruins:
            print("DIFERENTE ou sem CSV:", z.parent)
        sys.exit(1 if ruins else 0)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
