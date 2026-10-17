#!/usr/bin/env bash
# Relatório dos testes longos: scripts/relatorio.sh [--horas 8] [--job demo_timestamp]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
[ -x "$PY" ] || die "venv ausente: rode scripts/install.sh"
exec "$PY" "$PROJECT_DIR/src/report.py" "$@"
