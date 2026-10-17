#!/usr/bin/env bash
# Preparação idempotente: pastas, venv, supervisor fixado, .env a partir do exemplo.
# Uso: scripts/install.sh         (só verifica pacotes do sistema e mostra o comando apt)
#      scripts/install.sh --apt   (instala os pacotes ausentes via apt, com sudo se preciso)
# Não faz upgrade geral do sistema.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

PKGS="ca-certificates curl git ripgrep tmux python3 python3-venv python3-pip sqlite3 tzdata"
case "$PROJECT_DIR" in
  /sdcard/*|/storage/*|/mnt/sdcard/*) die "não instale em armazenamento compartilhado ($PROJECT_DIR); use ~/estiven4tech-mobile-server" ;;
esac

missing=""
if command -v dpkg-query >/dev/null 2>&1; then
  for p in $PKGS; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
  done
fi
if [ -n "$missing" ]; then
  if [ "${1:-}" = "--apt" ]; then
    SUDO=""; [ "$(id -u)" -ne 0 ] && { command -v sudo >/dev/null 2>&1 && SUDO=sudo || die "sem root e sem sudo para instalar:$missing"; }
    $SUDO apt-get update
    $SUDO apt-get install -y $missing
  else
    warn "pacotes ausentes:$missing"
    die "instale com: sudo apt update && sudo apt install -y$missing   (ou rode scripts/install.sh --apt)"
  fi
fi

ensure_dirs
if [ ! -x "$PY" ]; then
  info "criando venv em .venv"
  python3 -m venv "$VENV"
fi
want="$(grep -E '^supervisor==' "$PROJECT_DIR/requirements.lock" | cut -d= -f3)"
have="$("$PY" -c 'import importlib.metadata as m; print(m.version("supervisor"))' 2>/dev/null || true)"
if [ "$have" != "$want" ]; then
  info "instalando dependências fixadas (requirements.lock)"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r "$PROJECT_DIR/requirements.lock"
else
  info "supervisor $have já instalado"
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
  (umask 077; cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env")
  info ".env criado a partir de .env.example (sem credenciais)"
fi
chmod 600 "$PROJECT_DIR/.env"

if [ -f "$PROJECT_DIR/CLAUDE.md" ]; then
  grep -qxF '@AGENTS.md' "$PROJECT_DIR/CLAUDE.md" || printf '\n@AGENTS.md\n' >>"$PROJECT_DIR/CLAUDE.md"
else
  printf '@AGENTS.md\n' >"$PROJECT_DIR/CLAUDE.md"
fi

"$PY" -c "import sys; sys.path.insert(0, '$PROJECT_DIR/src'); import db; c = db.connect(); print('banco pronto: schema v%d, journal=%s' % (db.migrate(c), c.execute('PRAGMA journal_mode').fetchone()[0]))"
info "pronto. Próximo passo: scripts/start.sh"
