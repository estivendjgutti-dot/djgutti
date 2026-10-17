#!/usr/bin/env bash
# Backup online consistente (API de backup do SQLite) + código/config, com retenção 7 diários + 4 semanais.
# Pode rodar com o worker ativo. Não inclui .venv, caches nem .env (use --with-env só se souber o que faz).
# Uso: scripts/backup.sh [--with-env]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
[ -x "$PY" ] || die "venv ausente: rode scripts/install.sh"
ensure_dirs
exec "$PY" "$PROJECT_DIR/src/backup.py" create "$@"
