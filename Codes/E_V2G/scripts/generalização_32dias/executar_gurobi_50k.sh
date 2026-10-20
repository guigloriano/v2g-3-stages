#!/usr/bin/env bash
# executar_gurobi_50k.sh — Gurobi da generalização com as frotas completas do Boulder (N = 50.336) nos quatro
# cenários e nos 32 dias (128 instâncias), na ordem B4 (bld_s5), B3 (bld_s4), B2 (bld_s2), B1 (bld_s1).
#
# B4 é o único com risco de memória (projeção: ~11 GB na mediana, ~12 GB no pior dia): instâncias que
# atingirem o MemLimit são registradas como erro e a rotina segue para os demais cenários.
#
# Espera terminar executar_gurobi_39k_n1.sh e executar_gurobi_3k_9k_seq.sh (verifica a cada 60 s).
# Uma instância por vez (run_gurobi_sequencial.py), Threads 4, diretório de trabalho no disco.
# MemLimit do Gurobi definido antes de cada frota pela memória disponível na hora: disponível − 1,5 GB,
# limitado a [12, 15] GB (com o VS Code fechado, ~15 GB). Instância que atingir o limite é registrada
# como erro e a rotina segue.
# Registro: o mesmo de run_gurobi_sequencial.py, mais o log deste script (gurobi_50k.log).
# Retomável: rodar de novo pula as instâncias com concluido.json.
#
# Uso (desacoplado do terminal):
#   setsid nohup bash executar_gurobi_50k.sh > /dev/null 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
SAIDA="$(cd ../../../../Results/V2G/CampoGrande/generalization && pwd)"
LOG="$SAIDA/gurobi_50k.log"
N=50336

memlimit() {   # GB: memória disponível agora − 1,5, entre 12 e 15
  awk '/^MemAvailable:/ {m = $2 / 1048576 - 1.5; if (m < 12) m = 12; if (m > 15) m = 15; printf "%.1f", m}' /proc/meminfo
}

{
  echo "=== agendado $(date -Iseconds): aguardando executar_gurobi_39k_n1.sh e executar_gurobi_3k_9k_seq.sh"
  while pgrep -f "executar_gurobi_(39k_n1|3k_9k_seq)\.sh" > /dev/null \
        || pgrep -f "run_gurobi_sequencial.py --tamanho (39000|32330|9000|3000) " > /dev/null; do
    sleep 60
  done
  echo "=== início $(date -Iseconds) | $(free -h | awk 'NR==2{print "RAM disponível " $7}')"
  for FROTA in bld_s5 bld_s4 bld_s2 bld_s1; do
    ML=$(memlimit)
    echo "=== $FROTA, N = $N | $(date -Iseconds) | MemLimit $ML GB"
    python3 run_gurobi_sequencial.py --tamanho $N --frotas $FROTA --memlimit "$ML"
    echo "=== $FROTA terminado $(date -Iseconds) (código $?)"
  done
  echo "=== fim $(date -Iseconds)"
} >> "$LOG" 2>&1
