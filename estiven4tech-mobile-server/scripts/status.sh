#!/usr/bin/env bash
# Saúde: processos (supervisor), heartbeat, último sucesso por job, fila e rede.
# Uso: scripts/status.sh [--rede] [--json]     Saída: 0 saudável, 1 atenção, 2 parado
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[ -x "$PY" ] || die "venv ausente: rode scripts/install.sh"
if sup_alive; then
  state="$(worker_state)"
  tm="não"; have_tmux && tmux has-session -t "$SESSION" 2>/dev/null && tm="sim (tmux attach -t $SESSION)"
  case " $* " in *" --json "*) ;; *)
    info "supervisord: ATIVO pid $(ctl pid) | sessão tmux: $tm"
    ctl status 2>/dev/null | sed 's/^/  /'
  esac
else
  state="PARADO"
  case " $* " in *" --json "*) ;; *) info "supervisord: PARADO (inicie com scripts/start.sh)";; esac
fi
"$PY" "$PROJECT_DIR/src/status.py" --worker-state "$state" "$@"
