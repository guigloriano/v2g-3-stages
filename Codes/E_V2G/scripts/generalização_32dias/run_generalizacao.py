#!/usr/bin/env python3
"""
run_generalizacao.py — executa a grade de generalização (docs/13, Seções 3–6).

Grade: 32 dias (`selecao_dias.select_days()`) × 24 frotas/tamanhos
(bld_s1, bld_s2, bld_s4, bld_s5 × {3.000, 9.000, 18.000, 39.000, 50.336} + nor_s1 ×
{3.000, 9.000, 18.000, 32.330}) = 768 instâncias para o V2G nivelado e o LCVF; o Gurobi
roda no subconjunto N ∈ {3.000, 9.000, 18.000} (480 instâncias). As de 18.000 foram rodadas uma por vez com
run_gurobi_sequencial.py (registro de memória e tempo); por aqui, use --gurobi-jobs 1 para esse tamanho.

bld_s3 e bld_s6 ficam fora da grade padrão: têm as mesmas janelas de bld_s2 e bld_s1 e só
mudam o consumo por sessão (3,5×), que o modelo não usa (a demanda vem da bateria e do SoC),
então as entradas e os resultados são idênticos (verificado na rodada de 2026-10-04,
`analise/equivalencia_s3_s6.csv`). Podem ser rodados com `--frotas bld_s3 bld_s6`.

Nomes no texto do relatório (só na apresentação; os códigos da execução seguem os do dataset):
B1 = bld_s1 (Boulder real), B2 = bld_s2 (flexible), B3 = bld_s4 (8-h shifts),
B4 = bld_s5 (24-h); Norway = nor_s1.

V2G (`--v2g`, padrão `atual`):
  atual      versão do manuscrito desde 2026-10-29 (revisão interna R4/N20): recarga com a capacidade da bateria
             respeitada em todos os slots (v2g_nivelado_cap.py) e Stage 1 que só descarrega um veículo se a
             recuperação até 90% ainda couber na janela (v2g_nivelado_cap_s1rec.py). Grava em <saida>/v2g_nivelado/.
  original   V2G nivelado anterior (v2g_nivelado.py): capacidade só na saída. Por padrão grava em
             <saida>/_v2g_publicado_antes_correcao/v2g_nivelado/, onde estão os resultados dessa versão.

Os executores são cópias congeladas nesta pasta (v2g_stable.py, v2g_nivelado.py,
lcvf_fast_v2g.py e multiobj_c.c, mais os dois módulos da versão atual do V2G) e gravam no formato padrão deles (os mesmos CSVs e
figuras .webp, aqui a 150 dpi, sem perdas). O runner só troca o arquivo
de carga (um dia de all_days/), o diretório de saída e, nas etapas iterativas, grava
apenas a figura da ÚLTIMA iteração:

  V2G            CSVs padrão + Fig_01_perfil_inicial, Fig_02_v2g_estagios,
                 Fig_03_soc_distribuicao (+ conferencia_capacidade.json na versão atual;
                 Fig_S1_iter15_discharge só na original)
  LCVF           CSVs padrão (en_profile_final.csv e <instância>.csv na pasta da frota) +
                 Fig_01/Fig_01b/Fig_02_EN_All_EVs, <instância>.webp e Fig_03_15_VF_exec
                 (só a última das 15 iterações)
  Gurobi         en_profile_final.csv, solucao.csv, summary.csv + Fig_exact e Fig_compare
                 (LCVF × V2G × Exato), ambas de figuras_exato.py
  Todas          concluido.json (tempo, memória, commit; marca a instância como feita) e,
                 só se der erro, execucao.log.gz

`execucoes.csv` (na raiz da saída) acumula tempo e memória de cada execução.
Instâncias com `concluido.json` são puladas (retomável); `--refazer` ignora isso.
As tabelas, as figuras de comparação agregadas (gap por frota/estação, contra o Exato)
e os perfis sobrepostos por instância vêm de `analise_generalizacao.py`.

Saída: Results/V2G/CampoGrande/generalization/{v2g_nivelado,lcvf_v2g,exact_gurobi}/<frota>/<instância>/

Uso:
  python3 run_generalizacao.py --dry-run
  python3 run_generalizacao.py --tecnicas v2g --refazer            # refaz o V2G atual nas 768 instâncias
  python3 run_generalizacao.py --tecnicas v2g --v2g original       # V2G anterior (em _v2g_publicado_antes_correcao/)
  python3 run_generalizacao.py --tecnicas v2g lcvf --jobs 4
  python3 run_generalizacao.py --tecnicas gurobi --gurobi-jobs 2 --gurobi-threads 4
  python3 run_generalizacao.py --dias 20221003 --frotas bld_s1 --tamanhos 3000 --saida /tmp/teste   # teste
"""
import os
# Um processo por núcleo: sem paralelismo interno do numpy/BLAS nos workers.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import contextlib
import csv
import gzip
import io
import json
import multiprocessing as mp
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent          # …/E_V2G/scripts/generalização_32dias
sys.path.insert(0, str(SCRIPTS_DIR))                    # cópias congeladas dos executores ficam aqui
import selecao_dias  # noqa: E402

REPO_ROOT    = SCRIPTS_DIR.parents[3]
EV_DIR       = REPO_ROOT / "Datasets/ProcessedData/Vehicles/V2G"
DEFAULT_OUT  = REPO_ROOT / "Results/V2G/CampoGrande/generalization"
GUROBI_BIN   = SCRIPTS_DIR / "multiobj_c"              # compilado de multiobj_c.c desta pasta
GUROBI_HOME  = Path(os.environ.get("GUROBI_HOME", "/opt/gurobi1103/linux64"))

FROTAS = {
    **{f"bld_s{s}": (3000, 9000, 18000, 39000, 50336) for s in (1, 2, 4, 5)},
    "nor_s1": (3000, 9000, 18000, 32330),
}
# idênticas a bld_s2 e bld_s1 nas entradas dos algoritmos; só rodam se pedidas em --frotas
FROTAS_REDUNDANTES = {f"bld_s{s}": (3000, 9000, 18000, 39000, 50336) for s in (3, 6)}
# benchmark exato em todos os tamanhos (decisão de 2026-10-07). N >= 18.000 foi rodado em sequência
# por run_gurobi_sequencial.py (memória; B4 50.336 precisa de MemLimit >= 17 GB), não por este script.
N_GUROBI = tuple(sorted({n for t in FROTAS.values() for n in t}))
SUBDIR = {"v2g": "v2g_nivelado", "lcvf": "lcvf_v2g", "gurobi": "exact_gurobi"}


# ─── Utilidades ──────────────────────────────────────────────────────────────

def inst_name(n, frota, dia_path):
    return f"inst_{n}_{frota}_{dia_path.stem[3:]}"


def inst_dir(out_root, tec, n, frota, dia_path):
    return out_root / SUBDIR[tec] / frota / inst_name(n, frota, dia_path)


def _git_rev():
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "Codes"], cwd=REPO_ROOT,
                               capture_output=True, text=True).stdout.strip()
        return rev + ("+alterado" if dirty else "")
    except OSError:
        return "?"


def _gzip_text(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


# ─── V2G ─────────────────────────────────────────────────────────────────────

V2G_ANTERIOR = DEFAULT_OUT / "_v2g_publicado_antes_correcao"   # resultados da versão original
V2G_VERSAO = "atual"                                         # definido em main() (--v2g); lido pelo _worker


def _run_v2g(pp_path, dia_path, out_dir):
    """V2G atual (manuscrito): recarga com capacidade em todos os slots + Stage 1 consciente da recuperação."""
    import v2g_stable as vs
    import v2g_nivelado_cap as vc
    import v2g_nivelado_cap_s1rec as vr

    vs.INTERACTIVE_MENU   = False
    vs.CITY_DIR_CHOICES   = [1]
    vs.RUN_ALL_CITY_FILES = False
    vs.PP_FILE_FILTER     = [pp_path.name]
    vs.choose_city_file   = lambda city_path, choice, pattern="*.csv": dia_path
    vs.build_results_path = lambda city, scen, inst: out_dir.parent / inst
    vs.stage_charging_unified = vc.stage_charging_nivelado_cap
    vs.stage1 = vr.stage1_rec
    vs.SAVE_ITER_FIGS = False
    vc.preparar(vs.load_preprocessed_fleet(pp_path))
    vs.main()


def _run_v2g_original(pp_path, dia_path, out_dir):
    """V2G nivelado original (resultados em _v2g_publicado_antes_correcao/): capacidade só na saída."""
    import v2g_stable as vs
    import v2g_nivelado as vn

    vs.INTERACTIVE_MENU   = False
    vs.CITY_DIR_CHOICES   = [1]
    vs.RUN_ALL_CITY_FILES = False
    vs.PP_FILE_FILTER     = [pp_path.name]
    vs.choose_city_file   = lambda city_path, choice, pattern="*.csv": dia_path
    vs.build_results_path = lambda city, scen, inst: out_dir.parent / inst
    vs.stage_charging_unified = vn.stage_charging_nivelado

    # Figuras por iteração do S1: só a última (Fig_S1_iter15_discharge.webp)
    vs.SAVE_ITER_FIGS = True
    fig_s1 = vs._save_discharge_fig
    def discharge_fig(pconv_orig, pconv_new, Pf, iteration, out, **k):
        if iteration == vs.N_ITER_S1:
            fig_s1(pconv_orig, pconv_new, Pf, iteration, out, **k)
    vs._save_discharge_fig = discharge_fig

    vs.main()


# ─── LCVF ────────────────────────────────────────────────────────────────────

def _run_lcvf(pp_path, dia_path, out_dir):
    sys.argv = ["lcvf_fast_v2g.py"]           # o módulo lê argv na importação
    import lcvf_fast_v2g as lc

    lc.CITY_FILE      = dia_path
    lc.PP_FILE_FILTER = [pp_path.name]
    lc.RESULTS_ROOT   = out_dir.parent.parent

    # Figuras por iteração do valley-filling: só a última (Fig_03_15_VF_exec.webp)
    fig_vf = lc.grafico_VF
    def grafico(df_plot, Pc, pc_counter, *a, **k):
        if pc_counter == lc.ITERATION_COUNT:
            fig_vf(df_plot, Pc, pc_counter, *a, **k)
    lc.grafico_VF = grafico

    lc.main()


# ─── Worker Python (um processo novo por instância) ──────────────────────────

def _worker(task):
    tec, pp_path, dia_path, out_dir = task
    t0 = time.perf_counter()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            v2g = _run_v2g if V2G_VERSAO == "atual" else _run_v2g_original
            (v2g if tec == "v2g" else _run_lcvf)(pp_path, dia_path, out_dir)
        status, erro = "ok", ""
    except Exception:
        status, erro = "erro", traceback.format_exc()
    wall = time.perf_counter() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    if erro:
        out_dir.mkdir(parents=True, exist_ok=True)
        _gzip_text(out_dir / "execucao.log.gz", buf.getvalue() + "\n" + erro)
    return dict(tecnica=tec, instancia=out_dir.name, status=status, wall_s=round(wall, 2),
                maxrss_mb=round(rss, 1), erro=erro.strip().splitlines()[-1] if erro else "",
                dir=str(out_dir))


# ─── Gurobi ──────────────────────────────────────────────────────────────────

def _run_gurobi(n, frota, dia_path, out_root, threads):
    """Roda multiobj_c num diretório de trabalho próprio (ele grava charging.log e
    multiobj_c.lp no cwd e usa caminhos relativos ../../../{Datasets,Results}) e depois
    gera Fig_exact e Fig_compare (figuras_exato.py)."""
    target = out_root / SUBDIR["gurobi"]
    target.mkdir(parents=True, exist_ok=True)
    d = inst_dir(out_root, "gurobi", n, frota, dia_path)
    work = Path(tempfile.mkdtemp(prefix="gurobi_gen_"))
    log = []
    try:
        (work / "Datasets").symlink_to(REPO_ROOT / "Datasets")
        res = work / "Results/V2G/CampoGrande/generalization"
        res.mkdir(parents=True)
        (res / "exact_gurobi").symlink_to(target)
        cwd = work / "Codes/Exact_Alg/gurobi_alg"
        cwd.mkdir(parents=True)
        (cwd / "gurobi.env").write_text(f"Threads {threads}\n")

        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = f"{GUROBI_HOME / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"
        t0 = time.perf_counter()
        with open(cwd / "stdout.log", "w") as out:
            p = subprocess.Popen([str(GUROBI_BIN), str(n), frota, dia_path.name],
                                 cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
            _, wstatus, ru = os.wait4(p.pid, 0)
        rc = p.returncode = os.waitstatus_to_exitcode(wstatus)
        wall_solver = time.perf_counter() - t0
        # "EV i alocado no TS t" repete o solucao.csv linha a linha: fica fora do log
        with open(cwd / "stdout.log") as f:
            log.append("".join(l for l in f if not l.startswith("EV ")))

        ok = rc == 0 and (d / "summary.csv").exists()
        erro = "" if ok else f"multiobj_c: código de saída {rc}"
        if ok:
            fenv = dict(os.environ, MPLBACKEND="Agg", GENERALIZACAO_SAIDA=str(out_root))
            r = subprocess.run([sys.executable, str(SCRIPTS_DIR / "figuras_exato.py"), str(n), frota, dia_path.name],
                               cwd=SCRIPTS_DIR, env=fenv, capture_output=True, text=True)
            log.append(r.stdout + r.stderr)
            if r.returncode != 0:
                ok, erro = False, f"figuras_exato.py: código de saída {r.returncode}"
        if not ok:
            d.mkdir(parents=True, exist_ok=True)
            _gzip_text(d / "execucao.log.gz", "\n".join(log))
        return dict(tecnica="gurobi", instancia=d.name, status="ok" if ok else "erro",
                    wall_s=round(time.perf_counter() - t0, 2), wall_solver_s=round(wall_solver, 2),
                    maxrss_mb=round(ru.ru_maxrss / 1024, 1), erro=erro)
    finally:
        shutil.rmtree(work, ignore_errors=True)   # inclui charging.log e multiobj_c.lp


# ─── Orquestração ────────────────────────────────────────────────────────────

LOG_FIELDS = ["inicio", "tecnica", "instancia", "status", "wall_s", "wall_solver_s", "maxrss_mb",
              "jobs", "erro"]


def _registrar(out_root, row, d, rev, jobs):
    row = dict(row, inicio=datetime.now().isoformat(timespec="seconds"), jobs=jobs)
    log = out_root / "execucoes.csv"
    novo = not log.exists()
    with open(log, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if novo:
            w.writeheader()
        w.writerow(row)
    if row["status"] == "ok":
        (d / "concluido.json").write_text(json.dumps(
            dict(row, commit=rev, selecao="selecao_dias.select_days()"), ensure_ascii=False, indent=1))
    print(f"[{row['status']}] {row['tecnica']:6s} {row['instancia']}  {row['wall_s']:.1f}s  "
          f"{row['maxrss_mb']:.0f} MB {row['erro']}", flush=True)


def montar_tarefas(args, out_root):
    dias = selecao_dias.select_days()
    if args.dias:
        dias = [p for p in dias if p.stem.split("_")[2] in args.dias]
    tarefas = []
    for tec in args.tecnicas:
        for dia in dias:
            for frota, tamanhos in {**FROTAS, **FROTAS_REDUNDANTES}.items():
                if (args.frotas and frota not in args.frotas) or (not args.frotas and frota not in FROTAS):
                    continue
                for n in tamanhos:
                    if tec == "gurobi" and n not in N_GUROBI:
                        continue
                    if args.tamanhos and n not in args.tamanhos:
                        continue
                    d = inst_dir(out_root, tec, n, frota, dia)
                    if (d / "concluido.json").exists() and not args.refazer:
                        continue
                    tarefas.append((tec, n, frota, dia, d))
    # maiores primeiro: melhor balanceamento entre processos
    tarefas.sort(key=lambda t: -t[1])
    return dias, tarefas


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--tecnicas", nargs="+", choices=["v2g", "lcvf", "gurobi"],
                    default=["v2g", "lcvf", "gurobi"])
    ap.add_argument("--jobs", type=int, default=4, help="processos para V2G/LCVF")
    ap.add_argument("--gurobi-jobs", type=int, default=2, help="execuções simultâneas do Gurobi")
    ap.add_argument("--gurobi-threads", type=int, default=4, help="Threads de cada execução do Gurobi")
    ap.add_argument("--dias", nargs="+", help="restringe a dias YYYYMMDD da seleção")
    ap.add_argument("--frotas", nargs="+", choices=sorted({**FROTAS, **FROTAS_REDUNDANTES}))
    ap.add_argument("--tamanhos", nargs="+", type=int)
    ap.add_argument("--v2g", choices=["atual", "original"], default="atual",
                    help="versão do V2G (atual = manuscrito; original = anterior, grava em _v2g_publicado_antes_correcao/)")
    ap.add_argument("--saida", type=Path, default=None, help="padrão: generalization/ (ou a pasta da versão original)")
    ap.add_argument("--refazer", action="store_true", help="ignora concluido.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    global V2G_VERSAO
    V2G_VERSAO = args.v2g
    if args.v2g == "original" and set(args.tecnicas) != {"v2g"}:
        ap.error("--v2g original só roda o V2G (use --tecnicas v2g); LCVF e Gurobi não dependem da versão")
    if args.saida is None:
        args.saida = V2G_ANTERIOR if args.v2g == "original" else DEFAULT_OUT

    out_root = args.saida.resolve()
    dias, tarefas = montar_tarefas(args, out_root)
    por_tec = {t: sum(1 for x in tarefas if x[0] == t) for t in args.tecnicas}
    print(f"{len(dias)} dias | pendentes: {por_tec} | saída: {out_root}")
    if args.dry_run or not tarefas:
        return

    out_root.mkdir(parents=True, exist_ok=True)
    rev = _git_rev()
    pp = {p.name: p for p in EV_DIR.rglob("pp_*.csv")}

    py = [(t, pp[f"pp_{n}_{f}.csv"], dia, d) for t, n, f, dia, d in tarefas if t != "gurobi"]
    if py:
        ctx = mp.get_context("fork")
        with ctx.Pool(args.jobs, maxtasksperchild=1) as pool:
            for row in pool.imap_unordered(_worker, py):
                _registrar(out_root, row, Path(row.pop("dir")), rev, args.jobs)

    gu = [(n, f, dia, d) for t, n, f, dia, d in tarefas if t == "gurobi"]
    if gu:
        if not GUROBI_BIN.exists():
            raise FileNotFoundError(f"Compile o Gurobi primeiro: {GUROBI_BIN}")
        with ThreadPoolExecutor(args.gurobi_jobs) as ex:
            futs = {ex.submit(_run_gurobi, n, f, dia, out_root, args.gurobi_threads): d
                    for n, f, dia, d in gu}
            for fut in as_completed(futs):
                _registrar(out_root, fut.result(), futs[fut], rev, args.gurobi_jobs)


if __name__ == "__main__":
    main()
