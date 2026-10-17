#!/usr/bin/env bash
# Inicia app ou worker da secretária de TESTE (chamado pelo supervisor).
# Uso: secretaria/run-node.sh app|worker
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/_common.sh"
. "$PROJECT_DIR/secretaria/_sec.sh"
sec_load_env

# Travas: envio real, WAHA e e-mail só com autorização explícita no .env.secretaria.
if [ "${SECRETARIA_PERMITIR_ENVIO_REAL:-nao}" != "sim" ]; then
  [ "${WHATSAPP_SEND_MODE:-off}" = off ] || die "WHATSAPP_SEND_MODE=$WHATSAPP_SEND_MODE sem SECRETARIA_PERMITIR_ENVIO_REAL=sim"
  [ -z "${WAHA_URL:-}" ] || die "WAHA_URL definido sem SECRETARIA_PERMITIR_ENVIO_REAL=sim"
  [ -z "${RESEND_API_KEY:-}" ] || die "RESEND_API_KEY definido sem SECRETARIA_PERMITIR_ENVIO_REAL=sim"
fi

# Espera o Postgres (sobe junto no supervisor).
for _ in $(seq 1 30); do sec_pg_ready && break; sleep 1; done
sec_pg_ready || die "Postgres não respondeu em 30 s"

cd "$SECRETARIA_APP_DIR"
# Um processo Node só: tsx carregado como loader (o binário tsx criaria um processo pai extra, ~60 MB).
export NODE_OPTIONS="--import=$PROJECT_DIR/secretaria/bind-loopback.mjs ${NODE_OPTIONS:-}"
case "${1:-}" in
  app) exec node --import tsx src/server.ts ;;
  worker) exec node --import tsx src/worker.ts ;;
  *) die "uso: run-node.sh app|worker" ;;
esac
