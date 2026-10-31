#!/usr/bin/env bash
# Instalador do painel Acesso Wi-Fi (Debian/Ubuntu com systemd).
#
#   sudo ./deploy/install.sh
#
# Na primeira vez, pergunta os dados do FortiGate e do login e cria /opt/acessowifi/.env.
# Nas próximas (atualização), mantém o .env e só atualiza o código e reinicia o serviço.
# Para refazer a configuração: sudo rm /opt/acessowifi/.env && sudo ./deploy/install.sh
set -euo pipefail

APP_DIR=/opt/acessowifi
SVC_USER=acessowifi
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HAS_SYSTEMD=$([ -d /run/systemd/system ] && echo 1 || echo 0)

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
ask() {  # ask VAR "pergunta" "padrão"
  local __var=$1 prompt=$2 def=${3-} ans
  read -r -p "   $prompt${def:+ [$def]}: " ans
  printf -v "$__var" '%s' "${ans:-$def}"
}
ask_secret() {
  local __var=$1 prompt=$2 ans=""
  while [ -z "$ans" ]; do read -r -s -p "   $prompt: " ans; echo; done
  printf -v "$__var" '%s' "$ans"
}
yes_no() {  # yes_no "pergunta" S|N  -> retorna 0 para sim
  local def=$2 ans
  read -r -p "   $1 [$([ "$def" = S ] && echo S/n || echo s/N)]: " ans
  ans=${ans:-$def}
  [[ $ans =~ ^[sSyY] ]]
}
q() { printf "'%s'" "$1"; }  # valor entre aspas simples para o .env

[ "$(id -u)" -eq 0 ] || { echo "Rode como root: sudo $0"; exit 1; }

# ---------------------------------------------------------------- dependências
say "Verificando dependências"
command -v python3 >/dev/null || { command -v apt-get >/dev/null && apt-get install -y python3; }
if ! python3 -c "import ensurepip, venv" 2>/dev/null; then
  if command -v apt-get >/dev/null; then
    apt-get update -qq && apt-get install -y -qq python3-venv
  else
    echo "Instale o pacote de venv do Python (python3-venv) e rode de novo."; exit 1
  fi
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Precisa de Python 3.9 ou mais novo."; exit 1; }
info "$(python3 --version)"

# ---------------------------------------------------------------- arquivos
say "Copiando o painel para $APP_DIR"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell "$(command -v nologin || echo /bin/false)" "$SVC_USER"
mkdir -p "$APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  tar -C "$SRC_DIR" --exclude=.git --exclude=.venv --exclude=.env --exclude=__pycache__ -cf - . | tar -C "$APP_DIR" -xf -
fi
chown -R root:root "$APP_DIR"   # o serviço só lê o código; não precisa escrever nele

say "Instalando bibliotecas Python"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
PY="$APP_DIR/.venv/bin/python"

# ---------------------------------------------------------------- configuração
USE_NGINX=0
if [ -f "$APP_DIR/.env" ]; then
  say "Configuração existente mantida ($APP_DIR/.env)"
  grep -q "^BIND=.*127.0.0.1" "$APP_DIR/.env" && USE_NGINX=1
else
  say "1/3  Conexão com o FortiGate (API REST)"
  info "A conexão é por TOKEN, não por usuário e senha. Gere o token no FortiGate com o"
  info "arquivo deploy/fortigate-setup.conf (comando: execute api-user generate-key acessowifi)."
  ask FGT_HOST "IP do FortiGate (com :porta se o HTTPS de administração não for 443)" "192.168.1.99"
  FGT_HOST=${FGT_HOST#https://}; FGT_HOST=${FGT_HOST#http://}; FGT_HOST=${FGT_HOST%%/*}
  ask_secret FGT_TOKEN "Token da API (não aparece enquanto digita)"
  ask FGT_VDOM "VDOM" "root"
  ask FGT_GUEST_GROUPS "Grupo guest onde os usuários serão cadastrados" "WiFi-Cadastrados"
  ask FGT_MAC_GROUP "Grupo de endereços do 'Permitir sempre'" "WiFi-MAC-Liberados"
  TZ_DEFAULT=$(timedatectl show -p Timezone --value 2>/dev/null || true)
  ask TZ_VALUE "Fuso horário (o mesmo do FortiGate)" "${TZ_DEFAULT:-America/Sao_Paulo}"

  say "2/3  Login do painel"
  ask PANEL_USER "Usuário para entrar no painel" "admin"
  while :; do
    ask_secret PANEL_PASS "Senha do painel (mínimo 8 caracteres)"
    ask_secret PANEL_PASS2 "Repita a senha"
    [ "$PANEL_PASS" = "$PANEL_PASS2" ] || { info "As senhas não conferem."; continue; }
    [ ${#PANEL_PASS} -ge 8 ] || { info "Use pelo menos 8 caracteres."; continue; }
    break
  done

  say "3/3  Acesso ao painel"
  if yes_no "Instalar nginx com HTTPS (certificado autoassinado)? Recomendado" S; then
    USE_NGINX=1; BIND=127.0.0.1:8080; COOKIE_SECURE=true
  else
    ask PORT "Porta HTTP do painel (sem HTTPS, use só na rede interna)" "8080"
    BIND="0.0.0.0:$PORT"; COOKIE_SECURE=false
  fi

  HASH=$(printf '%s' "$PANEL_PASS" | "$PY" -c 'import sys; from werkzeug.security import generate_password_hash as g; print(g(sys.stdin.read()))')
  SECRET=$("$PY" -c 'import secrets; print(secrets.token_hex(32))')
  unset PANEL_PASS PANEL_PASS2

  (
    umask 027
    cat > "$APP_DIR/.env" <<EOF
# Gerado por deploy/install.sh em $(date '+%Y-%m-%d %H:%M'). Depois de editar: systemctl restart acessowifi

# --- FortiGate (conexão pela API REST, com token) ---
FGT_HOST=$(q "$FGT_HOST")
FGT_TOKEN=$(q "$FGT_TOKEN")
FGT_VDOM=$(q "$FGT_VDOM")
FGT_VERIFY_SSL=false
FGT_GUEST_GROUPS=$(q "$FGT_GUEST_GROUPS")
FGT_MAC_GROUP=$(q "$FGT_MAC_GROUP")
MAX_VALIDITY_DAYS=365
TZ=$(q "$TZ_VALUE")

# --- Login do painel ---
APP_USER=$(q "$PANEL_USER")
APP_PASSWORD_HASH=$(q "$HASH")
SECRET_KEY=$(q "$SECRET")
COOKIE_SECURE=$COOKIE_SECURE

# --- Servidor ---
BIND=$BIND
EOF
  )
  chown root:"$SVC_USER" "$APP_DIR/.env"
  chmod 640 "$APP_DIR/.env"
  unset FGT_TOKEN HASH SECRET
  info "Configuração salva em $APP_DIR/.env (legível só por root e pelo serviço)."
fi

# ---------------------------------------------------------------- nginx + HTTPS
if [ "$USE_NGINX" = 1 ]; then
  say "Configurando nginx com HTTPS"
  if ! command -v nginx >/dev/null; then
    if command -v apt-get >/dev/null; then apt-get install -y -qq nginx openssl
    else echo "Instale o nginx e rode de novo (ou configure manualmente com deploy/nginx.conf)."; exit 1; fi
  fi
  mkdir -p /etc/ssl/acessowifi
  if [ ! -f /etc/ssl/acessowifi/cert.pem ]; then
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=$(hostname)" \
      -addext "subjectAltName=DNS:$(hostname)${SERVER_IP:+,IP:$SERVER_IP}" \
      -keyout /etc/ssl/acessowifi/key.pem -out /etc/ssl/acessowifi/cert.pem 2>/dev/null
    chmod 600 /etc/ssl/acessowifi/key.pem
    info "Certificado autoassinado criado (válido por 10 anos)."
  fi
  if [ -d /etc/nginx/sites-available ]; then
    NGINX_CONF=/etc/nginx/sites-available/acessowifi
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/acessowifi
  else
    NGINX_CONF=/etc/nginx/conf.d/acessowifi.conf
  fi
  [ -f "$NGINX_CONF" ] || sed 's/server_name .*;/server_name _;/' "$APP_DIR/deploy/nginx.conf" > "$NGINX_CONF"
  nginx -t -q
  [ "$HAS_SYSTEMD" = 1 ] && systemctl enable -q nginx && systemctl reload-or-restart nginx
fi

# ---------------------------------------------------------------- serviços
say "Teste de conexão com o FortiGate"
CHECK_ARGS=()
yes_no "Testar também a escrita (cria e apaga um usuário e um endereço de teste)?" S && CHECK_ARGS=(--escrita)
if (cd "$APP_DIR" && runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/flask" --app app check-fortigate "${CHECK_ARGS[@]}"); then
  CHECK_OK=1
else
  CHECK_OK=0
fi

if [ "$HAS_SYSTEMD" = 1 ]; then
  say "Ativando os serviços"
  cp "$APP_DIR/deploy/acessowifi.service" "$APP_DIR/deploy/acessowifi-cleanup.service" \
     "$APP_DIR/deploy/acessowifi-cleanup.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable -q acessowifi acessowifi-cleanup.timer
  systemctl restart acessowifi
  systemctl start acessowifi-cleanup.timer
  sleep 2
  systemctl is-active -q acessowifi && info "Serviço acessowifi rodando." \
    || { info "O serviço não subiu. Veja: journalctl -u acessowifi -n 50"; exit 1; }
else
  info "systemd não encontrado: inicie manualmente com"
  info "  cd $APP_DIR && sudo -u $SVC_USER .venv/bin/gunicorn -w 2 -b \$BIND app:app"
fi

# ---------------------------------------------------------------- resumo
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
BIND_NOW=$(sed -n 's/^BIND=//p' "$APP_DIR/.env")
say "Pronto!"
if [ "$USE_NGINX" = 1 ]; then
  info "Painel:  https://${IP:-IP_DO_SERVIDOR}/   (o navegador vai avisar do certificado autoassinado)"
else
  info "Painel:  http://${IP:-IP_DO_SERVIDOR}:${BIND_NOW##*:}/"
fi
[ "$CHECK_OK" = 1 ] || info "ATENÇÃO: o teste com o FortiGate falhou (veja acima). Corrija e rode de novo o teste."
info ""
info "Comandos úteis:"
info "  Testar o FortiGate:  cd $APP_DIR && sudo -u $SVC_USER .venv/bin/flask --app app check-fortigate"
info "  Ver o log:           journalctl -u acessowifi -f"
info "  Mudar configuração:  sudo nano $APP_DIR/.env && sudo systemctl restart acessowifi"
info "  Atualizar:           git pull && sudo ./deploy/install.sh"
