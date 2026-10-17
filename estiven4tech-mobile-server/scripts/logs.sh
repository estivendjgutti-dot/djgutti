#!/usr/bin/env bash
# Uso: scripts/logs.sh [worker|supervisor|events|todos] [-f] [-n LINHAS]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

which=worker; follow=""; lines=50
while [ $# -gt 0 ]; do
  case "$1" in
    worker|supervisor|events|todos) which="$1" ;;
    -f) follow="-F" ;;
    -n) shift; lines="${1:?}" ;;
    *) die "uso: scripts/logs.sh [worker|supervisor|events|todos] [-f] [-n LINHAS]" ;;
  esac
  shift
done
L="$PROJECT_DIR/logs"
case "$which" in
  worker) files=("$L/worker.log") ;;
  supervisor) files=("$L/supervisord.log") ;;
  events) files=("$L/events.log") ;;
  todos) files=("$L/supervisord.log" "$L/events.log" "$L/worker.log") ;;
esac
existing=()
for f in "${files[@]}"; do [ -f "$f" ] && existing+=("$f"); done
[ "${#existing[@]}" -gt 0 ] || die "nenhum log ainda em $L (o servidor já foi iniciado?)"
exec tail -n "$lines" $follow "${existing[@]}"
