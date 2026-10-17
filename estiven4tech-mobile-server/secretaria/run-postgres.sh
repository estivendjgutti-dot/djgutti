#!/usr/bin/env bash
# Postgres da secretária de TESTE em foreground (chamado pelo supervisor). Só socket Unix, sem TCP.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/_common.sh"
. "$PROJECT_DIR/secretaria/_sec.sh"
[ "$(id -u)" -ne 0 ] || die "o PostgreSQL não roda como root; use o usuário normal do UserLAnd"
mkdir -p "$SEC_SOCKDIR" && chmod 700 "$SEC_SOCKDIR"
exec "$(sec_pg_bin)/postgres" -D "$SEC_PGDATA"
