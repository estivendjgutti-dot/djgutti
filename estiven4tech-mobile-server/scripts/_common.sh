# Funções comuns dos scripts. Uso: . "$(dirname "$0")/_common.sh"
# Resolve o projeto pelo caminho do script, não pelo diretório atual.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV="$PROJECT_DIR/.venv"
PY="$VENV/bin/python"
SUPERVISORD="$VENV/bin/supervisord"
SUPERVISORCTL="$VENV/bin/supervisorctl"
CONF="$PROJECT_DIR/config/supervisord.conf"
RUN_DIR="$PROJECT_DIR/run"
SOCK="$RUN_DIR/supervisor.sock"
PIDFILE="$RUN_DIR/supervisord.pid"
LOCKFILE="$RUN_DIR/worker.lock"
SESSION="${E4T_TMUX_SESSION:-e4t-server}"
export TZ="${TZ:-America/Sao_Paulo}"

info() { printf '%s\n' "$*"; }
warn() { printf 'aviso: %s\n' "$*" >&2; }
die()  { printf 'erro: %s\n' "$*" >&2; exit 1; }

ensure_dirs() {
  umask 077
  mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs" "$RUN_DIR" "$PROJECT_DIR/backups"
  chmod 700 "$PROJECT_DIR/data" "$PROJECT_DIR/logs" "$RUN_DIR" "$PROJECT_DIR/backups"
}

ctl() { "$SUPERVISORCTL" -c "$CONF" "$@"; }

# supervisord responde no socket? (prova melhor que PID sozinho)
sup_alive() { [ -S "$SOCK" ] && ctl pid >/dev/null 2>&1; }

# PID do arquivo, validado pela linha de comando (evita PID reciclado pelo sistema)
pid_matches() {
  local pid="$1" pattern="$2"
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -qF -- "$pattern"
}

sup_pid_from_file() {
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if pid_matches "$pid" "supervisord" && pid_matches "$pid" "$CONF"; then echo "$pid"; fi
}

# worker que segura o lock (pode ser órfão se o supervisord morreu com kill -9)
locked_worker_pid() {
  local pid
  pid="$(head -n1 "$LOCKFILE" 2>/dev/null || true)"
  # cmdline do supervisor contém ".../config/../src/worker.py": checa projeto e script separadamente
  if pid_matches "$pid" "src/worker.py" && pid_matches "$pid" "$PROJECT_DIR/"; then echo "$pid"; fi
}

worker_state() {
  local line
  line="$(ctl status worker 2>/dev/null || true)"
  set -- $line
  echo "${2:-DESCONHECIDO}"
}

wait_gone() {  # wait_gone PID SEGUNDOS
  local pid="$1" i=0
  while [ "$i" -lt "$2" ] && kill -0 "$pid" 2>/dev/null; do sleep 1; i=$((i + 1)); done
  ! kill -0 "$pid" 2>/dev/null
}

have_tmux() { command -v tmux >/dev/null 2>&1; }
