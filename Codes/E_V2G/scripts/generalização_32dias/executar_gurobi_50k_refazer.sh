#!/usr/bin/env bash
# executar_gurobi_50k_refazer.sh — refaz as instâncias do Gurobi com N = 50.336 que terminaram em erro (sem
# concluido.json), com MemLimit maior. Motivo: inst_50336_bld_s5_cgr_20221017_spring_mon parou com
# "error code 10001" (memória) com MemLimit 14,5 GB, embora o RSS medido fosse ~11,8 GB: o Gurobi conta no
# MemLimit a memória que reserva, não só a que está em uso.
#
# Espera o PID dado (o executar_gurobi_50k.sh) terminar, verificando com kill -0 a cada 60 s. A espera usa o
# PID e não pgrep -f, porque pgrep -f também encontra o shell que lançou o script (foi o que travou a fila
# em 2026-10-06).
# run_gurobi_sequencial.py pula as instâncias com concluido.json, então só as que falharam rodam de novo.
# Frotas: B4, B3, B2 e B1, para pegar erros que apareçam também nas outras frotas.
# MemLimit: 17 GB (19,4 GB de RAM, ~1,8 GB usados sem o Gurobi, pico de RSS de ~12,3 GB no B4).
# Registro: o mesmo de run_gurobi_sequencial.py, mais o log deste script (gurobi_50k_refazer.log).
#
# Uso (desacoplado do terminal e com a suspensão bloqueada durante a espera e a execução):
#   setsid nohup systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block \
#     --who="Gurobi (refazer 50k)" --why="refaz instâncias do Gurobi com erro" \
#     bash executar_gurobi_50k_refazer.sh <PID do executar_gurobi_50k.sh> > /dev/null 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
SAIDA="$(cd ../../../../Results/V2G/CampoGrande/generalization && pwd)"
LOG="$SAIDA/gurobi_50k_refazer.log"
ESPERA="${1:?informe o PID do executar_gurobi_50k.sh}"
MEMLIMIT=17

{
  echo "=== agendado $(date -Iseconds): aguardando o PID $ESPERA terminar"
  while kill -0 "$ESPERA" 2>/dev/null; do
    sleep 60
  done
  echo "=== início $(date -Iseconds) | $(free -h | awk 'NR==2{print "RAM disponível " $7}') | MemLimit $MEMLIMIT GB"
  python3 run_gurobi_sequencial.py --tamanho 50336 --frotas bld_s5 bld_s4 bld_s2 bld_s1 --memlimit "$MEMLIMIT"
  echo "=== fim $(date -Iseconds) (código $?)"
} >> "$LOG" 2>&1
