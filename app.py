"""Painel de acesso Wi-Fi — usuários do captive portal e clientes conectados no FortiGate."""
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

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


def connected_clients(groups=None):
    groups = groups if groups is not None else fgt.user_groups()
    google = saml_groups(groups)
    known = {u["username"] for u in registered_users(groups)}

    # Quem fez login no captive portal aparece aqui, indexado por IP
    auth_by_ip = {a.get("ipaddr"): a for a in fgt.firewall_auth_users() if a.get("ipaddr")}

    now = time.time()
    clients = []
    for c in fgt.wifi_clients():
        auth = auth_by_ip.get(c.get("ip"), {})
        username = c.get("user") or auth.get("username", "")
        auth_groups = _names(auth.get("usergroup"))
        if not username:
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
            "mac": c.get("mac", ""),
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
    for u in users:
        u["online"] = u["username"] in online
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
    return jsonify(ok=True)


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
