#!/usr/bin/env bash
# Restauração explícita.
#   Teste (não toca produção):  scripts/restore.sh BACKUP.tar.gz --target /tmp/teste-restore
#   Produção (servidor parado): scripts/stop.sh && scripts/restore.sh BACKUP.tar.gz --production [--with-config]
# O banco atual é preservado em data/pre-restore-<data>/ antes da troca.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
[ -x "$PY" ] || die "venv ausente: rode scripts/install.sh"
[ $# -ge 2 ] || die "uso: restore.sh BACKUP.tar.gz --target DIR | --production [--with-config]"
archive="$1"; shift
[ -f "$archive" ] || die "arquivo não encontrado: $archive"

case " $* " in
  *" --production "*)
    if sup_alive || [ -n "$(sup_pid_from_file)" ] || [ -n "$(locked_worker_pid)" ]; then
      die "servidor em execução: rode scripts/stop.sh antes de restaurar"
    fi
    "$PY" "$PROJECT_DIR/src/backup.py" verify "$archive" >/dev/null || die "backup reprovado na verificação"
    printf 'Isto substitui data/app.db pelo backup %s.\nDigite RESTAURAR para continuar: ' "$(basename "$archive")"
    read -r answer
    [ "$answer" = RESTAURAR ] || die "cancelado"
    exec "$PY" "$PROJECT_DIR/src/backup.py" restore "$archive" "$@" --yes
    ;;
  *) exec "$PY" "$PROJECT_DIR/src/backup.py" restore "$archive" "$@" ;;
esac
