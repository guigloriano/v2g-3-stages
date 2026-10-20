#!/usr/bin/env bash
# executar_n1_dia_periodico.sh — refaz a N1 (nor_s1) com o dia periódico (2026-10-08): as sessões que passam da
# meia-noite continuam na madrugada do mesmo dia (coluna wrap_end_ts dos pp_*_nor_s1.csv), em vez de serem
# cortadas no slot 144. As frotas do Boulder não têm sessões após a meia-noite e não mudam.
#   1) V2G nivelado e LCVF: 4 tamanhos × 32 dias (run_generalizacao.py, 4 processos)
#   2) Gurobi, uma instância por vez (run_gurobi_sequencial.py, Threads 4, MemLimit 12 GB), do menor para o maior
# Os resultados antigos (horizonte cortado) estão em generalization/_n1_horizonte_cortado/.
# Retomável: rodar de novo pula as instâncias com concluido.json.
#
# Uso (desacoplado do terminal, numa linha própria):
#   setsid nohup bash executar_n1_dia_periodico.sh > /dev/null 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
SAIDA="$(cd ../../../../Results/V2G/CampoGrande/generalization && pwd)"
LOG="$SAIDA/n1_dia_periodico.log"

{
  echo "=== início $(date -Iseconds) | $(free -h | awk 'NR==2{print "RAM disponível " $7}')"
  python3 run_generalizacao.py --tecnicas v2g lcvf --jobs 4 --frotas nor_s1
  echo "=== V2G e LCVF terminados $(date -Iseconds) (código $?)"
  for n in 3000 9000 18000 32330; do
    python3 run_gurobi_sequencial.py --tamanho "$n" --frotas nor_s1
    echo "=== Gurobi N = $n terminado $(date -Iseconds) (código $?)"
  done
  echo "=== fim $(date -Iseconds)"
} >> "$LOG" 2>&1
