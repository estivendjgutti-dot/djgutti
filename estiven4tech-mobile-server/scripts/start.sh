#!/usr/bin/env bash
# Inicialização idempotente: nunca cria um segundo supervisord/worker.
# Uso: scripts/start.sh            (supervisord -n dentro de tmux, sessão "e4t-server")
#      scripts/start.sh --foreground  (sem tmux: supervisord -n neste terminal; Ctrl+C encerra)
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

MODE=tmux
case "${1:-}" in
  --foreground|--no-tmux) MODE=foreground ;;
  "") ;;
  *) die "opção desconhecida: $1 (use --foreground)" ;;
esac

[ -x "$SUPERVISORD" ] || die "venv/supervisor ausentes: rode scripts/install.sh"
case "$PROJECT_DIR" in
  /sdcard/*|/storage/*|/mnt/sdcard/*) die "projeto em armazenamento compartilhado ($PROJECT_DIR); mova para ~/" ;;
esac
ensure_dirs

# Trava contra dois start.sh simultâneos (mkdir é atômico; não depende de flock(1)).
STARTLOCK="$RUN_DIR/start.lock.d"
if ! mkdir "$STARTLOCK" 2>/dev/null; then
  if [ -n "$(find "$STARTLOCK" -maxdepth 0 -mmin +2 2>/dev/null)" ]; then
    rmdir "$STARTLOCK" 2>/dev/null || true
    mkdir "$STARTLOCK" || die "não foi possível obter a trava de início"
  else
    die "outro start.sh em andamento"
  fi
fi
trap 'rmdir "$STARTLOCK" 2>/dev/null || true' EXIT

# 1) Já está rodando? Só garante o worker.
if sup_alive; then
  state="$(worker_state)"
  case "$state" in
    RUNNING|STARTING) info "já em execução: supervisord pid $(ctl pid), worker $state. Nada a fazer." ;;
    *) info "supervisord ativo, worker em $state: iniciando worker"; ctl start worker || true ;;
  esac
  exit 0
fi

# 2) supervisord vivo sem socket respondendo: não duplicar.
spid="$(sup_pid_from_file)"
if [ -n "$spid" ]; then
  die "supervisord (pid $spid) vivo mas o socket não responde. Rode scripts/stop.sh e depois start.sh"
fi
rm -f "$PIDFILE" "$SOCK"

# 3) Worker órfão (supervisord morto por kill/Android) segurando o lock: encerra com checkpoint.
wpid="$(locked_worker_pid)"
if [ -n "$wpid" ]; then
  warn "worker órfão pid $wpid encontrado; enviando SIGTERM (checkpoint)"
  kill -TERM "$wpid" 2>/dev/null || true
  wait_gone "$wpid" 95 || { warn "worker não saiu; SIGKILL"; kill -KILL "$wpid" 2>/dev/null || true; wait_gone "$wpid" 5 || true; }
fi

if [ "$MODE" = foreground ] || ! have_tmux; then
  have_tmux || warn "tmux não encontrado; usando modo foreground"
  info "iniciando supervisord em foreground (Ctrl+C encerra com checkpoint)"
  exec "$SUPERVISORD" -n -c "$CONF"
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  warn "sessão tmux $SESSION existia sem supervisord respondendo; recriando"
  tmux kill-session -t "$SESSION" 2>/dev/null || true
fi
if ! tmux new-session -d -s "$SESSION" "exec '$SUPERVISORD' -n -c '$CONF'"; then
  die "tmux falhou. Alternativa: scripts/start.sh --foreground (veja docs/OPERACAO.md)"
fi

for _ in $(seq 1 20); do sup_alive && break; sleep 1; done
sup_alive || die "supervisord não respondeu em 20 s; veja: scripts/logs.sh supervisor"
for _ in $(seq 1 15); do [ "$(worker_state)" = RUNNING ] && break; sleep 1; done
info "supervisord pid $(ctl pid) na sessão tmux '$SESSION'"
ctl status || true
[ "$(worker_state)" = RUNNING ] || { warn "worker ainda não está RUNNING; veja scripts/logs.sh worker"; exit 1; }
