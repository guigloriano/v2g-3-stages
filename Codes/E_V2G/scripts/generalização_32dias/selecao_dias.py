#!/usr/bin/env python3
"""
selecao_dias.py — seleção determinística dos 32 dias de carga de Campo Grande.

Substitui o baseline (sábado 11/06/2022) e os 4 dias sazonais escolhidos à mão
por 8 dias úteis por estação, igualmente espaçados dentro de cada estação.
Parte de `select_days()` de `Codes/D_NSGAIII/utils/extras/batch_helpers.py`
(artigo do NSGA-III, congelado) com três correções:

1. Calendário explícito: exclui feriados nacionais, estaduais (MS) e municipais
   (Campo Grande), pontos facultativos, jogos do Brasil na Copa de 2022 e os
   dias úteis adjacentes a cada um deles (pontes e vésperas).
2. Estações como períodos contínuos (`SEASON_WINDOWS`): os dados vão de
   02/06/2022 a 31/05/2023, então o outono usa só 21/03–31/05/2023 e não
   mistura junho de 2022.
3. Espaçamento cobrindo a estação inteira: índices `linspace(0, n-1, k)`
   em vez de `[::len // k][:k]`, que descartava o fim de cada estação.

`reference_days()` define os dias ilustrativos por critério: o medoide dos dias
selecionados (dia típico, menor RMSE à mediana dos perfis) e o de maior pico
(pior caso).

Uso: python3 selecao_dias.py [--csv <saida.csv>]
"""
import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_BASE        = Path(__file__).resolve().parents[2]      # …/Codes/E_V2G
REPO_ROOT    = _BASE.parent.parent
ALL_DAYS_DIR = REPO_ROOT / 'Datasets/ProcessedData/Cities/CampoGrande/all_days'

N_PER_SEASON = 8
WEEKDAYS     = ('mon', 'tue', 'wed', 'thu', 'fri')

# Estações astronômicas (Hemisfério Sul) recortadas no intervalo dos dados.
SEASON_WINDOWS = {
    'winter': (date(2022, 6, 21),  date(2022, 9, 22)),
    'spring': (date(2022, 9, 23),  date(2022, 12, 20)),
    'summer': (date(2022, 12, 21), date(2023, 3, 20)),
    'autumn': (date(2023, 3, 21),  date(2023, 5, 31)),
}

# Dias atípicos no período dos dados (só os que caem em dia útil).
EXCLUDED_DAYS = {
    date(2022, 6, 13):  'Santo Antônio (municipal, Campo Grande)',
    date(2022, 6, 16):  'Corpus Christi',
    date(2022, 8, 26):  'Aniversário de Campo Grande (municipal)',
    date(2022, 9, 7):   'Independência',
    date(2022, 10, 11): 'Criação do Estado de MS (estadual)',
    date(2022, 10, 12): 'Nossa Senhora Aparecida',
    date(2022, 11, 2):  'Finados',
    date(2022, 11, 15): 'Proclamação da República',
    date(2022, 11, 24): 'Copa 2022: Brasil × Sérvia',
    date(2022, 11, 28): 'Copa 2022: Brasil × Suíça',
    date(2022, 12, 2):  'Copa 2022: Camarões × Brasil',
    date(2022, 12, 5):  'Copa 2022: Brasil × Coreia do Sul',
    date(2022, 12, 9):  'Copa 2022: Croácia × Brasil',
    date(2023, 2, 20):  'Carnaval',
    date(2023, 2, 21):  'Carnaval',
    date(2023, 2, 22):  'Quarta-feira de Cinzas (ponto facultativo)',
    date(2023, 4, 7):   'Sexta-feira Santa',
    date(2023, 4, 21):  'Tiradentes',
    date(2023, 5, 1):   'Dia do Trabalho',
}


def _parse(path: Path):
    """pc_cgr_YYYYMMDD_<season>_<dow>.csv → (date, dow)."""
    _, _, ymd, _, dow = path.stem.split('_')
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])), dow


def excluded_with_neighbors() -> dict:
    """Dias excluídos + dias úteis imediatamente antes/depois (pontes e vésperas)."""
    out = dict(EXCLUDED_DAYS)
    for d, why in EXCLUDED_DAYS.items():
        for nb in (d - timedelta(days=1), d + timedelta(days=1)):
            if nb.weekday() < 5 and nb not in out:
                out[nb] = f'adjacente a {d:%d/%m/%Y} ({why})'
    return out


def select_days(n_per_season: int = N_PER_SEASON, all_days_dir: Path = ALL_DAYS_DIR) -> list:
    """
    Amostra estratificada e determinística: n_per_season dias úteis por estação,
    igualmente espaçados dentro de SEASON_WINDOWS, sem dias de EXCLUDED_DAYS nem
    seus adjacentes. Retorna os caminhos em ordem cronológica.
    """
    excluded = excluded_with_neighbors()
    by_season = {s: [] for s in SEASON_WINDOWS}
    for f in sorted(all_days_dir.glob('pc_cgr_*.csv')):
        d, dow = _parse(f)
        if dow not in WEEKDAYS or d in excluded:
            continue
        for season, (start, end) in SEASON_WINDOWS.items():
            if start <= d <= end:
                by_season[season].append(f)

    selected = []
    for season, files in by_season.items():
        if len(files) < n_per_season:
            raise ValueError(f'{season}: só {len(files)} dias elegíveis, pedi {n_per_season}')
        idx = np.linspace(0, len(files) - 1, n_per_season).round().astype(int)
        selected += [files[i] for i in idx]
    return selected


def reference_days(paths: list, col: str = 'Pmax_Tuc') -> dict:
    """Dia típico (medoide: menor RMSE à mediana dos perfis) e pior caso (maior pico)."""
    profiles = {p: pd.read_csv(p)[col].to_numpy() for p in paths}
    median = np.median(np.vstack(list(profiles.values())), axis=0)
    rmse = {p: float(np.sqrt(np.mean((v - median) ** 2))) for p, v in profiles.items()}
    return {
        'tipico': min(rmse, key=rmse.get),
        'pior_caso': max(profiles, key=lambda p: profiles[p].max()),
    }


def selection_table(paths: list, col: str = 'Pmax_Tuc') -> pd.DataFrame:
    rows = []
    for p in paths:
        d, dow = _parse(p)
        season = next(s for s, (a, b) in SEASON_WINDOWS.items() if a <= d <= b)
        v = pd.read_csv(p)[col]
        rows.append({'date': d.isoformat(), 'season': season, 'dow': dow,
                     'peak_kw': round(v.max(), 1), 'mean_kw': round(v.mean(), 1),
                     'file': p.name})
    return pd.DataFrame(rows)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--csv', type=Path, help='grava a tabela dos dias selecionados')
    args = ap.parse_args()

    paths = select_days()
    df = selection_table(paths)
    print(df.drop(columns='file').to_string(index=False))
    print(df.groupby('season', sort=False)['peak_kw'].agg(['min', 'median', 'max']))
    for role, p in reference_days(paths).items():
        print(f'{role}: {p.name}')
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f'gravado: {args.csv}')
