#!/usr/bin/env bash
# Fase A — diagnóstico do ambiente real. Não imprime variáveis de ambiente nem credenciais.
# Gera docs/diagnostico-<data>.txt. Rode no UserLAnd ANTES de instalar dependências e de novo depois.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
OUT="$PROJECT_DIR/docs/diagnostico-$(date -u +%Y%m%dT%H%M%SZ).txt"
mkdir -p "$PROJECT_DIR/docs"

run() {
  printf '\n$ %s\n' "$*"
  bash -c "$*" 2>&1 | sed -e 's/[A-Za-z0-9_-]*\(token\|TOKEN\|key\|KEY\|secret\|SECRET\)[A-Za-z0-9_-]*=[^ ]*/<redigido>/g'
  printf '[saída %s]\n' "${PIPESTATUS[0]}"
}

{
  echo "# Diagnóstico estiven4tech-mobile-server"
  echo "# coletado em $(date -Is) | projeto: $PROJECT_DIR"
  echo "## Fase A (AGENTS.md §5)"
  run 'uname -m'
  run 'uname -sr'
  run 'cat /etc/os-release'
  run 'id'
  run 'getconf LONG_BIT'
  run 'getconf GNU_LIBC_VERSION'
  run 'df -h "$HOME"'
  run 'df -T "$HOME" 2>/dev/null || mount 2>/dev/null | grep -E " / | $HOME " | head -5'
  run 'free -h'
  run 'nproc'
  run 'ps -p 1 -o pid,comm,args'
  run 'date -Is; echo "TZ=${TZ:-<não definido>}"; ls -l /etc/localtime 2>/dev/null'
  run 'command -v python3 && python3 --version'
  run 'command -v tmux && tmux -V'
  run 'command -v sqlite3 && sqlite3 --version'
  run 'command -v sudo; command -v systemctl; command -v docker; command -v pkg; true'

  echo; echo "## Indícios de PRoot / limitações"
  run 'grep -E "^(TracerPid|Seccomp|NoNewPrivs):" /proc/self/status'
  run 'cat /proc/1/comm 2>/dev/null; readlink /proc/1/exe 2>/dev/null; true'
  run 'echo "PROOT_*: $(env | grep -c "^PROOT_" ) variável(is) (valores omitidos)"'
  run 'cat /proc/meminfo 2>/dev/null | head -3   # pode refletir o Android inteiro'
  run 'cat /sys/class/power_supply/battery/capacity /sys/class/power_supply/battery/status 2>/dev/null || echo "bateria: não acessível pelo Linux"'

  echo; echo "## Testes funcionais no disco do projeto"
  T="$(mktemp -d "$PROJECT_DIR/.diag.XXXX")"
  run "python3 - <<'PY'
import fcntl, os, socket, sqlite3, sys
d = '$T'
fh = open(os.path.join(d, 'l'), 'w')
fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB); print('flock: OK')
c = sqlite3.connect(os.path.join(d, 't.db'))
print('sqlite', sqlite3.sqlite_version, 'journal_mode=wal ->', c.execute('PRAGMA journal_mode=wal').fetchone()[0])
c.execute('create table t(x)'); c.execute('insert into t values (1)'); c.commit()
c2 = sqlite3.connect(os.path.join(d, 't.db')); print('leitura concorrente:', c2.execute('select count(*) from t').fetchone()[0])
s = socket.socket(socket.AF_UNIX); s.bind(os.path.join(d, 's.sock')); s.listen(1); print('socket unix: OK')
import signal; signal.setitimer(signal.ITIMER_REAL, 0); print('setitimer: OK')
PY"
  run "command -v tmux >/dev/null && tmux -L e4tdiag new-session -d -s diag 'sleep 5' && tmux -L e4tdiag has-session -t diag && echo 'tmux: OK' && tmux -L e4tdiag kill-server"
  rm -rf "$T"
  echo; echo "## Itens que só o aparelho mostra (preencher em docs/DIAGNOSTICO.md)"
  echo "Android/One UI, RAM física, armazenamento livre, versão do UserLAnd, distro escolhida no app."
} | tee "$OUT"
chmod 600 "$OUT"
echo; echo "salvo em: $OUT"
