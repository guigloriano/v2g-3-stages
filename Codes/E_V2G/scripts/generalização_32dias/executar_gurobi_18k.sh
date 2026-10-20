#!/usr/bin/env bash
# executar_gurobi_18k.sh — Gurobi da generalização com N = 18.000 nas 5 frotas e 32 dias (160 instâncias).
# Reproduz a execução de 2026-10-05 (01:07–04:45, 3,58 h, 160/160 ótimas): uma instância por vez
# (run_gurobi_sequencial.py), Threads 4, MemLimit 12 GB, ordem padrão B1, B2, N1, B3, B4.
# Diferença em relação àquela execução: o diretório de trabalho agora fica no disco
# (<saida>/_trabalho_gurobi) e não no /tmp (tmpfs, RAM); os resultados do solver não mudam.
# Registro: exact_gurobi/<frota>/inst_18000_*/{gurobi.log.gz,stdout.log.gz,recursos.csv,concluido.json},
#           execucoes_gurobi_sequencial.csv, execucoes.csv e o log deste script (gurobi_18000.log).
# Retomável: rodar de novo pula as instâncias com concluido.json (use --refazer para refazer tudo).
#
# Uso (desacoplado do terminal):
#   setsid nohup bash executar_gurobi_18k.sh > /dev/null 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
SAIDA="$(cd ../../../../Results/V2G/CampoGrande/generalization && pwd)"
LOG="$SAIDA/gurobi_18000.log"

{
  echo "=== início $(date -Iseconds) | $(free -h | awk 'NR==2{print "RAM disponível " $7}')"
  python3 run_gurobi_sequencial.py --tamanho 18000 "$@"
  echo "=== fim $(date -Iseconds) (código $?)"
} >> "$LOG" 2>&1
