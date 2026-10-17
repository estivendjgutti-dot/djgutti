#!/usr/bin/env bash
# OPCIONAL: chama start.sh (idempotente) ao abrir uma sessão do UserLAnd.
# Não é ativado pela instalação. Faz backup do ~/.bashrc e usa marcadores para remover depois.
# Uso: scripts/retomada-bashrc.sh ativar | desativar | status
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
RC="$HOME/.bashrc"
BEGIN="# >>> estiven4tech-mobile-server retomada >>>"
END="# <<< estiven4tech-mobile-server retomada <<<"

case "${1:-status}" in
  ativar)
    touch "$RC"
    if grep -qF "$BEGIN" "$RC"; then info "já ativado em $RC"; exit 0; fi
    cp -p "$RC" "$RC.bak-e4t-$(date +%Y%m%d%H%M%S)"
    cat >>"$RC" <<BLOCK
$BEGIN
# Só em shell interativo e fora do tmux; start.sh não duplica instâncias.
if [ -n "\${PS1:-}" ] && [ -z "\${TMUX:-}" ] && [ -x "$PROJECT_DIR/scripts/start.sh" ]; then
  "$PROJECT_DIR/scripts/start.sh" >/dev/null 2>&1 && echo "estiven4tech: servidor ativo (scripts/status.sh)" \\
    || echo "estiven4tech: start.sh falhou; rode $PROJECT_DIR/scripts/status.sh"
fi
$END
BLOCK
    info "ativado (backup do original salvo ao lado de $RC)"
    ;;
  desativar)
    [ -f "$RC" ] && grep -qF "$BEGIN" "$RC" || { info "não estava ativado"; exit 0; }
    cp -p "$RC" "$RC.bak-e4t-$(date +%Y%m%d%H%M%S)"
    awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1;next} $0==e{skip=0;next} !skip' "$RC" >"$RC.tmp-e4t" && mv "$RC.tmp-e4t" "$RC"
    info "desativado"
    ;;
  status)
    if [ -f "$RC" ] && grep -qF "$BEGIN" "$RC"; then info "ativado em $RC"; else info "desativado"; fi
    ;;
  *) die "uso: retomada-bashrc.sh ativar | desativar | status" ;;
esac
