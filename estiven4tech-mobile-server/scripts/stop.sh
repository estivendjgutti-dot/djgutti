#!/usr/bin/env bash
# Encerramento idempotente: SIGTERM no worker (checkpoint no SQLite) e depois no supervisord.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[ -x "$SUPERVISORCTL" ] || die "venv ausente: rode scripts/install.sh"
stopped=0
if sup_alive; then
  spid="$(ctl pid)"
  info "parando worker (aguarda tarefa atual e checkpoint)..."
  ctl stop worker || true
  ctl shutdown >/dev/null || true
  wait_gone "$spid" 30 || { warn "supervisord não saiu; SIGTERM"; kill -TERM "$spid" 2>/dev/null || true; wait_gone "$spid" 10 || true; }
  stopped=1
else
  spid="$(sup_pid_from_file)"
  if [ -n "$spid" ]; then
    info "supervisord pid $spid sem socket: SIGTERM"
    kill -TERM "$spid" 2>/dev/null || true
    wait_gone "$spid" 100 || { kill -KILL "$spid" 2>/dev/null || true; }
    stopped=1
  fi
fi

wpid="$(locked_worker_pid)"
if [ -n "$wpid" ]; then
  info "worker pid $wpid ainda ativo: SIGTERM"
  kill -TERM "$wpid" 2>/dev/null || true
  wait_gone "$wpid" 95 || { warn "SIGKILL no worker"; kill -KILL "$wpid" 2>/dev/null || true; }
  stopped=1
fi

if have_tmux && tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION" 2>/dev/null || true
fi
rm -f "$SOCK"
[ "$stopped" = 1 ] && info "parado." || info "já estava parado."
