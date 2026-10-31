"""Painel de acesso Wi-Fi — usuários do captive portal e clientes conectados no FortiGate."""
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

import ssids
from fortigate import FortiGate, FortiGateError

ENV_FILE = Path(__file__).resolve().with_name(".env")
if ENV_FILE.exists() and not os.access(ENV_FILE, os.R_OK):
    raise SystemExit(f"Sem permissão para ler {ENV_FILE}. Rode como root: acessowifi testar")
load_dotenv(ENV_FILE)
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


# --- PWA (instalar o painel como app) e certificado do servidor ---
CA_FILE = Path(os.getenv("ACESSOWIFI_CA", "/etc/ssl/acessowifi/ca.pem"))


@app.get("/sw.js")
def service_worker():
    # Na raiz, para valer para o painel todo; a versão no conteúdo faz o navegador trocar a casca antiga
    body = (Path(app.static_folder) / "sw.js").read_text().replace("__VERSION__", APP_VERSION)
    return app.response_class(body, mimetype="application/javascript",
                              headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def web_manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.get("/certificado")
def certificate_page():
    return send_from_directory(app.static_folder, "certificado.html")


@app.get("/certificado/acessowifi-ca.crt")
def certificate_download():
    if not CA_FILE.is_file():
        return jsonify(error="Este servidor ainda não tem o certificado próprio."), 404
    return app.response_class(CA_FILE.read_bytes(), mimetype="application/x-x509-ca-cert",
                              headers={"Content-Disposition": "attachment; filename=acessowifi-ca.crt"})


@app.get("/certificado/info")
def certificate_info():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert = x509.load_pem_x509_certificate(CA_FILE.read_bytes())
    except (OSError, ValueError, ImportError):
        return jsonify(available=False)
    name = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    scope = []
    try:
        nc = cert.extensions.get_extension_for_class(x509.NameConstraints).value
        scope = [str(getattr(g, "value", g)) for g in nc.permitted_subtrees or []]
    except x509.ExtensionNotFound:
        pass
    return jsonify(available=True, name=name[0].value if name else "", scope=scope,
                   not_after=cert.not_valid_after_utc.isoformat(),
                   fingerprint=cert.fingerprint(hashes.SHA256()).hex(":").upper())


@app.errorhandler(FortiGateError)
def fortigate_error(e):
    app.logger.error("FortiGate: %s", e)
    return jsonify(error=str(e)), 502


@app.errorhandler(Exception)
def unexpected_error(e):
    """Erro inesperado: mostra o motivo na tela (não só "Erro 500") e grava o detalhe no log."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Erro inesperado em %s", request.path)
    if not request.path.startswith("/api/"):
        return "Erro interno do painel. Detalhes: acessowifi log", 500
    return jsonify(error=f"Erro interno do painel ({type(e).__name__}: {e}). "
                         f"Versão {APP_VERSION}. Detalhes no log do servidor: acessowifi log"), 500


@app.errorhandler(ssids.SSIDError)
def ssid_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(BadRequest)
def bad_request(e):
    return jsonify(error=str(e)), 400


# --- Normalização dos dados do FortiOS ---

def _names(items):
    return [i.get("name") for i in items or [] if i.get("name")]


# --- Validade das contas guest ---
# O formato do campo "expiration" das contas guest não é documentado e mudou entre versões
# (no FortiOS 7.4.13, "AAAA/MM/DD HH:MM:SS" foi gravado como 0). O painel grava, relê e, se o
# FortiGate não guardou a data certa, tenta o próximo formato. O que funcionar fica salvo em
# CTL_DIR/expiration-format e é usado direto nas próximas vezes.
EXP_FORMATS = {
    "data": lambda dt: dt.strftime("%Y/%m/%d %H:%M:%S"),
    "data-iso": lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S"),
    "epoch": lambda dt: int(dt.timestamp()),
    "epoch-texto": lambda dt: str(int(dt.timestamp())),
    "hora-data": lambda dt: dt.strftime("%H:%M:%S %Y/%m/%d"),
    "data-minutos": lambda dt: dt.strftime("%Y/%m/%d %H:%M"),
}
_exp_format = {"name": None}


def parse_expiration(value):
    """Lê a validade em qualquer formato que o FortiGate devolva (texto ou segundos desde 1970)."""
    if value in (None, "", 0, "0"):
        return None
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text)) if int(text) > 10 ** 9 else None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%H:%M:%S %Y/%m/%d", "%Y/%m/%d %H:%M",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _known_exp_format():
    if not _exp_format["name"]:
        try:
            name = (CTL_DIR / "expiration-format").read_text().strip()
            _exp_format["name"] = name if name in EXP_FORMATS else None
        except OSError:
            pass
    return _exp_format["name"] or "data"


def _remember_exp_format(name):
    _exp_format["name"] = name
    try:
        (CTL_DIR / "expiration-format").write_text(name)
    except OSError:
        pass   # sem a pasta (ex.: testes), vale só enquanto o painel estiver rodando


def _guest_entry(group, guest_id):
    for g in fgt.user_groups():
        if g["name"] == group:
            for gu in g.get("guest") or []:
                if gu.get("id") == guest_id:
                    return gu
    return None


def _expiration_ok(group, guest_id, when):
    stored = (_guest_entry(group, guest_id) or {}).get("expiration")
    got = parse_expiration(stored)
    return bool(got and abs((got - when).total_seconds()) <= 90), stored


def set_expiration(group, guest_id, when, sent=None):
    """Garante que a conta tenha a validade 'when'. 'sent' = formato já enviado no cadastro."""
    tried = []
    if sent:
        ok, stored = _expiration_ok(group, guest_id, when)
        if ok:
            _remember_exp_format(sent)
            return sent
        tried.append(f"{sent} -> '{stored}'")
    for name in [_known_exp_format()] + [n for n in EXP_FORMATS if n != _known_exp_format()]:
        if name == sent:
            continue
        fgt.update_guest(group, guest_id, {"expiration": EXP_FORMATS[name](when)})
        ok, stored = _expiration_ok(group, guest_id, when)
        if ok:
            _remember_exp_format(name)
            if tried:
                app.logger.info("Formato de validade do FortiGate detectado: %s", name)
            return name
        tried.append(f"{name} -> '{stored}'")
    raise FortiGateError("O FortiGate não gravou a validade em nenhum formato conhecido ("
                         + "; ".join(tried) + "). Mande esta mensagem para ajuste do painel.")


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
            exp = parse_expiration(gu.get("expiration"))
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
    """Validade como texto no formato do painel (usado nos comentários dos endereços MAC)."""
    return _expiration_dt(data).strftime(FGT_DATE)


def _expiration_dt(data):
    try:
        exp = datetime.strptime(str(data.get("expiration", "")), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise BadRequest("Informe a data e hora de validade.")
    if exp <= datetime.now():
        raise BadRequest("A validade precisa ser no futuro.")
    if exp > datetime.now() + timedelta(days=MAX_VALIDITY_DAYS):
        raise BadRequest(f"A validade máxima é de {MAX_VALIDITY_DAYS} dias.")
    return exp


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
    when = _expiration_dt(data)
    fmt = _known_exp_format()
    guest = {
        "user-id": username,
        "password": password,
        "name": _text(data, "name"),
        "email": _text(data, "email"),
        "mobile-phone": _text(data, "phone"),
        "company": _text(data, "company"),
        "expiration": EXP_FORMATS[fmt](when),
        "sponsor": session["user"],
    }
    fgt.add_guest(group, guest)
    created = next((gu for g in fgt.user_groups() if g["name"] == group
                    for gu in g.get("guest") or [] if gu.get("user-id") == username), None)
    if not created:
        raise FortiGateError("A conta foi enviada ao FortiGate, mas não apareceu no grupo.")
    try:
        set_expiration(group, created["id"], when, sent=fmt)
    except FortiGateError:
        fgt.delete_guest(group, created["id"])   # não deixa conta sem validade para trás
        raise
    app.logger.info("%s cadastrou %s no grupo %s (válido até %s)",
                    session["user"], username, group, when.strftime(FGT_DATE))
    # A senha só é devolvida aqui, uma vez, para ser repassada à pessoa
    return jsonify(username=username, password=password, expiration=when.strftime(FGT_DATE)), 201


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
    when = _expiration_dt(data) if "expiration" in data else None
    new_password = None
    if data.get("reset_password"):
        new_password = fields["password"] = _password(data)
    if not fields and not when:
        raise BadRequest("Nada para alterar.")

    if fields:
        fgt.update_guest(group, guest_id, fields)
    if when:
        set_expiration(group, guest_id, when)
        fields["expiration"] = when
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


# --- Atualizações (botão no painel) ---
# O painel não tem privilégio para instalar: grava um pedido em CTL_DIR e as units
# acessowifi-update*.path executam o deploy/acessowifi-update.sh como root.
CTL_DIR = Path(os.getenv("ACESSOWIFI_CTL", "/var/lib/acessowifi-ctl"))
try:
    APP_VERSION = (Path(__file__).resolve().with_name("VERSION")).read_text().strip()
except OSError:
    APP_VERSION = "?"
_demo_updates = {"state": "idle", "message": "2 atualização(ões) disponível(is).", "log": [],
                 "current": {"hash": "a1b2c3d", "subject": "Versão instalada", "date": "2026-10-30T10:00:00-03:00",
                             "version": APP_VERSION},
                 "target_version": "1.2",
                 "pending_versions": [
                     {"version": "1.2", "date": "2026-11-02", "changes": ["Relatório de uso por SSID.",
                                                                         "Exportar a lista de usuários em planilha."]},
                     {"version": "1.1", "date": "2026-11-01", "changes": ["Ajustes de cores na tela de login."]}],
                 "pending": [{"hash": "e4f5a6b", "subject": "Aba SSIDs: escolha do tipo de acesso",
                              "date": "2026-10-31T09:12:00-03:00"},
                             {"hash": "c7d8e9f", "subject": "Interface: botão de atualizações",
                              "date": "2026-10-31T10:40:00-03:00"}],
                 "checked_at": int(time.time()), "_done_at": None}


def _update_status():
    if os.getenv("DEMO_MODE") == "1":
        st = _demo_updates
        if st["_done_at"] and time.time() >= st["_done_at"]:
            st.update(state="ok", message="Versão 1.2 instalada.", _done_at=None, checked_at=int(time.time()),
                      current={**st["pending"][-1], "version": "1.2"}, pending=[], pending_versions=[],
                      log=st["log"] + ["Atualizado para a versão 1.2."])
        return {k: v for k, v in st.items() if not k.startswith("_")}
    try:
        with open(CTL_DIR / "status.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"state": "unavailable", "pending": [],
                "message": "Atualizações pelo painel não configuradas. Rode ./deploy/install.sh no servidor."}


def _request(kind):
    if os.getenv("DEMO_MODE") == "1":
        if kind == "install" and _demo_updates["pending"]:
            _demo_updates.update(state="installing", message="Instalando a atualização...",
                                 log=["Atualizando..."], _done_at=time.time() + 4, checked_at=int(time.time()) + 1)
        return
    if not CTL_DIR.is_dir():
        raise BadRequest("Atualizações pelo painel não configuradas. Rode ./deploy/install.sh no servidor.")
    (CTL_DIR / kind).write_text(session["user"])


@app.get("/api/updates")
@login_required
def api_updates():
    return jsonify({**_update_status(), "installed_version": APP_VERSION})


@app.post("/api/updates/check")
@login_required
def api_updates_check():
    _request("check")
    return jsonify(ok=True)


@app.post("/api/updates/install")
@login_required
def api_updates_install():
    status = _update_status()
    if status.get("state") in ("installing", "checking"):
        raise BadRequest("Já existe uma operação de atualização em andamento.")
    _request("install")
    app.logger.info("%s pediu a instalação de %d atualização(ões)", session["user"], len(status.get("pending", [])))
    return jsonify(ok=True)


# --- SSIDs extras (com agendamento e escolha de FortiAPs) ---

@app.get("/api/ssids")
@login_required
def api_ssids():
    return jsonify(ssids.list_ssids(fgt))


@app.put("/api/ssids-main")
@login_required
def api_set_main_ssid():
    name = str((request.get_json(silent=True) or {}).get("name") or "")
    ssids.set_main_vap(fgt, name)
    app.logger.info("%s definiu o SSID principal: %s", session["user"], name)
    return jsonify(ok=True)


@app.post("/api/ssids")
@login_required
def api_create_ssid():
    data = request.get_json(silent=True) or {}
    name = ssids.create_ssid(fgt, data)
    app.logger.info("%s criou o SSID '%s' (%s), modo %s, APs %s", session["user"], data.get("ssid"), name,
                    data.get("mode"), ", ".join(data.get("aps") or []))
    return jsonify(ok=True, name=name), 201


@app.put("/api/ssids/<name>")
@login_required
def api_update_ssid(name):
    data = request.get_json(silent=True) or {}
    ssids.update_ssid(fgt, name, data)
    app.logger.info("%s alterou o SSID %s: %s", session["user"], name, ", ".join(sorted(data)))
    return jsonify(ok=True)


@app.delete("/api/ssids/<name>")
@login_required
def api_delete_ssid(name):
    ssids.delete_ssid(fgt, name)
    app.logger.info("%s removeu o SSID %s", session["user"], name)
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
    if not check("Conexão e token (lista de usuários)", lambda: f"{len(fgt.local_users())} usuário(s) local(is)"
                 + (f", FortiOS {fgt.version}" if getattr(fgt, "version", None) else "")):
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

    def ssid_check():
        ready, reason, switch = ssids.readiness(fgt)
        if not ready:
            raise FortiGateError(reason)
        info = ssids.main_info(fgt)
        how = {"bridge": "modo bridge (mesma VLAN)", "switch": f"software switch '{switch}' (mesma rede)",
               "own": "modo túnel (cada SSID novo ganha rede própria)"}.get(info["mode"], info["mode"])
        return f"principal '{info['name']}', {how}"
    check("SSIDs extras (aba SSIDs)", ssid_check)

    if escrita and has_guest:
        group = managed_groups(groups)[0]
        when = (datetime.now() + timedelta(hours=1)).replace(microsecond=0)

        def guest_roundtrip():
            # Sem validade primeiro: mostra como o próprio FortiGate preenche o campo
            fgt.add_guest(group, {"user-id": "acessowifi.teste", "password": generate_password(),
                                  "name": "Teste do painel"})
            saved = next(gu for g in fgt.user_groups() if g["name"] == group
                         for gu in g.get("guest") or [] if gu.get("user-id") == "acessowifi.teste")
            native = saved.get("expiration")
            try:
                fmt = set_expiration(group, saved["id"], when)
            finally:
                fgt.delete_guest(group, saved["id"])
            return f"criado e apagado em {group}; validade gravada no formato '{fmt}' (padrão do FortiGate: '{native}')"

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
