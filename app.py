"""Painel de acesso Wi-Fi — lê usuários cadastrados e clientes conectados no FortiGate."""
import os
import time
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

from fortigate import FortiGate, FortiGateError

load_dotenv()

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


# --- Autenticação da página ---

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify(error="não autenticado"), 401
            return redirect(url_for("login"))
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
    return jsonify(error=str(e)), 502


# --- Normalização dos dados do FortiOS ---

def _names(items):
    return [i.get("name") for i in items or [] if i.get("name")]


def registered_users():
    groups = fgt.user_groups()

    member_of = {}
    for g in groups:
        for name in _names(g.get("member")):
            member_of.setdefault(name, []).append(g["name"])

    users = []
    for u in fgt.local_users():
        users.append({
            "username": u.get("name"),
            "kind": "local",
            "auth": u.get("type", "password"),
            "enabled": u.get("status") == "enable",
            "name": "",
            "email": u.get("email-to", ""),
            "phone": u.get("sms-phone", ""),
            "company": "",
            "two_factor": u.get("two-factor", "disable"),
            "expiration": "",
            "groups": member_of.get(u.get("name"), []),
        })  # o campo 'passwd' é descartado de propósito

    for g in groups:
        for gu in g.get("guest") or []:
            users.append({
                "username": gu.get("user-id"),
                "kind": "guest",
                "auth": "guest",
                "enabled": True,
                "name": gu.get("name", ""),
                "email": gu.get("email", ""),
                "phone": gu.get("mobile-phone", ""),
                "company": gu.get("company", ""),
                "two_factor": "disable",
                "expiration": gu.get("expiration", ""),
                "groups": [g["name"]],
            })
    return users


def connected_clients():
    # Usuários autenticados por captive portal / firewall auth, indexados por IP
    auth_by_ip = {a.get("ipaddr"): a for a in fgt.firewall_auth_users() if a.get("ipaddr")}

    now = time.time()
    clients = []
    for c in fgt.wifi_clients():
        auth = auth_by_ip.get(c.get("ip"), {})
        assoc = c.get("association_time")
        clients.append({
            "username": c.get("user") or auth.get("username", ""),
            "auth_method": "802.1X" if c.get("user") else (auth.get("method", "") if auth else ""),
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


# --- API consumida pelo frontend ---

@app.get("/api/users")
@login_required
def api_users():
    users = registered_users()
    online = {c["username"] for c in connected_clients() if c["username"]}
    for u in users:
        u["online"] = u["username"] in online
    return jsonify(users)


@app.get("/api/clients")
@login_required
def api_clients():
    known = {u["username"] for u in registered_users()}
    clients = connected_clients()
    for c in clients:
        c["registered"] = c["username"] in known
    return jsonify(clients)


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
