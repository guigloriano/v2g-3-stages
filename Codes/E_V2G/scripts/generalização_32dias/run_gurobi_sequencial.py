#!/usr/bin/env python3
"""
run_gurobi_sequencial.py — modelo exato (Gurobi) da generalização em um tamanho de frota acima de 9.000,
executado de forma estritamente sequencial e com registro completo de recursos.

Diferenças em relação ao run_generalizacao.py (que roda o Gurobi só em N ∈ {3.000, 9.000}, com até 2
execuções simultâneas):
  * uma instância por vez: a próxima só começa quando a anterior (solver + figuras) terminou;
  * mesmos executável (multiobj_c desta pasta), Threads (4, como na grade) e saída
    (generalization/exact_gurobi/<frota>/inst_<N>_<frota>_cgr_<dia>/), com o mesmo concluido.json;
  * MemLimit do Gurobi (padrão 12 GB): se for atingido, a instância é registrada como erro e a rotina
    segue para a próxima, sem levar a máquina ao swap;
  * registro de tudo, por instância:
      gurobi.log.gz     log integral do Gurobi (charging.log)
      stdout.log.gz     saída do multiobj_c (sem as linhas "EV i alocado no TS t", que repetem solucao.csv)
      recursos.csv      série a cada 1 s: RSS do processo, memória disponível e swap usado na máquina
      concluido.json    como no runner, mais os campos de recursos e do log (só se status ok)
    e, na raiz da saída, execucoes_gurobi_sequencial.csv (uma linha por instância, ok ou erro) e uma linha
    no execucoes.csv geral (jobs = 1).

Ordem: a de --frotas (padrão: da mais barata para a mais cara, B1, B2, N1, B3, B4); dias na ordem da seleção.
Retomável: instâncias com concluido.json são puladas. O diretório de trabalho (onde o multiobj_c grava o .lp do
modelo, centenas de MB) fica no disco, em <saida>/_trabalho_gurobi, porque o /tmp desta máquina é tmpfs (RAM);
ele é apagado ao fim de cada instância, e o tamanho do .lp fica registrado (lp_mb).

Uso:
  python3 run_gurobi_sequencial.py --tamanho 18000 --dry-run
  nohup python3 run_gurobi_sequencial.py --tamanho 18000 > ../../../../Results/V2G/CampoGrande/generalization/gurobi_18000.log 2>&1 &
  python3 run_gurobi_sequencial.py --tamanho 18000 --frotas bld_s5 --dias 20221003     # piloto
"""
import argparse
import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import run_generalizacao as rg
import selecao_dias

ORDEM = ["bld_s1", "bld_s2", "nor_s1", "bld_s4", "bld_s5"]     # B1, B2, N1, B3, B4
CAMPOS = ["inicio", "fim", "frota", "N", "dia", "instancia", "status", "erro", "codigo_saida",
          "wall_s", "wall_solver_s", "cpu_s", "maxrss_mb", "rss_pico_amostrado_mb",
          "mem_disponivel_min_mb", "swap_usado_max_mb", "threads", "memlimit_gb",
          "presolve_linhas", "presolve_colunas", "nos_explorados", "status_gurobi", "pico_kw", "lp_mb"]


def _meminfo():
    """MemAvailable e swap usado (MB) da máquina, de /proc/meminfo."""
    v = {}
    with open("/proc/meminfo") as f:
        for linha in f:
            k, x = linha.split(":", 1)
            v[k] = int(x.split()[0]) / 1024
    return v["MemAvailable"], v["SwapTotal"] - v["SwapFree"]


def _rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for linha in f:
                if linha.startswith("VmRSS:"):
                    return int(linha.split()[1]) / 1024
    except FileNotFoundError:
        pass
    return 0.0


class Amostrador(threading.Thread):
    """Amostra a cada `intervalo` s o RSS do processo e a memória da máquina até o processo terminar."""

    def __init__(self, pid, intervalo=1.0):
        super().__init__(daemon=True)
        self.pid, self.intervalo, self.linhas, self.parar = pid, intervalo, [], threading.Event()

    def run(self):
        t0 = time.perf_counter()
        while not self.parar.is_set():
            disp, swap = _meminfo()
            self.linhas.append((round(time.perf_counter() - t0, 1), round(_rss_mb(self.pid), 1),
                                round(disp, 1), round(swap, 1)))
            self.parar.wait(self.intervalo)


def _ler_log_gurobi(texto):
    """Campos-resumo do charging.log: tamanho após o presolve (1º nível), nós explorados (soma) e status final."""
    pres = re.search(r"Presolved: (\d+) rows, (\d+) columns", texto)
    nos = sum(int(float(m)) for m in re.findall(r"Explored (\d+(?:\.\d+)?) nodes", texto))
    finais = re.findall(r"^(Optimal solution found.*|Memory limit reached.*|Time limit reached.*|"
                         r"Model is infeasible.*|Solve interrupted.*|Out of memory.*)$", texto, re.M)
    return dict(presolve_linhas=int(pres.group(1)) if pres else "", presolve_colunas=int(pres.group(2)) if pres else "",
                nos_explorados=nos, status_gurobi=finais[-1].strip() if finais else "")


def executar(n, frota, dia, out_root, threads, memlimit, tmpdir):
    d = rg.inst_dir(out_root, "gurobi", n, frota, dia)
    target = out_root / rg.SUBDIR["gurobi"]
    target.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="gurobi_seq_", dir=tmpdir))   # no disco: o /tmp é tmpfs (RAM)
    inicio = datetime.now().isoformat(timespec="seconds")
    try:
        # mesmo diretório de trabalho espelhado do runner (caminhos relativos do multiobj_c)
        (work / "Datasets").symlink_to(rg.REPO_ROOT / "Datasets")
        res = work / "Results/V2G/CampoGrande/generalization"
        res.mkdir(parents=True)
        (res / "exact_gurobi").symlink_to(target)
        cwd = work / "Codes/Exact_Alg/gurobi_alg"
        cwd.mkdir(parents=True)
        (cwd / "gurobi.env").write_text(f"Threads {threads}\nMemLimit {memlimit}\n")
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = f"{rg.GUROBI_HOME / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"

        t0 = time.perf_counter()
        with open(cwd / "stdout.log", "w") as out:
            p = subprocess.Popen([str(rg.GUROBI_BIN), str(n), frota, dia.name], cwd=cwd, env=env,
                                 stdout=out, stderr=subprocess.STDOUT)
            amostra = Amostrador(p.pid)
            amostra.start()
            _, wstatus, ru = os.wait4(p.pid, 0)
            amostra.parar.set()
            amostra.join()
        rc = os.waitstatus_to_exitcode(wstatus)
        wall_solver = time.perf_counter() - t0

        d.mkdir(parents=True, exist_ok=True)
        glog = (cwd / "charging.log").read_text(errors="replace") if (cwd / "charging.log").exists() else ""
        with gzip.open(d / "gurobi.log.gz", "wt") as f:
            f.write(glog)
        with open(cwd / "stdout.log", errors="replace") as fi, gzip.open(d / "stdout.log.gz", "wt") as fo:
            fo.writelines(l for l in fi if not l.startswith("EV "))
        with open(d / "recursos.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "rss_mb", "mem_disponivel_mb", "swap_usado_mb"])
            w.writerows(amostra.linhas)

        ok = rc == 0 and (d / "summary.csv").exists()
        erro = "" if ok else f"multiobj_c: código de saída {rc}"
        if ok:
            fenv = dict(os.environ, MPLBACKEND="Agg", GENERALIZACAO_SAIDA=str(out_root))
            r = subprocess.run([sys.executable, str(rg.SCRIPTS_DIR / "figuras_exato.py"), str(n), frota, dia.name],
                               cwd=rg.SCRIPTS_DIR, env=fenv, capture_output=True, text=True)
            if r.returncode != 0:
                ok, erro = False, f"figuras_exato.py: código de saída {r.returncode}"
                with gzip.open(d / "figuras.log.gz", "wt") as f:
                    f.write(r.stdout + r.stderr)
        pico = ""
        if (d / "summary.csv").exists():
            with open(d / "summary.csv") as f:
                pico = next(csv.DictReader(f)).get("Pico_kW", "")
        lp = cwd / "multiobj_c.lp"
        lp_mb = round(lp.stat().st_size / 1024**2, 1) if lp.exists() else ""
        rss = [x[1] for x in amostra.linhas] or [0]
        disp = [x[2] for x in amostra.linhas] or [0]
        swap = [x[3] for x in amostra.linhas] or [0]
        return dict(inicio=inicio, fim=datetime.now().isoformat(timespec="seconds"), frota=frota, N=n,
                    dia=dia.stem.split("_")[2], instancia=d.name, status="ok" if ok else "erro", erro=erro,
                    codigo_saida=rc, wall_s=round(time.perf_counter() - t0, 2), wall_solver_s=round(wall_solver, 2),
                    cpu_s=round(ru.ru_utime + ru.ru_stime, 1), maxrss_mb=round(ru.ru_maxrss / 1024, 1),
                    rss_pico_amostrado_mb=max(rss), mem_disponivel_min_mb=min(disp), swap_usado_max_mb=max(swap),
                    threads=threads, memlimit_gb=memlimit, pico_kw=pico, lp_mb=lp_mb, **_ler_log_gurobi(glog)), d
    finally:
        shutil.rmtree(work, ignore_errors=True)        # inclui o multiobj_c.lp (centenas de MB)


def registrar(out_root, row, d, rev):
    det = out_root / "execucoes_gurobi_sequencial.csv"
    if det.exists():                                   # arquivo de uma versão com menos colunas: completa o cabeçalho
        with open(det, newline="") as f:
            antigas = list(csv.DictReader(f))
        if antigas and list(antigas[0]) != CAMPOS:
            with open(det, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=CAMPOS, restval="")
                w.writeheader()
                w.writerows(antigas)
    novo = not det.exists()
    with open(det, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        w.writerow(row)
    geral = dict(tecnica="gurobi", instancia=row["instancia"], status=row["status"], wall_s=row["wall_s"],
                 wall_solver_s=row["wall_solver_s"], maxrss_mb=row["maxrss_mb"], erro=row["erro"])
    rg._registrar(out_root, geral, d, rev, 1)          # execucoes.csv geral e concluido.json (se ok)
    if row["status"] == "ok":                          # acrescenta os campos de recursos ao concluido.json
        cj = d / "concluido.json"
        dados = json.loads(cj.read_text())
        dados.update({k: row[k] for k in CAMPOS if k not in dados}, execucao="run_gurobi_sequencial.py")
        cj.write_text(json.dumps(dados, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--tamanho", type=int, required=True, help="N da frota (ex.: 18000)")
    ap.add_argument("--frotas", nargs="+", choices=ORDEM, default=ORDEM)
    ap.add_argument("--dias", nargs="+", help="restringe a dias YYYYMMDD da seleção")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--memlimit", type=float, default=12.0, help="MemLimit do Gurobi em GB")
    ap.add_argument("--saida", type=Path, default=rg.DEFAULT_OUT)
    ap.add_argument("--tmpdir", type=Path, default=None,
                    help="diretório de trabalho do multiobj_c (padrão: <saida>/_trabalho_gurobi, no disco)")
    ap.add_argument("--refazer", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_root = args.saida.resolve()
    tmpdir = (args.tmpdir or out_root / "_trabalho_gurobi").resolve()
    dias = selecao_dias.select_days()
    if args.dias:
        dias = [p for p in dias if p.stem.split("_")[2] in args.dias]
    tarefas = []
    for frota in dict.fromkeys(args.frotas):              # na ordem dada em --frotas (padrão: ORDEM)
        if args.tamanho not in rg.FROTAS[frota]:
            print(f"[aviso] {frota}: N = {args.tamanho} não existe nesta frota; pulada")
            continue
        for dia in dias:
            d = rg.inst_dir(out_root, "gurobi", args.tamanho, frota, dia)
            if (d / "concluido.json").exists() and not args.refazer:
                continue
            tarefas.append((frota, dia))
    disp, swap = _meminfo()
    print(f"N = {args.tamanho} | {len(tarefas)} instâncias pendentes, uma por vez | Threads {args.threads} | "
          f"MemLimit {args.memlimit} GB | memória disponível agora {disp/1024:.1f} GB, swap usado {swap/1024:.1f} GB",
          flush=True)
    if args.dry_run or not tarefas:
        return
    if not rg.GUROBI_BIN.exists():
        raise FileNotFoundError(f"Compile o Gurobi primeiro: {rg.GUROBI_BIN}")
    rev = rg._git_rev()
    t_ini = time.perf_counter()
    for i, (frota, dia) in enumerate(tarefas, 1):
        row, d = executar(args.tamanho, frota, dia, out_root, args.threads, args.memlimit, tmpdir)
        registrar(out_root, row, d, rev)
        print(f"    {i}/{len(tarefas)} | solver {row['wall_solver_s']:.0f} s | pico RSS {row['rss_pico_amostrado_mb']:.0f} MB | "
              f"disp. mín. {row['mem_disponivel_min_mb']/1024:.1f} GB | swap máx. {row['swap_usado_max_mb']:.0f} MB | "
              f"{row['status_gurobi'][:60]} | decorrido {(time.perf_counter() - t_ini)/3600:.2f} h", flush=True)
    print(f"Concluído em {(time.perf_counter() - t_ini)/3600:.2f} h", flush=True)


if __name__ == "__main__":
    main()
