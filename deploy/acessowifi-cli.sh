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
  diagnostico)
    echo "== Carregando o painel como o usuário do serviço"
    if (cd "$APP_DIR" && runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/python" -c "import app" 2>&1 | tail -6); then :; fi
    if command -v systemd-run >/dev/null; then
      echo "== Carregando o painel com as mesmas restrições do serviço (systemd)"
      systemd-run --quiet --wait --pipe --collect \
        -p User="$SVC_USER" -p Group="$SVC_USER" -p WorkingDirectory="$APP_DIR" \
        -p EnvironmentFile="$APP_DIR/.env" -p NoNewPrivileges=true -p ProtectSystem=strict \
        -p ProtectHome=true -p PrivateTmp=true \
        "$APP_DIR/.venv/bin/python" -c "import app; print('OK: o painel carrega normalmente')" 2>&1 | tail -6
    fi
    echo "== Erros no log do serviço"
    journalctl -u acessowifi -n 300 --no-pager -o cat 2>/dev/null \
      | grep -E "Error|Exception|error:|denied|No such|not found" | grep -v HaltServer | sort -u | tail -8
    echo "== Portas"
    BIND=$(sed -n "s/^BIND='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$APP_DIR/.env")
    echo "porta interna configurada: ${BIND:-?}"
    ss -ltnp 2>/dev/null | grep -E ":(${BIND##*:}|80|443|8443) " || echo "(nada escutando nessas portas)"
    ;;
  senha)
    # Troca o usuário/senha de login do painel (não mexe no FortiGate)
    CURRENT=$(sed -n "s/^APP_USER='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$APP_DIR/.env")
    echo "Usuário atual do painel: ${CURRENT:-admin}"
    read -r -p "Novo usuário [${CURRENT:-admin}]: " NEW_USER
    NEW_USER=${NEW_USER:-${CURRENT:-admin}}
    [[ $NEW_USER =~ ^[A-Za-z0-9._@-]{3,64}$ ]] || { echo "Usuário inválido (3 a 64 letras, números ou . _ @ -)."; exit 1; }
    while :; do
      read -r -s -p "Nova senha (mínimo 8 caracteres): " P1; echo
      read -r -s -p "Repita a senha: " P2; echo
      [ "$P1" = "$P2" ] || { echo "As senhas não conferem."; continue; }
      [ ${#P1} -ge 8 ] || { echo "Use pelo menos 8 caracteres."; continue; }
      break
    done
    printf '%s' "$P1" | NEW_USER="$NEW_USER" ENV_FILE="$APP_DIR/.env" "$APP_DIR/.venv/bin/python" -c '
import os, sys
from werkzeug.security import generate_password_hash
values = {"APP_USER": os.environ["NEW_USER"], "APP_PASSWORD_HASH": generate_password_hash(sys.stdin.read())}
path = os.environ["ENV_FILE"]
lines = open(path).read().splitlines()
out = [l for l in lines if l.split("=", 1)[0] not in values] + [f"{k}=\x27{v}\x27" for k, v in values.items()]
open(path, "w").write("\n".join(out) + "\n")'
    unset P1 P2
    chown root:"$SVC_USER" "$APP_DIR/.env"; chmod 640 "$APP_DIR/.env"
    systemctl restart acessowifi 2>/dev/null || true
    echo "Pronto: entre no painel com o usuário '$NEW_USER' e a nova senha."
    ;;
  atualizar)  shift; /usr/local/sbin/acessowifi-update "${@:---instalar}" ;;
  verificar)  /usr/local/sbin/acessowifi-update --verificar ;;
  atualizacoes)
    "$APP_DIR/.venv/bin/python" - /var/lib/acessowifi-ctl/status.json <<'PY'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except OSError:
    sys.exit("Ainda não há verificação. Rode: acessowifi verificar")
c = s.get("current") or {}
print(f"Versão instalada: {c.get('hash', '?')} {c.get('subject', '')}")
print(f"Situação: {s.get('message', '')}")
for p in s.get("pending", []):
    print(f"  novo: {p['hash']} {p['subject']}")
for line in s.get("log", [])[-10:]:
    print(f"  | {line}")
PY
    ;;
  log)       journalctl -u acessowifi -f ;;
  reiniciar) systemctl restart acessowifi && echo "Painel reiniciado." ;;
  *)
    echo "Uso: acessowifi <comando>"
    echo "  status      situação do painel, do nginx e da limpeza automática"
    echo "  testar      testa a conexão com o FortiGate (testar --escrita: teste completo)"
    echo "  log         acompanha o log do painel"
    echo "  reiniciar   reinicia o painel"
    echo "  diagnostico mostra por que o painel não sobe"
    echo "  senha       troca o usuário e a senha de login do painel"
    echo "  verificar   consulta o GitHub e conta as atualizações (não instala)"
    echo "  atualizar   instala a versão nova do GitHub (--forcar: reinstala a atual)"
    echo "  atualizacoes  versão instalada e atualizações disponíveis"
    echo "  limpar      remove agora as liberações de MAC vencidas"
    ;;
esac
