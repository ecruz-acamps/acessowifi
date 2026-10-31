#!/usr/bin/env bash
# Comando de administração do painel Acesso Wi-Fi (instalado em /usr/local/bin/acessowifi).
# Funciona sem sudo: rode como root ("su -").
set -uo pipefail
APP_DIR=/opt/acessowifi
SVC_USER=acessowifi
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

[ "$(id -u)" -eq 0 ] || { echo "Rode como root (su - ou sudo): acessowifi ${1:-ajuda}"; exit 1; }

flask_cli() {
  cd "$APP_DIR" && FLASK_SKIP_DOTENV=1 runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/flask" --app app "$@"
}

case "${1:-ajuda}" in
  testar)    shift; flask_cli check-fortigate "$@" ;;
  limpar)    flask_cli cleanup-devices ;;
  status)
    for unit in acessowifi nginx acessowifi-cleanup.timer; do
      printf '%-26s %s\n' "$unit" "$(systemctl is-active "$unit" 2>/dev/null || true)"
    done
    BIND=$(sed -n "s/^BIND='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$APP_DIR/.env")
    printf '%-26s %s\n' "porta interna" "${BIND:-?}"
    ls -l "$APP_DIR/.env" | awk '{print "permissão do .env           " $1, $3 ":" $4}'
    systemctl status acessowifi --no-pager -n 5 2>/dev/null | tail -6
    ;;
  log)       journalctl -u acessowifi -f ;;
  reiniciar) systemctl restart acessowifi && echo "Painel reiniciado." ;;
  *)
    echo "Uso: acessowifi <comando>"
    echo "  status      situação do painel, do nginx e da limpeza automática"
    echo "  testar      testa a conexão com o FortiGate (testar --escrita: teste completo)"
    echo "  log         acompanha o log do painel"
    echo "  reiniciar   reinicia o painel"
    echo "  limpar      remove agora as liberações de MAC vencidas"
    ;;
esac
