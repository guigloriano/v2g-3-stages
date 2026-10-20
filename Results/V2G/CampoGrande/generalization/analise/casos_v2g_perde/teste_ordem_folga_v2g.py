"""Testa variantes da recarga S2+S3 do V2G nivelado (monkeypatch; repositório intocado)."""
import sys, os, inspect, glob, json, time
from pathlib import Path
import numpy as np, pandas as pd
COD = Path('/home/lscad/Guilherme/Doutorado/Redmine/power-charging-evs/Codes/E_V2G/scripts/generalização_32dias')
sys.path.insert(0, str(COD))
import run_generalizacao as rg
import v2g_stable as vs
import v2g_nivelado as vn

ORIG_ITER = vs._run_lcvf_style_iter
src = inspect.getsource(ORIG_ITER).replace("def _run_lcvf_style_iter(", "def _run_lcvf_style_iter_lax(")
alvo = "cand_idx = cand_idx[np.argsort(priority_key[cand_idx])]"
assert alvo in src
src = src.replace(alvo, "folga = can0[np.ix_(cand_idx, remaining)].sum(axis=1) - np.ceil(energy[cand_idx] / ENERGY_PER_SLOT_KWH - 1e-9)\n            cand_idx = cand_idx[np.argsort(folga, kind='stable')]")
exec(src, vs.__dict__)
LAX_ITER = vs._run_lcvf_style_iter_lax

def floor_aware_fresh(pconv_0, avail, energy_rem, blocked, upper_pc, K, priority_key):
    it = vs._run_lcvf_style_iter
    _, _, _, _, floor = it(pconv_0, avail, energy_rem, blocked, 1e12, priority_key)
    up, lo = upper_pc, float(pconv_0.min()); best = None; rows = []
    for j in range(K):
        Pc = (up + lo) / 2
        p, e, _, a, un = it(pconv_0, avail, energy_rem, blocked, Pc, priority_key)
        Flag = 1 if un - floor < vs.TOLERANCE else 0
        rows.append(dict(Iteration=j + 1, Pc=round(Pc, 3), Unallocated_kWh=round(un, 2), Floor_kWh=round(floor, 2), Flag=Flag))
        if Flag: up = Pc; best = (p, e, a)
        else: lo = Pc
    if best is None:
        p, e, _, a, un = it(pconv_0, avail, energy_rem, blocked, up, priority_key); best = (p, e, a)
        rows.append(dict(Iteration="final", Pc=round(up, 3), Unallocated_kWh=round(un, 2), Floor_kWh=round(floor, 2), Flag=int(un - floor < vs.TOLERANCE)))
    return best[0], best[1], best[2], pd.DataFrame(rows), floor

ORIG_FA = vn.stage_charging_floor_aware
def configurar(var):
    vs._run_lcvf_style_iter = LAX_ITER if var in ('lax', 'lax_fresh') else ORIG_ITER
    vn.stage_charging_floor_aware = floor_aware_fresh if var == 'lax_fresh' else ORIG_FA

OUT = Path(os.environ.get('SAIDA', '.')).resolve()
def job(t):
    var, fr, N, dia = t
    configurar(var)
    dia_path = next(Path(rg.REPO_ROOT / 'Datasets/ProcessedData/Cities/CampoGrande/all_days').glob(f'pc_{dia}.csv'))
    pp = next(rg.EV_DIR.rglob(f'pp_{N}_{fr}.csv'))
    out_dir = OUT / var / 'v2g_nivelado' / fr / f'inst_{N}_{fr}_{dia}'
    row = rg._worker(('v2g', pp, dia_path, out_dir))
    s = pd.read_csv(out_dir / 'summary_3stage.csv').set_index('Estágio') if row['status'] == 'ok' else None
    return dict(var=var, fr=fr, N=N, dia=dia, status=row['status'], wall=row['wall_s'], erro=row.get('erro', '')[:200],
                pico=None if s is None else s.loc['Após S3 (máx)', 'Pico_Pconv_kW'])

if __name__ == '__main__':
    from multiprocessing import get_context
    G = rg.DEFAULT_OUT
    celulas = [('bld_s1', 50336), ('bld_s1', 39000), ('nor_s1', 32330), ('nor_s1', 18000), ('bld_s2', 50336),
               ('bld_s4', 9000), ('bld_s5', 18000), ('bld_s2', 9000)]
    dias = sorted({Path(p).name.split('_cgr_')[1] for p in glob.glob(f'{G}/v2g_nivelado/bld_s1/inst_3000_*')})
    dias = ['cgr_' + d for d in dias[::4]]
    variantes = sys.argv[1:] or ['lax', 'lax_fresh']
    tasks = [(v, f, n, d) for f, n in celulas for d in dias for v in variantes]
    print(len(tasks), 'tarefas', flush=True)
    rows = []
    with get_context('fork').Pool(4, maxtasksperchild=1) as pool:
        for i, r in enumerate(pool.imap_unordered(job, tasks)):
            rows.append(r); print(i + 1, r['var'], r['fr'], r['N'], r['dia'], r['status'], r['pico'], flush=True)
            pd.DataFrame(rows).to_csv(OUT / 'res_v2g.csv', index=False)
    print('fim', flush=True)
