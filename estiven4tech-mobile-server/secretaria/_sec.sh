# Variáveis e funções da secretária de TESTE. Requer scripts/_common.sh carregado antes.
SECRETARIA_ENV="$PROJECT_DIR/.env.secretaria"
SEC_PGDATA="$PROJECT_DIR/data/secretaria-pg"
SEC_SOCKDIR="$RUN_DIR/pg"
SEC_CONF="$PROJECT_DIR/config/supervisord.d/secretaria.conf"
SEC_NODE_VERSION="22.22.0"
SEC_NODE_BASE="$HOME/.local/opt"
# SHA-256 oficiais (nodejs.org/dist/v22.22.0/SHASUMS256.txt), conferidos em 17/10/2026.
SEC_NODE_SHA_arm64="1bf1eb9ee63ffc4e5d324c0b9b62cf4a289f44332dfef9607cea1a0d9596ba6f"
SEC_NODE_SHA_x64="9aa8e9d2298ab68c600bd6fb86a6c13bce11a4eca1ba9b39d79fa021755d7c37"

sec_arch() {
  case "$(uname -m)" in
    aarch64|arm64) echo arm64 ;;
    x86_64|amd64) echo x64 ;;
    *) die "arquitetura $(uname -m) sem Node 22 oficial suportado aqui" ;;
  esac
}
SEC_NODE_DIR="$SEC_NODE_BASE/node-v$SEC_NODE_VERSION-linux-$(sec_arch)"

sec_pg_bin() {
  local d
  d="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -n1)"
  [ -n "$d" ] || die "PostgreSQL não instalado (sudo apt install -y postgresql)"
  echo "$d"
}

sec_load_env() {
  [ -f "$SECRETARIA_ENV" ] || die "faltando $SECRETARIA_ENV: rode scripts/secretaria.sh instalar"
  set -a
  # shellcheck disable=SC1090
  . "$SECRETARIA_ENV"
  set +a
  SECRETARIA_APP_DIR="${SECRETARIA_APP_DIR:-$HOME/secretaria-barber/poc-whatsapp}"
  export PATH="$SEC_NODE_DIR/bin:$PATH"
}

sec_pg_ready() { "$(sec_pg_bin)/pg_isready" -q -h "$SEC_SOCKDIR" -p 5433 -U secretaria >/dev/null 2>&1; }
