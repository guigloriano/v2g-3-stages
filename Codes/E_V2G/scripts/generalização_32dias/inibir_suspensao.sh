#!/usr/bin/env bash
# inibir_suspensao.sh — impede suspender, hibernar, desligar a tela por ociosidade e o efeito de fechar a tampa
# enquanto os PIDs dados estiverem vivos (fila do Gurobi). Termina sozinho quando todos acabarem.
#
# Uso:  setsid nohup systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block \
#         --who="Gurobi (generalização)" --why="fila de execuções do Gurobi" \
#         bash inibir_suspensao.sh <PID> [<PID> ...] > /dev/null 2>&1 < /dev/null &
# Conferir: systemd-inhibit --list      Cancelar: kill <PID deste script>
while :; do
  vivo=0
  for p in "$@"; do kill -0 "$p" 2>/dev/null && vivo=1; done
  [ "$vivo" -eq 1 ] || exit 0
  sleep 60
done
