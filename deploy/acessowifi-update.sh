#!/usr/bin/env bash
# Atualizações do painel a partir do GitHub. Instalado em /usr/local/sbin/acessowifi-update.
#
#   acessowifi-update --verificar   consulta o GitHub e conta os commits novos (não instala)
#   acessowifi-update [--instalar]  instala a versão nova (pedido pelo botão do painel ou pela CLI)
#   acessowifi-update --forcar      reinstala a versão atual
#
# O painel não tem privilégio para instalar: o botão grava um pedido em $CTL (check ou install)
# e as units acessowifi-update*.path chamam este script como root. O resultado vai para
# $CTL/status.json, que o painel lê para mostrar o número de atualizações e o andamento.
# Na instalação, se a versão nova não carregar ou não responder, volta para a anterior.
set -uo pipefail
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

APP_DIR=${ACESSOWIFI_APP_DIR:-/opt/acessowifi}
STATE=${ACESSOWIFI_STATE:-/var/lib/acessowifi}
CTL=${ACESSOWIFI_CTL:-/var/lib/acessowifi-ctl}
REPO=$STATE/repo
SVC_USER=acessowifi
RESTART_CMD=${ACESSOWIFI_RESTART:-"systemctl restart acessowifi"}
MODE=${1:---instalar}

log() { echo "$*"; LOG+=("$*"); }
short() { git -C "$REPO" rev-parse --short "$1"; }
LOG=()

[ "$(id -u)" -eq 0 ] || { echo "Rode como root."; exit 1; }
mkdir -p "$CTL" && chown root:"$SVC_USER" "$CTL" && chmod 770 "$CTL"
# O pedido é consumido já no início (senão a unit .path dispararia de novo)
case "$MODE" in --verificar) rm -f "$CTL/check" ;; *) rm -f "$CTL/install" ;; esac

# status.json: estado, versão instalada, commits pendentes e o log da última operação
write_status() {  # write_status estado mensagem
  STATUS_STATE=$1 STATUS_MSG=$2 STATUS_LOG="$(printf '%s\n' "${LOG[@]+"${LOG[@]}"}")" \
  CURRENT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null) \
  TARGET=$(git -C "$REPO" rev-parse "origin/$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)" 2>/dev/null) \
  REPO="$REPO" python3 - "$CTL/status.json" <<'PY'
import json, os, subprocess, sys, time
def git(*args):
    r = subprocess.run(["git", "-C", os.environ["REPO"], *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""
def commits(*rng):
    out = git("log", "--format=%h%x1f%s%x1f%cI", *rng)
    return [dict(zip(("hash", "subject", "date"), l.split("\x1f"))) for l in out.splitlines() if l]
def vtuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return ()
def versions(rev, newer_than):
    """Seções '## X.Y — data' do CHANGELOG.md de 'rev' mais novas que 'newer_than'."""
    out, cur = [], None
    for line in git("show", f"{rev}:CHANGELOG.md").splitlines():
        if line.startswith("## "):
            number, _, date = line[3:].partition("—")
            cur = {"version": number.strip(), "date": date.strip(), "changes": []}
            if vtuple(cur["version"]) > vtuple(newer_than):
                out.append(cur)
            else:
                cur = None
        elif cur is not None and line.startswith("- "):
            cur["changes"].append(line[2:].strip())
    return out
cur, tgt = os.environ.get("CURRENT", ""), os.environ.get("TARGET", "")
current = (commits("-1", cur) or [{}])[0] if cur else {}
current["version"] = git("show", f"{cur}:VERSION") if cur else ""
pending = commits(f"{cur}..{tgt}") if cur and tgt and cur != tgt else []
target_version = git("show", f"{tgt}:VERSION") if pending else current["version"]
status = {"state": os.environ["STATUS_STATE"], "message": os.environ["STATUS_MSG"],
          "log": [l for l in os.environ.get("STATUS_LOG", "").splitlines() if l],
          "current": current, "pending": pending, "target_version": target_version,
          "pending_versions": versions(tgt, current["version"] or "0") if pending else [],
          "checked_at": int(time.time())}
tmp = sys.argv[1] + ".tmp"
with open(tmp, "w") as f:
    json.dump(status, f, ensure_ascii=False)
os.chmod(tmp, 0o644)
os.replace(tmp, sys.argv[1])
PY
}

[ -d "$REPO/.git" ] || { log "Cópia do repositório não encontrada em $REPO. Rode o instalador."; write_status failed "${LOG[0]}"; exit 1; }
exec 9>"$STATE/update.lock"
flock -n 9 || { echo "Outra operação de atualização já está rodando."; exit 0; }

BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
[ "$MODE" = --verificar ] && write_status checking "Consultando o GitHub..." || write_status installing "Consultando o GitHub..."
if ! git -C "$REPO" fetch -q origin "$BRANCH" 2>"$STATE/fetch.err"; then
  log "Não consegui consultar o GitHub: $(tail -1 "$STATE/fetch.err")"
  write_status failed "Não consegui consultar o GitHub."
  exit 1
fi
CURRENT=$(git -C "$REPO" rev-parse HEAD)
TARGET=$(git -C "$REPO" rev-parse "origin/$BRANCH")

if [ "$MODE" = --verificar ]; then
  N=$(git -C "$REPO" rev-list --count "$CURRENT..$TARGET")
  V_NOW=$(git -C "$REPO" show "$CURRENT:VERSION" 2>/dev/null); V_NEW=$(git -C "$REPO" show "$TARGET:VERSION" 2>/dev/null)
  if [ "$N" = 0 ]; then MSG="O painel está na versão mais recente${V_NOW:+ ($V_NOW)}."
  else MSG="Versão ${V_NEW:-nova} disponível${V_NOW:+ (instalada: $V_NOW)}."; fi
  write_status idle "$MSG"
  [ -t 1 ] && echo "$MSG"
  exit 0
fi
if [ "$CURRENT" = "$TARGET" ] && [ "$MODE" != --forcar ]; then
  log "O painel já está na versão mais recente ($(short "$CURRENT"))."
  write_status ok "O painel já está na versão mais recente."
  exit 0
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

  # Comandos e units do systemd (cópia + rename: nunca sobrescreve um script em execução)
  replace() { cp "$1" "$2.new" && chmod "$3" "$2.new" && mv -f "$2.new" "$2"; }
  replace "$APP_DIR/deploy/acessowifi-cli.sh" /usr/local/bin/acessowifi 755
  replace "$APP_DIR/deploy/acessowifi-update.sh" /usr/local/sbin/acessowifi-update 755
  if [ -d /run/systemd/system ]; then
    local changed=0 unit
    for unit in "$APP_DIR"/deploy/*.service "$APP_DIR"/deploy/*.timer "$APP_DIR"/deploy/*.path; do
      [ -f "$unit" ] || continue
      cmp -s "$unit" "/etc/systemd/system/$(basename "$unit")" && continue
      cp "$unit" /etc/systemd/system/ && changed=1
    done
    [ "$changed" = 1 ] && systemctl daemon-reload
    systemctl enable -q --now acessowifi-update.timer acessowifi-update.path acessowifi-update-install.path 2>/dev/null || true
  fi

  # Certificado HTTPS (CA própria do servidor), se o painel usa nginx
  if [ -f /etc/nginx/sites-available/acessowifi ] || [ -f /etc/nginx/conf.d/acessowifi.conf ]; then
    while read -r line; do log "$line"; done < <(bash "$APP_DIR/deploy/acessowifi-cert.sh" 2>&1)
  fi

  # Antes de reiniciar: o painel carrega? (erro de sintaxe, import quebrado, variável faltando)
  if ! (cd "$APP_DIR" && runuser -u "$SVC_USER" -- "$APP_DIR/.venv/bin/python" -c "import app" >"$STATE/selftest.log" 2>&1); then
    log "A versão $(short "$rev") não carrega:"
    while read -r line; do log "  $line"; done < <(tail -5 "$STATE/selftest.log")
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
while read -r line; do log "  $line"; done < <(git -C "$REPO" log --format='%h %s' "$CURRENT..$TARGET" | head -20)
write_status installing "Instalando a atualização..."

if deploy "$TARGET"; then
  V_NEW=$(cat "$APP_DIR/VERSION" 2>/dev/null)
  log "Atualizado para ${V_NEW:+a versão $V_NEW }($(short "$TARGET"))."
  write_status ok "Versão ${V_NEW:-nova} instalada."
else
  log "Falhou. Voltando para $(short "$CURRENT")..."
  if deploy "$CURRENT"; then
    log "Versão anterior restaurada; o painel continua funcionando."
    write_status failed "A atualização falhou e a versão anterior foi restaurada."
  else
    log "ATENÇÃO: não consegui restaurar a versão anterior. Rode: acessowifi diagnostico"
    write_status failed "A atualização falhou e não consegui restaurar a versão anterior."
  fi
  exit 1
fi
