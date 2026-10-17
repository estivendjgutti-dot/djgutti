#!/usr/bin/env bash
# Atalho para o supervisorctl deste projeto. Ex.: scripts/ctl.sh status | restart worker | tail -f worker
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
sup_alive || die "supervisord parado (scripts/start.sh)"
exec "$SUPERVISORCTL" -c "$CONF" "$@"
