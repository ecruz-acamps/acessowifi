#!/usr/bin/env bash
# Atualização automática do painel a partir do GitHub.
# Instalado em /usr/local/sbin/acessowifi-update e executado pelo acessowifi-update.timer.
#
#   acessowifi-update            atualiza se houver commit novo no GitHub
#   acessowifi-update --forcar   reinstala a versão atual mesmo sem novidade
#
# Se a versão nova não carregar ou não responder, volta para a anterior e não tenta
# de novo aquele commit até sair outro.
set -uo pipefail
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

APP_DIR=${ACESSOWIFI_APP_DIR:-/opt/acessowifi}
STATE=${ACESSOWIFI_STATE:-/var/lib/acessowifi}
REPO=$STATE/repo
SVC_USER=acessowifi
RESTART_CMD=${ACESSOWIFI_RESTART:-"systemctl restart acessowifi"}

log() { echo "$*"; }
short() { git -C "$REPO" rev-parse --short "$1"; }

[ "$(id -u)" -eq 0 ] || { echo "Rode como root."; exit 1; }
[ -d "$REPO/.git" ] || { log "Cópia do repositório não encontrada em $REPO. Rode o instalador."; exit 1; }
exec 9>"$STATE/update.lock"
flock -n 9 || { log "Outra atualização já está rodando."; exit 0; }

BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
if ! git -C "$REPO" fetch -q origin "$BRANCH" 2>"$STATE/fetch.err"; then
  log "Não consegui consultar o GitHub agora (tento de novo no próximo ciclo): $(tail -1 "$STATE/fetch.err")"
  exit 0
fi
CURRENT=$(git -C "$REPO" rev-parse HEAD)
TARGET=$(git -C "$REPO" rev-parse "origin/$BRANCH")

if [ "${1:-}" != "--forcar" ]; then
  [ "$CURRENT" = "$TARGET" ] && exit 0
  if [ "$(cat "$STATE/failed-rev" 2>/dev/null)" = "$TARGET" ]; then
    exit 0   # já falhou antes; espera o próximo commit
  fi
fi

# ------------------------------------------------------------------ instalação de uma versão
deploy() {
  local rev=$1
  git -C "$REPO" reset -q --hard "$rev" || return 1

  # Código: o .env (configuração) não está no repositório e não é tocado
  git -C "$REPO" archive "$rev" | tar -x -C "$APP_DIR" || return 1
  find "$APP_DIR" -path "$APP_DIR/.env" -prune -o -path "$APP_DIR/.venv" -prune -o -exec chown root:root {} +
  chown root:"$SVC_USER" "$APP_DIR/.env" && chmod 640 "$APP_DIR/.env"

  # Bibliotecas, só se requirements.txt mudou
  local req_sum
  req_sum=$(sha256sum "$APP_DIR/requirements.txt" | cut -d' ' -f1)
  if [ "$req_sum" != "$(cat "$STATE/requirements.sha" 2>/dev/null)" ]; then
    log "Instalando bibliotecas novas"
    "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" || return 1
    echo "$req_sum" > "$STATE/requirements.sha"
  fi

  # Comandos e serviços do systemd (cópia + rename: nunca sobrescreve um script em execução)
  replace() { cp "$1" "$2.new" && chmod "$3" "$2.new" && mv -f "$2.new" "$2"; }
  replace "$APP_DIR/deploy/acessowifi-cli.sh" /usr/local/bin/acessowifi 755
  replace "$APP_DIR/deploy/acessowifi-update.sh" /usr/local/sbin/acessowifi-update 755
  if [ -d /run/systemd/system ]; then
    local changed=0 unit
    for unit in "$APP_DIR"/deploy/*.service "$APP_DIR"/deploy/*.timer; do
      cmp -s "$unit" "/etc/systemd/system/$(basename "$unit")" && continue
      cp "$unit" /etc/systemd/system/ && changed=1
    done
    [ "$changed" = 1 ] && systemctl daemon-reload
  fi

  # Antes de reiniciar: o painel carrega? (erro de sintaxe, import quebrado, variável faltando)
  if ! (cd "$APP_DIR" && runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/python" -c "import app" >"$STATE/selftest.log" 2>&1); then
    log "A versão $(short "$rev") não carrega:"
    tail -5 "$STATE/selftest.log" | sed 's/^/  /'
    return 1
  fi

  $RESTART_CMD 9>&- || return 1   # 9>&-: o serviço reiniciado não herda a trava
  local bind port _
  bind=$(sed -n "s/^BIND='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$APP_DIR/.env")
  port=${bind##*:}
  for _ in $(seq 1 20); do
    "$APP_DIR/.venv/bin/python" -c 'import sys, urllib.request as u
u.build_opener(u.ProxyHandler({})).open(sys.argv[1], timeout=2)' "http://127.0.0.1:$port/login" 2>/dev/null && return 0
    sleep 1
  done
  log "A versão $(short "$rev") não respondeu depois de reiniciar."
  return 1
}

log "Atualizando $(short "$CURRENT") -> $(short "$TARGET"):"
git -C "$REPO" log --format='  %h %s' "$CURRENT..$TARGET" 2>/dev/null | head -20

if deploy "$TARGET"; then
  rm -f "$STATE/failed-rev"
  log "Atualizado para $(short "$TARGET")."
else
  echo "$TARGET" > "$STATE/failed-rev"
  log "Falhou. Voltando para $(short "$CURRENT")..."
  if deploy "$CURRENT"; then
    log "Versão anterior restaurada. O commit $(short "$TARGET") não será tentado de novo; corrija e envie outro."
  else
    log "ATENÇÃO: não consegui restaurar a versão anterior. Rode: acessowifi diagnostico"
  fi
  exit 1
fi
