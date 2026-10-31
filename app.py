"""Painel de acesso Wi-Fi — usuários do captive portal e clientes conectados no FortiGate."""
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

from fortigate import FortiGate, FortiGateError

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    PERMANENT_SESSION_LIFETIME=8 * 3600,
)

APP_USER = os.environ["APP_USER"]
APP_PASSWORD_HASH = os.environ["APP_PASSWORD_HASH"]
# Grupos guest que o painel pode alterar. Vazio = todos os grupos do tipo guest.
GUEST_GROUPS = [g.strip() for g in os.getenv("FGT_GUEST_GROUPS", "").split(",") if g.strip()]
MAX_VALIDITY_DAYS = int(os.getenv("MAX_VALIDITY_DAYS", 365))
# Grupo de endereços usado como "Exempt sources" do captive portal
MAC_GROUP = os.getenv("FGT_MAC_GROUP", "WiFi-MAC-Liberados")
MAC_PREFIX = "acessowifi-"  # só endereços com este prefixo são gerenciados pelo painel

if os.getenv("DEMO_MODE") == "1":
    from mock_data import MockFortiGate
    fgt = MockFortiGate()
else:
    verify = os.getenv("FGT_VERIFY_SSL", "true")
    verify = {"true": True, "false": False}.get(verify.lower(), verify)  # ou caminho do CA
    fgt = FortiGate(
        host=os.environ["FGT_HOST"],
        token=os.environ["FGT_TOKEN"],
        vdom=os.getenv("FGT_VDOM", "root"),
        verify=verify,
    )

FGT_DATE = "%Y/%m/%d %H:%M:%S"  # formato de 'expiration' no FortiOS (hora local do FortiGate)


class BadRequest(Exception):
    pass


# --- Autenticação da página ---

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify(error="não autenticado"), 401
            return redirect(url_for("login"))
        # Proteção CSRF: alterações só via fetch do próprio painel
        if request.method != "GET" and request.headers.get("X-Requested-With") != "acessowifi":
            return jsonify(error="requisição recusada"), 403
        return view(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("user", "")
        pw = request.form.get("password", "")
        if user == APP_USER and check_password_hash(APP_PASSWORD_HASH, pw):
            session.clear()
            session.permanent = True
            session["user"] = user
            return redirect(url_for("index"))
        time.sleep(1)  # freia tentativas de força bruta
        return redirect(url_for("login", erro=1))
    return send_from_directory(app.static_folder, "login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(FortiGateError)
def fortigate_error(e):
    app.logger.error("FortiGate: %s", e)
    return jsonify(error=str(e)), 502


@app.errorhandler(BadRequest)
def bad_request(e):
    return jsonify(error=str(e)), 400


# --- Normalização dos dados do FortiOS ---

def _names(items):
    return [i.get("name") for i in items or [] if i.get("name")]


def _parse_fgt_date(value):
    try:
        return datetime.strptime(value, FGT_DATE)
    except (TypeError, ValueError):
        return None


def managed_groups(groups):
    guest = [g["name"] for g in groups if g.get("group-type") == "guest"]
    return [g for g in guest if g in GUEST_GROUPS] if GUEST_GROUPS else guest


def saml_groups(groups):
    """Grupos que têm um servidor SAML (ex.: Google Workspace) como membro."""
    saml = set(_names(fgt.saml_servers()))
    return {g["name"] for g in groups if saml & set(_names(g.get("member")))}


def registered_users(groups=None):
    groups = groups if groups is not None else fgt.user_groups()
    editable = set(managed_groups(groups))
    now = datetime.now()

    member_of = {}
    for g in groups:
        for name in _names(g.get("member")):
            member_of.setdefault(name, []).append(g["name"])

    users = []
    for u in fgt.local_users():
        users.append({
            "username": u.get("name"),
            "kind": "local",
            "enabled": u.get("status") == "enable",
            "name": "", "email": u.get("email-to", ""), "phone": u.get("sms-phone", ""), "company": "",
            "expiration": None, "expired": False,
            "group": None, "groups": member_of.get(u.get("name"), []),
            "editable": False,
        })  # o campo 'passwd' é descartado de propósito

    for g in groups:
        for gu in g.get("guest") or []:
            exp = _parse_fgt_date(gu.get("expiration"))
            users.append({
                "id": gu.get("id"),
                "username": gu.get("user-id"),
                "kind": "guest",
                "enabled": True,
                "name": gu.get("name", ""),
                "email": gu.get("email", ""),
                "phone": gu.get("mobile-phone", ""),
                "company": gu.get("company", ""),
                "expiration": exp.strftime("%Y-%m-%dT%H:%M") if exp else None,
                "expired": bool(exp and exp <= now),
                "group": g["name"],
                "groups": [g["name"]],
                "editable": g["name"] in editable,
            })
    return users


def normalize_mac(value):
    hexdigits = re.sub(r"[^0-9a-f]", "", str(value or "").lower())
    if len(hexdigits) != 12:
        raise BadRequest("Endereço MAC inválido.")
    return ":".join(hexdigits[i:i + 2] for i in range(0, 12, 2))


def is_random_mac(mac):
    """MAC privado/aleatório (bit 'localmente administrado'), usado por iOS e Android."""
    try:
        return bool(int(mac[1], 16) & 2)
    except (IndexError, ValueError):
        return False


# O vínculo aparelho -> usuário fica no comentário do endereço no FortiGate:
# "acessowifi;user=joao.pereira;group=WiFi-Cadastrados;host=Galaxy-A54"  (validade segue a conta)
# "acessowifi;user=fulano@acampamento.org.br;exp=2026/12/01 23:59:00;host=iPhone"  (validade própria)
def _encode_comment(meta):
    clean = {k: re.sub(r"[;=]", "", str(v))[:64] for k, v in meta.items() if v}
    return ";".join(["acessowifi"] + [f"{k}={v}" for k, v in clean.items()])


def _decode_comment(comment):
    parts = (comment or "").split(";")
    if not parts or parts[0] != "acessowifi":
        return {}
    return dict(p.split("=", 1) for p in parts[1:] if "=" in p)


def _address_mac(address):
    mac = address.get("macaddr")
    if isinstance(mac, list):
        mac = mac[0].get("macaddr") if mac else ""
    return (mac or "").lower()


def allowed_devices(groups=None):
    """Aparelhos liberados por MAC (exceção do captive portal), com a validade resolvida."""
    groups = groups if groups is not None else fgt.user_groups()
    guests = {(u["group"], u["username"]): u for u in registered_users(groups) if u["kind"] == "guest"}
    members = set(_names(fgt.address_group(MAC_GROUP).get("member")))
    now = datetime.now()

    devices = []
    for a in fgt.addresses(MAC_PREFIX):
        if not a.get("name", "").startswith(MAC_PREFIX):
            continue
        meta = _decode_comment(a.get("comment"))
        mac = _address_mac(a)
        if meta.get("group"):
            account = guests.get((meta["group"], meta.get("user")))
            expiration = account["expiration"] if account else None
            expired = account is None or account["expired"]
        else:
            exp = _parse_fgt_date(meta.get("exp"))
            expiration = exp.strftime("%Y-%m-%dT%H:%M") if exp else None
            expired = not exp or exp <= now
        devices.append({
            "address": a["name"],
            "mac": mac,
            "username": meta.get("user", ""),
            "group": meta.get("group"),
            "follows_account": bool(meta.get("group")),
            "hostname": meta.get("host", ""),
            "expiration": expiration,
            "expired": expired,
            "active": a["name"] in members,
            "random_mac": is_random_mac(mac),
        })
    return devices


def remove_device(device):
    if device["active"]:
        fgt.remove_group_member(MAC_GROUP, device["address"])
    fgt.delete_address(device["address"])


def cleanup_devices():
    """Remove liberações vencidas (conta expirada/removida) e endereços órfãos."""
    removed = []
    for d in allowed_devices():
        if d["expired"] or not d["active"]:
            remove_device(d)
            removed.append(d)
            app.logger.info("liberação de %s (%s) removida: %s", d["mac"], d["username"],
                            "vencida" if d["expired"] else "fora do grupo")
    return removed


def connected_clients(groups=None):
    groups = groups if groups is not None else fgt.user_groups()
    google = saml_groups(groups)
    known = {u["username"] for u in registered_users(groups)}
    devices = {d["mac"]: d for d in allowed_devices(groups) if d["active"]}

    # Quem fez login no captive portal aparece aqui, indexado por IP
    auth_by_ip = {a.get("ipaddr"): a for a in fgt.firewall_auth_users() if a.get("ipaddr")}

    now = time.time()
    clients = []
    for c in fgt.wifi_clients():
        auth = auth_by_ip.get(c.get("ip"), {})
        username = c.get("user") or auth.get("username", "")
        auth_groups = _names(auth.get("usergroup"))
        mac = (c.get("mac") or "").lower()
        device = devices.get(mac)
        if device and not username:
            username, source = device["username"], "mac"   # entrou sem portal, liberado por MAC
        elif not username:
            source = "pending"      # associado ao SSID, ainda não fez login no portal
        elif google & set(auth_groups):
            source = "google"
        elif username in known:
            source = "registered"
        else:
            source = "unknown"
        assoc = c.get("association_time")
        clients.append({
            "username": username,
            "source": source,
            "auth_groups": auth_groups,
            "ip": c.get("ip", ""),
            "mac": mac,
            "random_mac": is_random_mac(mac),
            "allowed": bool(device),
            "hostname": c.get("hostname") or c.get("host", ""),
            "os": c.get("os", ""),
            "manufacturer": c.get("manufacturer", ""),
            "ssid": c.get("ssid", ""),
            "ap": c.get("wtp_name") or c.get("wtp_id", ""),
            "radio": c.get("radio_type", ""),
            "channel": c.get("channel"),
            "signal": c.get("signal"),
            "snr": c.get("snr"),
            "rate_mbps": round(c["data_rate_bps"] / 1e6) if c.get("data_rate_bps") else None,
            "vlan": c.get("vlan_id"),
            "connected_for": int(now - assoc) if isinstance(assoc, (int, float)) and assoc > 0 else None,
            "idle": c.get("idle_time"),
            "traffic_bytes": auth.get("traffic_vol_bytes"),
        })
    return clients


# --- Validação do cadastro ---

USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # sem 0/o, 1/l/i: fácil de digitar no celular


def generate_password(length=8):
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _text(data, field, max_len=64):
    value = str(data.get(field) or "").strip()
    if len(value) > max_len:
        raise BadRequest(f"'{field}' pode ter no máximo {max_len} caracteres.")
    return value


def _expiration(data):
    try:
        exp = datetime.strptime(str(data.get("expiration", "")), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise BadRequest("Informe a data e hora de validade.")
    if exp <= datetime.now():
        raise BadRequest("A validade precisa ser no futuro.")
    if exp > datetime.now() + timedelta(days=MAX_VALIDITY_DAYS):
        raise BadRequest(f"A validade máxima é de {MAX_VALIDITY_DAYS} dias.")
    return exp.strftime(FGT_DATE)


def _password(data):
    pw = str(data.get("password") or "")
    if not pw:
        return generate_password()
    if not 6 <= len(pw) <= 64:
        raise BadRequest("A senha deve ter entre 6 e 64 caracteres.")
    return pw


def _editable_group(groups, name):
    if name not in managed_groups(groups):
        raise BadRequest("Grupo inválido ou não liberado para o painel.")
    return name


def _find_guest(groups, group, guest_id):
    for g in groups:
        if g["name"] == group:
            for gu in g.get("guest") or []:
                if gu.get("id") == guest_id:
                    return gu
    raise BadRequest("Usuário não encontrado.")


# --- API consumida pelo frontend ---

@app.get("/api/users")
@login_required
def api_users():
    groups = fgt.user_groups()
    users = registered_users(groups)
    online = {c["username"] for c in connected_clients(groups) if c["username"]}
    devices = {}
    for d in allowed_devices(groups):
        devices[d["username"]] = devices.get(d["username"], 0) + 1
    for u in users:
        u["online"] = u["username"] in online
        u["devices"] = devices.get(u["username"], 0)
    return jsonify(users)


@app.get("/api/groups")
@login_required
def api_groups():
    return jsonify(managed_groups(fgt.user_groups()))


@app.post("/api/users")
@login_required
def api_create_user():
    data = request.get_json(silent=True) or {}
    groups = fgt.user_groups()
    group = _editable_group(groups, data.get("group"))

    username = _text(data, "username")
    if not USERNAME_RE.match(username):
        raise BadRequest("Usuário: 3 a 64 caracteres, só letras, números e . _ @ -")
    if username.lower() in {u["username"].lower() for u in registered_users(groups) if u["username"]}:
        raise BadRequest(f"Já existe um usuário '{username}'.")

    password = _password(data)
    guest = {
        "user-id": username,
        "password": password,
        "name": _text(data, "name"),
        "email": _text(data, "email"),
        "mobile-phone": _text(data, "phone"),
        "company": _text(data, "company"),
        "expiration": _expiration(data),
        "sponsor": session["user"],
    }
    fgt.add_guest(group, guest)
    app.logger.info("%s cadastrou %s no grupo %s (válido até %s)",
                    session["user"], username, group, guest["expiration"])
    # A senha só é devolvida aqui, uma vez, para ser repassada à pessoa
    return jsonify(username=username, password=password, expiration=guest["expiration"]), 201


@app.put("/api/users/<group>/<int:guest_id>")
@login_required
def api_update_user(group, guest_id):
    data = request.get_json(silent=True) or {}
    groups = fgt.user_groups()
    _editable_group(groups, group)
    guest = _find_guest(groups, group, guest_id)

    fields = {}
    for field, key in (("name", "name"), ("email", "email"), ("phone", "mobile-phone"), ("company", "company")):
        if field in data:
            fields[key] = _text(data, field)
    if "expiration" in data:
        fields["expiration"] = _expiration(data)
    new_password = None
    if data.get("reset_password"):
        new_password = fields["password"] = _password(data)
    if not fields:
        raise BadRequest("Nada para alterar.")

    fgt.update_guest(group, guest_id, fields)
    app.logger.info("%s alterou %s (%s)", session["user"], guest.get("user-id"),
                    ", ".join(k for k in fields if k != "password") + (" + senha" if new_password else ""))
    return jsonify(username=guest.get("user-id"), password=new_password)


@app.delete("/api/users/<group>/<int:guest_id>")
@login_required
def api_delete_user(group, guest_id):
    groups = fgt.user_groups()
    _editable_group(groups, group)
    guest = _find_guest(groups, group, guest_id)
    fgt.delete_guest(group, guest_id)
    app.logger.info("%s removeu %s do grupo %s", session["user"], guest.get("user-id"), group)
    for d in allowed_devices():
        if d["group"] == group and d["username"] == guest.get("user-id"):
            remove_device(d)
            app.logger.info("liberação de %s removida junto com a conta", d["mac"])
    return jsonify(ok=True)


@app.get("/api/devices")
@login_required
def api_devices():
    online = {c["mac"] for c in connected_clients()}
    devices = allowed_devices()
    for d in devices:
        d["online"] = d["mac"] in online
    return jsonify(devices)


@app.post("/api/devices")
@login_required
def api_allow_device():
    data = request.get_json(silent=True) or {}
    mac = normalize_mac(data.get("mac"))
    username = _text(data, "username")
    if not username:
        raise BadRequest("Informe o usuário dono do aparelho.")
    groups = fgt.user_groups()
    if any(d["mac"] == mac for d in allowed_devices(groups)):
        raise BadRequest("Este aparelho já está liberado.")

    meta = {"user": username}
    account = next((u for u in registered_users(groups)
                    if u["kind"] == "guest" and u["editable"] and u["username"].lower() == username.lower()), None)
    if account:
        if account["expired"]:
            raise BadRequest("A conta deste usuário está expirada. Renove antes de liberar o aparelho.")
        meta.update(user=account["username"], group=account["group"])   # validade segue a conta
    else:
        meta["exp"] = _expiration(data)                                  # ex.: usuário do Google
    meta["host"] = _text(data, "hostname")

    name = MAC_PREFIX + mac.replace(":", "")
    fgt.create_address({"name": name, "type": "mac", "macaddr": [{"macaddr": mac}],
                        "comment": _encode_comment(meta)})
    try:
        fgt.add_group_member(MAC_GROUP, name)
    except FortiGateError:
        fgt.delete_address(name)
        raise
    app.logger.info("%s liberou o aparelho %s de %s (%s)", session["user"], mac, meta["user"],
                    f"segue a conta {meta['group']}" if account else f"até {meta['exp']}")
    return jsonify(ok=True, mac=mac), 201


@app.delete("/api/devices/<mac>")
@login_required
def api_remove_device(mac):
    mac = normalize_mac(mac)
    device = next((d for d in allowed_devices() if d["mac"] == mac), None)
    if not device:
        raise BadRequest("Aparelho não encontrado.")
    remove_device(device)
    app.logger.info("%s removeu a liberação de %s (%s)", session["user"], mac, device["username"])
    return jsonify(ok=True)


@app.cli.command("check-fortigate")
@click.option("--escrita", is_flag=True, help="Também cria e apaga um usuário e um endereço de teste.")
def check_fortigate_command(escrita):
    """Testa a conexão, as permissões do token e os grupos configurados."""
    failures = 0

    def check(label, fn):
        nonlocal failures
        try:
            detail = fn()
            print(f"  [OK]   {label}" + (f" — {detail}" if detail else ""))
            return True
        except Exception as e:  # noqa: BLE001 — o objetivo é relatar qualquer falha
            failures += 1
            print(f"  [ERRO] {label} — {e}")
            return False

    print(f"FortiGate: {os.getenv('FGT_HOST', '(demo)')}  vdom: {os.getenv('FGT_VDOM', 'root')}")
    if not check("Conexão e token (lista de usuários)", lambda: f"{len(fgt.local_users())} usuário(s) local(is)"):
        print("\nSem conexão: confira FGT_HOST, FGT_TOKEN, o trusted host e 'allowaccess https' na interface.")
        raise SystemExit(1)

    groups = fgt.user_groups()
    check("Grupos de usuário", lambda: f"{len(groups)} grupo(s)")
    check("Clientes Wi-Fi (FortiAP)", lambda: f"{len(fgt.wifi_clients())} conectado(s)")
    check("Usuários autenticados (captive portal)", lambda: f"{len(fgt.firewall_auth_users())} sessão(ões)")
    check("Access points", lambda: f"{len(fgt.managed_aps())} AP(s)")
    check("Servidores SAML (Google)", lambda: ", ".join(_names(fgt.saml_servers())) or "nenhum")

    def guest_groups():
        found = managed_groups(groups)
        missing = [g for g in GUEST_GROUPS if g not in found]
        if missing:
            raise FortiGateError(f"grupo(s) guest não encontrado(s): {', '.join(missing)}")
        if not found:
            raise FortiGateError("nenhum grupo do tipo guest")
        return ", ".join(found)

    has_guest = check("Grupo(s) guest para cadastro", guest_groups)
    has_mac = check(f"Grupo de endereços '{MAC_GROUP}'",
                    lambda: f"{len(fgt.address_group(MAC_GROUP).get('member') or [])} membro(s)")

    if escrita and has_guest:
        group = managed_groups(groups)[0]
        exp = (datetime.now() + timedelta(hours=1)).strftime(FGT_DATE)

        def guest_roundtrip():
            fgt.add_guest(group, {"user-id": "acessowifi.teste", "password": generate_password(),
                                  "name": "Teste do painel", "expiration": exp})
            saved = next(gu for g in fgt.user_groups() if g["name"] == group
                         for gu in g.get("guest") or [] if gu.get("user-id") == "acessowifi.teste")
            fgt.delete_guest(group, saved["id"])
            if saved.get("expiration") != exp:
                raise FortiGateError(f"validade gravada como '{saved.get('expiration')}', esperado '{exp}'")
            return f"criado e apagado em {group}, validade gravada corretamente"

        check("Escrita: usuário guest de teste", guest_roundtrip)

    if escrita and has_mac:
        name = MAC_PREFIX + "teste"

        def address_roundtrip():
            fgt.create_address({"name": name, "type": "mac", "macaddr": [{"macaddr": "02:11:22:33:44:55"}],
                                "comment": "acessowifi;teste"})
            try:
                fgt.add_group_member(MAC_GROUP, name)
                fgt.remove_group_member(MAC_GROUP, name)
            finally:
                fgt.delete_address(name)
            return f"criado, incluído em {MAC_GROUP} e apagado"

        check("Escrita: endereço MAC de teste", address_roundtrip)

    print("\nTudo certo!" if not failures else f"\n{failures} problema(s) encontrado(s). Veja o README.")
    raise SystemExit(1 if failures else 0)


@app.cli.command("cleanup-devices")
def cleanup_devices_command():
    """Remove liberações por MAC vencidas. Rodar periodicamente (systemd timer)."""
    removed = cleanup_devices()
    print(f"{len(removed)} liberação(ões) removida(s)")


@app.get("/api/clients")
@login_required
def api_clients():
    return jsonify(connected_clients())


@app.get("/api/aps")
@login_required
def api_aps():
    count = {}
    for c in connected_clients():
        count[c["ap"]] = count.get(c["ap"], 0) + 1
    return jsonify([
        {
            "name": a.get("name") or a.get("wtp_id"),
            "serial": a.get("wtp_id", ""),
            "status": a.get("status", ""),
            "ip": a.get("local_ipv4_addr", ""),
            "version": a.get("os_version", ""),
            "clients": count.get(a.get("name") or a.get("wtp_id"), 0),
        }
        for a in fgt.managed_aps()
    ])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 8080)), debug=False)
