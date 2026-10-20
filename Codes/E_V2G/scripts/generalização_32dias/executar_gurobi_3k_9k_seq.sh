#!/usr/bin/env bash
# executar_gurobi_3k_9k_seq.sh — refaz o Gurobi com N = 9.000 e 3.000 (5 frotas × 32 dias = 320 instâncias),
# agora uma instância por vez, para medir tempo e memória nas mesmas condições de 18.000 e 39.000
# (na grade original rodaram 2 Gurobi simultâneos de 4 threads em 4 núcleos físicos, o que infla os tempos).
#
# Espera terminar o executar_gurobi_39k_n1.sh (verifica a cada 60 s) e só então começa.
# Ordem: das maiores para as menores, 9.000 e depois 3.000; em cada tamanho B4, B3, B2, B1, N1.
# --refazer: grava por cima dos resultados existentes (o Gurobi é determinístico com os mesmos parâmetros; os
# valores anteriores ficaram em analise/exato_3k_9k_antes_refazer.csv para conferência).
# Registro: o mesmo de run_gurobi_sequencial.py, mais o log deste script (gurobi_3k_9k_seq.log).
#
# Uso (desacoplado do terminal):
#   setsid nohup bash executar_gurobi_3k_9k_seq.sh > /dev/null 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
SAIDA="$(cd ../../../../Results/V2G/CampoGrande/generalization && pwd)"
LOG="$SAIDA/gurobi_3k_9k_seq.log"
FROTAS="bld_s5 bld_s4 bld_s2 bld_s1 nor_s1"

{
  echo "=== agendado $(date -Iseconds): aguardando o fim de executar_gurobi_39k_n1.sh"
  while pgrep -f "executar_gurobi_39k_n1.sh" > /dev/null || pgrep -f "run_gurobi_sequencial.py --tamanho (39000|32330)" > /dev/null; do
    sleep 60
  done
  echo "=== início $(date -Iseconds) | $(free -h | awk 'NR==2{print "RAM disponível " $7}')"
  python3 run_gurobi_sequencial.py --tamanho 9000 --frotas $FROTAS --refazer
  echo "=== N = 9.000 terminado $(date -Iseconds) (código $?)"
  python3 run_gurobi_sequencial.py --tamanho 3000 --frotas $FROTAS --refazer
  echo "=== N = 3.000 terminado $(date -Iseconds) (código $?)"
  echo "=== fim $(date -Iseconds)"
} >> "$LOG" 2>&1
