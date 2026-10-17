#!/usr/bin/env bash
# Validação automática (critérios 1-6 e 10 do AGENTS.md §10) numa CÓPIA isolada do projeto.
# Não toca data/, logs/ nem a sessão tmux de produção. Leva ~2-3 minutos.
# Uso: scripts/validate.sh [--keep]   (--keep mantém a pasta temporária para inspeção)
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
[ -x "$PY" ] || die "venv ausente: rode scripts/install.sh"
echo "== testes unitários"
"$PY" -m unittest discover -s "$PROJECT_DIR/tests" -p 'test_*.py'
echo "== integração (supervisor + tmux em sandbox)"
exec "$PY" "$PROJECT_DIR/tests/integration.py" "$@"
