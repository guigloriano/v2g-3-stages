#!/usr/bin/env bash
# executar_gurobi_39k_n1.sh — Gurobi da generalização nas frotas maiores, das maiores para as menores:
#   1) N = 39.000: B4 (bld_s5), B3 (bld_s4), B2 (bld_s2), B1 (bld_s1)   — 128 instâncias
#   2) N1 completa (nor_s1, N = 32.330)                                  —  32 instâncias
# Uma instância por vez (run_gurobi_sequencial.py), Threads 4, MemLimit 12 GB, diretório de trabalho no disco.
# Registro: exact_gurobi/<frota>/inst_<N>_*/{gurobi.log.gz,stdout.log.gz,recursos.csv,concluido.json},
#           execucoes_gurobi_sequencial.csv, execucoes.csv e o log deste script (gurobi_39k_n1.log).
# Retomável: rodar de novo pula as instâncias com concluido.json.
#
# Uso (desacoplado do terminal):
#   setsid nohup bash executar_gurobi_39k_n1.sh > /dev/null 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
SAIDA="$(cd ../../../../Results/V2G/CampoGrande/generalization && pwd)"
LOG="$SAIDA/gurobi_39k_n1.log"

{
  echo "=== início $(date -Iseconds) | $(free -h | awk 'NR==2{print "RAM disponível " $7}')"
  python3 run_gurobi_sequencial.py --tamanho 39000 --frotas bld_s5 bld_s4 bld_s2 bld_s1
  echo "=== N = 39.000 terminado $(date -Iseconds) (código $?)"
  python3 run_gurobi_sequencial.py --tamanho 32330 --frotas nor_s1
  echo "=== N1 completa terminada $(date -Iseconds) (código $?)"
  echo "=== fim $(date -Iseconds)"
} >> "$LOG" 2>&1
