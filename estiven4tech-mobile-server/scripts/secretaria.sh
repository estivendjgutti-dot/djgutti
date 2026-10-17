#!/usr/bin/env bash
# Secretária virtual de TESTE (cópia do secretaria-barber/poc-whatsapp) rodando no celular sem Docker.
# Node 22 + PostgreSQL local (só socket Unix) sob o mesmo supervisord. Envio real DESLIGADO.
#
# Uso: scripts/secretaria.sh instalar | ativar | desativar | status | testar | simular | migrar | backup | operador | psql
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
. "$PROJECT_DIR/secretaria/_sec.sh"

need_non_root() { [ "$(id -u)" -ne 0 ] || die "PostgreSQL não roda como root: use o usuário normal do UserLAnd"; }

install_node() {
  if [ -x "$SEC_NODE_DIR/bin/node" ]; then info "Node $("$SEC_NODE_DIR/bin/node" -v) já instalado"; return; fi
  local arch sha tmp name
  arch="$(sec_arch)"; name="node-v$SEC_NODE_VERSION-linux-$arch.tar.xz"
  eval "sha=\$SEC_NODE_SHA_$arch"
  tmp="$(mktemp -d)"
  info "baixando $name de nodejs.org"
  curl -fsSL "https://nodejs.org/dist/v$SEC_NODE_VERSION/$name" -o "$tmp/$name"
  echo "$sha  $tmp/$name" | sha256sum -c - >/dev/null || { rm -rf "$tmp"; die "SHA-256 do Node não confere; download descartado"; }
  mkdir -p "$SEC_NODE_BASE"
  tar -xJf "$tmp/$name" -C "$SEC_NODE_BASE"
  rm -rf "$tmp"
  info "Node $("$SEC_NODE_DIR/bin/node" -v) instalado em $SEC_NODE_DIR"
}

write_env() {
  [ -f "$SECRETARIA_ENV" ] && { info ".env.secretaria já existe (mantido)"; return; }
  local py="python3"
  (umask 077; "$py" - "$SECRETARIA_ENV" "$SEC_SOCKDIR" "$HOME/secretaria-barber/poc-whatsapp" <<'PY'
import base64, secrets, sys
path, sock, appdir = sys.argv[1:4]
lines = [
    "# Secretária de TESTE no celular. Gerado por scripts/secretaria.sh; chmod 600; nunca versionar.",
    "# Segredos aleatórios locais: NÃO são os da VPS de produção.",
    "SECRETARIA_APP_DIR=%s" % appdir,
    "TZ=America/Sao_Paulo",
    "PORT=3100",
    "PUBLIC_BASE_URL=http://127.0.0.1:3100",
    "WEBHOOK_URL=http://127.0.0.1:3100/webhooks/meta",
    "DATABASE_URL=postgres://secretaria@localhost:5433/secretaria?host=%s" % sock,
    "META_APP_SECRET=%s" % secrets.token_hex(32),
    "META_WEBHOOK_VERIFY_TOKEN=%s" % secrets.token_hex(16),
    "ADMIN_TOKEN=%s" % secrets.token_hex(32),
    "SECRETS_KEY=%s" % base64.b64encode(secrets.token_bytes(32)).decode(),
    "META_ACCESS_TOKEN=",
    "META_GRAPH_VERSION=v22.0",
    "# off = nunca chama a Meta. Mudar exige SECRETARIA_PERMITIR_ENVIO_REAL=sim e autorização do proprietário.",
    "WHATSAPP_SEND_MODE=off",
    "SECRETARIA_PERMITIR_ENVIO_REAL=nao",
    "# IA: vazio = resposta fixa de teste. Com chave, cada mensagem simulada gera custo na OpenAI.",
    "OPENAI_API_KEY=",
    "OPENAI_MODEL=gpt-4o-mini",
    "GOOGLE_OAUTH_CLIENT_ID=",
    "GOOGLE_OAUTH_CLIENT_SECRET=",
    "WAHA_URL=",
    "WAHA_API_KEY=",
    "RESEND_API_KEY=",
    "RESEND_WEBHOOK_SECRET=",
]
with open(path, "w") as fh:
    fh.write("\n".join(lines) + "\n")
PY
  )
  chmod 600 "$SECRETARIA_ENV"
  info ".env.secretaria criado com segredos aleatórios locais e envio desligado"
}

init_cluster() {
  local bin; bin="$(sec_pg_bin)"
  if [ -f "$SEC_PGDATA/PG_VERSION" ]; then info "cluster PostgreSQL já existe"; return; fi
  info "criando cluster PostgreSQL em data/secretaria-pg"
  mkdir -p "$(dirname "$SEC_PGDATA")"
  "$bin/initdb" -D "$SEC_PGDATA" -U secretaria --auth-local=trust --auth-host=reject -E UTF8 --no-locale >/dev/null
  cat >>"$SEC_PGDATA/postgresql.conf" <<CONF

# --- estiven4tech-mobile-server: secretária de teste ---
listen_addresses = ''                       # sem TCP: só socket Unix privado
unix_socket_directories = '$SEC_SOCKDIR'
unix_socket_permissions = 0700
port = 5433
shared_buffers = 32MB
max_connections = 30
dynamic_shared_memory_type = mmap           # evita /dev/shm (pode faltar no PRoot)
timezone = 'UTC'
log_timezone = 'UTC'
CONF
}

# Executa "$@" com o Postgres no ar: usa o do supervisor ou sobe um temporário.
with_pg() {
  local bin started=0; bin="$(sec_pg_bin)"
  mkdir -p "$SEC_SOCKDIR" && chmod 700 "$SEC_SOCKDIR"
  if ! sec_pg_ready; then
    "$bin/pg_ctl" -D "$SEC_PGDATA" -l "$PROJECT_DIR/logs/sec-postgres-temp.log" -w start >/dev/null
    started=1
  fi
  local rc=0
  "$@" || rc=$?
  [ "$started" = 1 ] && "$bin/pg_ctl" -D "$SEC_PGDATA" -m fast -w stop >/dev/null
  return "$rc"
}

ensure_dbs() {
  local bin; bin="$(sec_pg_bin)"
  for db in secretaria secretaria_test; do
    "$bin/psql" -h "$SEC_SOCKDIR" -p 5433 -U secretaria -d postgres -tAc "select 1 from pg_database where datname='$db'" | grep -q 1 \
      || "$bin/createdb" -h "$SEC_SOCKDIR" -p 5433 -U secretaria "$db"
  done
}

run_migrate() { (cd "$SECRETARIA_APP_DIR" && npm run --silent migrate); }
setup_db() { ensure_dbs && run_migrate; }
run_tests() { (cd "$SECRETARIA_APP_DIR" && npm run --silent migrate >/dev/null && npm run --silent typecheck && npx vitest run); }

cmd_instalar() {
  need_non_root
  local missing=""
  ls /usr/lib/postgresql/*/bin/postgres >/dev/null 2>&1 || missing="$missing postgresql"
  for p in curl xz-utils; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
  done
  [ -z "$missing" ] || die "instale antes: sudo apt install -y$missing"
  ensure_dirs
  install_node
  write_env
  sec_load_env
  [ -f "$SECRETARIA_APP_DIR/package.json" ] || die "código não encontrado em $SECRETARIA_APP_DIR
  clone com: git clone https://github.com/estivendjgutti-dot/secretaria-barber.git ~/secretaria-barber"
  case "$SECRETARIA_APP_DIR" in /sdcard/*|/storage/*) die "não rode o app de armazenamento compartilhado";; esac
  info "instalando dependências do app (npm ci)"
  (cd "$SECRETARIA_APP_DIR" && npm ci --no-audit --no-fund)
  init_cluster
  with_pg setup_db
  info "instalado. Próximo passo: scripts/secretaria.sh ativar"
}

cmd_ativar() {
  need_non_root
  [ -f "$SEC_PGDATA/PG_VERSION" ] || die "rode scripts/secretaria.sh instalar antes"
  local L="$PROJECT_DIR/logs"
  mkdir -p "$(dirname "$SEC_CONF")"
  cat >"$SEC_CONF" <<CONF
; Gerado por scripts/secretaria.sh ativar — secretária de TESTE (envio real desligado)
[program:sec-postgres]
command=$PROJECT_DIR/secretaria/run-postgres.sh
priority=10
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=5
startretries=3
stopsignal=INT
stopwaitsecs=60
redirect_stderr=true
stdout_logfile=$L/sec-postgres.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3

[program:sec-app]
command=$PROJECT_DIR/secretaria/run-node.sh app
priority=20
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=10
startretries=5
stopsignal=TERM
stopwaitsecs=30
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=$L/sec-app.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3

[program:sec-worker]
command=$PROJECT_DIR/secretaria/run-node.sh worker
priority=30
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=10
startretries=5
stopsignal=TERM
stopwaitsecs=30
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=$L/sec-worker.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
CONF
  if sup_alive; then
    ctl reread >/dev/null && ctl update
    for _ in $(seq 1 40); do
      [ "$(ctl status sec-app 2>/dev/null | awk '{print $2}')" = RUNNING ] && break; sleep 1
    done
    ctl status sec-postgres sec-app sec-worker || true
  else
    info "ativado; sobe junto com scripts/start.sh"
  fi
}

cmd_desativar() {
  if sup_alive && [ -f "$SEC_CONF" ]; then ctl stop sec-worker sec-app sec-postgres || true; fi
  rm -f "$SEC_CONF"
  if sup_alive; then ctl reread >/dev/null && ctl update; fi
  info "secretária desativada (dados preservados em data/secretaria-pg)"
}

port_3100_bindings() {
  # Endereços locais escutando na porta 3100 (0x0C1C), lidos de /proc/net/tcp e tcp6.
  local f
  for f in /proc/net/tcp /proc/net/tcp6; do
    [ -r "$f" ] && awk 'NR>1 && $4=="0A" {split($2,a,":"); if (a[2]=="0C1C") print a[1]}' "$f"
  done | sort -u
  return 0
}

cmd_status() {
  local rc=0
  if sup_alive && [ -f "$SEC_CONF" ]; then
    ctl status sec-postgres sec-app sec-worker || rc=1
  else
    info "secretária: não ativa no supervisor"; return 2
  fi
  if sec_pg_ready; then info "postgres: aceitando conexões (socket privado)"; else info "PROBLEMA: postgres não responde"; rc=1; fi
  local hz; hz="$(curl -fsS -m 5 http://127.0.0.1:3100/healthz 2>/dev/null || true)"
  if [ -n "$hz" ]; then info "app /healthz: $hz"; else info "PROBLEMA: app não responde em 127.0.0.1:3100"; rc=1; fi
  local b; b="$(port_3100_bindings | tr '\n' ' ' | sed 's/ *$//')"
  case "$b" in
    0100007F) info "porta 3100: só loopback (127.0.0.1) — OK" ;;
    "") ;;
    *) info "PROBLEMA: porta 3100 escutando em: $b (esperado só 0100007F = 127.0.0.1)"; rc=1 ;;
  esac
  sec_load_env
  info "envio WhatsApp: ${WHATSAPP_SEND_MODE:-off} | IA: $([ -n "${OPENAI_API_KEY:-}" ] && echo 'OpenAI (com custo)' || echo 'resposta fixa de teste') | WAHA: $([ -n "${WAHA_URL:-}" ] && echo ligado || echo desligado)"
  return "$rc"
}

test_env() {
  sec_load_env
  export DATABASE_URL="postgres://secretaria@localhost:5433/secretaria_test?host=$SEC_SOCKDIR"
  # Os testes usam integrações fictícias: nada de chave real neles.
  export OPENAI_API_KEY="" WAHA_URL="" RESEND_API_KEY="" WHATSAPP_SEND_MODE=off
}

cmd_testar() {
  need_non_root
  test_env
  local out="$PROJECT_DIR/logs/sec-testes-ultima.txt"
  info "typecheck + testes do app (banco secretaria_test). Pode levar 1-3 min."
  set +e
  with_pg run_tests >"$out" 2>&1
  local rc=$?
  set -e
  grep -E "Test Files|Tests  |×" "$out" | tail -n 12 || true
  info "saída completa: logs/sec-testes-ultima.txt"
  return "$rc"
}

cmd_simular() {
  sec_load_env
  sec_pg_ready || die "postgres parado: scripts/secretaria.sh ativar"
  curl -fsS -m 5 http://127.0.0.1:3100/healthz >/dev/null || die "app não responde em 127.0.0.1:3100"
  local out="$PROJECT_DIR/logs/sec-simulacao-ultima.txt"
  set +e
  (cd "$SECRETARIA_APP_DIR" && timeout 150 npm run --silent simular) >"$out" 2>&1
  local rc=$?
  set -e
  [ "${1:-}" = "--quieto" ] || cat "$out"
  [ "$rc" -eq 0 ] || { warn "simulador saiu com código $rc (veja logs/sec-simulacao-ultima.txt)"; return 1; }
  grep -q "handshake: HTTP 200" "$out" || { warn "handshake não retornou 200"; return 1; }
  grep -q "assinatura falsa: HTTP 401" "$out" || { warn "assinatura falsa não foi recusada com 401"; return 1; }
  grep -q '"webhookEvents"' "$out" || { warn "/admin/status não respondeu"; return 1; }
  info "simulação OK"
}

cmd_migrar() { need_non_root; sec_load_env; with_pg run_migrate; }

cmd_backup() {
  sec_load_env
  sec_pg_ready || die "postgres parado: scripts/secretaria.sh ativar"
  local bin dir f keep="${SECRETARIA_BACKUP_KEEP:-7}"
  bin="$(sec_pg_bin)"; dir="$PROJECT_DIR/backups/secretaria"
  mkdir -p "$dir" && chmod 700 "$dir"
  f="$dir/secretaria-$(date -u +%Y%m%dT%H%M%SZ).dump"
  (umask 077; "$bin/pg_dump" -h "$SEC_SOCKDIR" -p 5433 -U secretaria -Fc -f "$f.partial" secretaria)
  "$bin/pg_restore" --list "$f.partial" >/dev/null || { rm -f "$f.partial"; die "dump ilegível; descartado"; }
  mv "$f.partial" "$f"
  ls -1t "$dir"/secretaria-*.dump | tail -n +"$((keep + 1))" | xargs -r rm -f
  info "backup: ${f#$PROJECT_DIR/} ($(du -h "$f" | cut -f1)); mantidos os $keep mais recentes"
  info "restaurar (teste): createdb -h $SEC_SOCKDIR -p 5433 -U secretaria restaurado && pg_restore -h $SEC_SOCKDIR -p 5433 -U secretaria -d restaurado $f"
}

cmd_operador() {
  [ $# -ge 1 ] || die 'uso: scripts/secretaria.sh operador email@exemplo.com "Nome"'
  sec_load_env
  [ -z "${RESEND_API_KEY:-}" ] || warn "RESEND_API_KEY preenchida: o convite será enviado por e-mail"
  (cd "$SECRETARIA_APP_DIR" && npm run --silent criar-operador -- "$@")
}

cmd_psql() { sec_load_env; exec "$(sec_pg_bin)/psql" -h "$SEC_SOCKDIR" -p 5433 -U secretaria -d "${1:-secretaria}"; }

case "${1:-}" in
  instalar) cmd_instalar ;;
  ativar) cmd_ativar ;;
  desativar) cmd_desativar ;;
  status) cmd_status ;;
  testar) cmd_testar ;;
  simular) shift; cmd_simular "$@" ;;
  migrar) cmd_migrar ;;
  psql) shift; cmd_psql "$@" ;;
  backup) cmd_backup ;;
  operador) shift; cmd_operador "$@" ;;
  *) die "uso: scripts/secretaria.sh instalar | ativar | desativar | status | testar | simular [--quieto] | migrar | backup | operador EMAIL [NOME] | psql [banco]" ;;
esac
