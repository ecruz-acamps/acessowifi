#!/usr/bin/env bash
# Instalador do painel Acesso Wi-Fi (Debian/Ubuntu com systemd).
#
#   sudo ./deploy/install.sh
#
# Pergunta tudo o que é necessário (deploy/configure.py), configura o FortiGate (opcional,
# por SSH, mostrando as mudanças antes), gera /opt/acessowifi/.env, instala nginx com HTTPS,
# ativa os serviços e testa tudo. Rodar de novo atualiza o programa e pergunta se quer
# refazer a configuração.
set -euo pipefail

APP_DIR=/opt/acessowifi
SVC_USER=acessowifi
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HAS_SYSTEMD=$([ -d /run/systemd/system ] && echo 1 || echo 0)

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
yes_no() {  # yes_no "pergunta" S|N  -> retorna 0 para sim
  local def=$2 ans
  read -r -p "   $1 [$([ "$def" = S ] && echo S/n || echo s/N)]: " ans
  ans=${ans:-$def}
  [[ $ans =~ ^[sSyY] ]]
}

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
RECONFIGURE=1
if [ -f "$APP_DIR/.env" ]; then
  say "Já existe uma configuração em $APP_DIR/.env"
  yes_no "Refazer a configuração (FortiGate, login, acesso)? Responda não para só atualizar o programa" N || RECONFIGURE=0
fi
if [ "$RECONFIGURE" = 1 ]; then
  # Grava num arquivo temporário e só substitui o .env se o assistente terminar
  TMP_ENV="$APP_DIR/.env.new"
  DEMO_ARGS=(); [ "${ACESSOWIFI_DEMO:-}" = 1 ] && DEMO_ARGS=(--demo)   # só para testes
  if ! (cd "$APP_DIR" && "$PY" deploy/configure.py --env "$TMP_ENV" "${DEMO_ARGS[@]}"); then
    rm -f "$TMP_ENV"
    echo; echo "Configuração não concluída. Nada foi alterado no servidor; rode o instalador de novo."
    exit 1
  fi
  mv "$TMP_ENV" "$APP_DIR/.env"
  chown root:"$SVC_USER" "$APP_DIR/.env"
  chmod 640 "$APP_DIR/.env"
fi
grep -q "^BIND=.*127.0.0.1" "$APP_DIR/.env" && USE_NGINX=1 || USE_NGINX=0

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
say "Teste completo com o FortiGate (cria e apaga um usuário e um endereço de teste)"
if (cd "$APP_DIR" && runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/flask" --app app check-fortigate --escrita); then
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

# ---------------------------------------------------------------- firewall do servidor
PANEL_PORT=$([ "$USE_NGINX" = 1 ] && echo 443 || sed -n "s/^BIND='\{0,1\}[^:]*:\([0-9]*\).*/\1/p" "$APP_DIR/.env")
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  say "Firewall do servidor (ufw)"
  ufw allow "$PANEL_PORT/tcp" >/dev/null && info "Porta $PANEL_PORT liberada no ufw."
fi

# ---------------------------------------------------------------- resumo
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
say "Pronto!"
if [ "$USE_NGINX" = 1 ]; then
  info "Painel:  https://${IP:-IP_DO_SERVIDOR}/   (o navegador vai avisar do certificado autoassinado)"
else
  info "Painel:  http://${IP:-IP_DO_SERVIDOR}:$PANEL_PORT/"
fi
if [ "$CHECK_OK" != 1 ]; then
  info "ATENÇÃO: o teste com o FortiGate falhou (veja acima). Veja docs/FORTIGATE.md, parte"
  info "\"Problemas comuns\", e depois rode: sudo ./deploy/install.sh"
fi
info ""
info "Comandos úteis:"
info "  Testar o FortiGate:  cd $APP_DIR && sudo -u $SVC_USER .venv/bin/flask --app app check-fortigate"
info "  Ver o log:           journalctl -u acessowifi -f"
info "  Mudar configuração:  sudo ./deploy/install.sh  (responda sim para refazer)"
info "  Atualizar:           git pull && sudo ./deploy/install.sh"
