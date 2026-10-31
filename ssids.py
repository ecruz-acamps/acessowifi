"""SSIDs extras: criados a partir do SSID principal, com agendamento e escolha de FortiAPs.

Tudo fica no próprio FortiGate, que liga e desliga os SSIDs sozinho:
- o SSID novo copia a segurança do principal (captive portal, grupos, lista de exceção);
- mesma rede do principal: mesma VLAN (SSID em bridge) ou o mesmo software switch (SSID em túnel);
- agendamento: o campo "schedule" do SSID aponta para "always" (ligado), para um agendamento
  vencido (desligado) ou para um agendamento único do firewall com início e fim (período);
- FortiAPs: cada AP recebe, por rádio, a lista de SSIDs que deve transmitir (override-vaps).

O painel só altera SSIDs com o prefixo "aw-" (os que ele mesmo criou).
"""
import ipaddress
import os
import re
import shlex
from datetime import datetime

from fortigate import FortiGateError

VAP = "cmdb/wireless-controller/vap"
WTP = "cmdb/wireless-controller/wtp"
PROFILE = "cmdb/wireless-controller/wtp-profile"
ONETIME = "cmdb/firewall.schedule/onetime"
SWITCH = "cmdb/system/switch-interface"
INTERFACE = "cmdb/system/interface"
DHCP = "cmdb/system.dhcp/server"
POLICY = "cmdb/firewall/policy"
# Faixa de onde saem as redes próprias dos SSIDs novos (uma /24 livre para cada SSID)
NETWORK_POOL = os.getenv("FGT_SSID_NETWORKS", "10.250.0.0/16")
# Copiados da política de internet do SSID principal para a do SSID novo
POLICY_FIELDS = (
    "dstintf", "dstaddr", "service", "schedule", "nat", "utm-status", "inspection-mode", "ssl-ssh-profile",
    "av-profile", "webfilter-profile", "dnsfilter-profile", "application-list", "ips-sensor",
    "file-filter-profile", "emailfilter-profile", "logtraffic", "groups", "users",
)

PREFIX = "aw-"                 # nome interno dos SSIDs criados pelo painel (FortiOS: até 15 caracteres)
OFF_SCHEDULE = "aw-desligado"  # agendamento já vencido: o SSID fica fora do ar
ALWAYS = "always"
RADIOS = ("radio-1", "radio-2", "radio-3", "radio-4")
SCHED_FMT = "%H:%M %Y/%m/%d"   # formato do FortiOS para agendamento único
INPUT_FMT = "%Y-%m-%dT%H:%M"   # formato do <input type=datetime-local>

# Rede: sempre copiada do SSID principal (é o que coloca o SSID novo na mesma rede)
NETWORK_FIELDS = ("local-bridging", "vlanid", "intra-vap-privacy")
# Segurança: copiada do principal só no acesso "igual ao principal"
SECURITY_FIELDS = (
    "security", "captive-portal", "portal-type", "auth", "selected-usergroups", "usergroup",
    "security-exempt-list", "security-redirect-url", "external-web", "external-logout",
    "portal-message-override-group", "local-authentication", "pmf",
)
ACCESS = ("same", "open", "wpa2")   # igual ao principal, sem senha, WPA2 com senha


def access_of(vap):
    """Tipo de acesso de um SSID: portal, open, wpa2, enterprise ou o valor bruto."""
    sec = vap.get("security", "")
    if sec == "captive-portal" or vap.get("captive-portal") == "enable":
        return "portal"
    if sec == "open":
        return "open"
    if "personal" in sec:
        return "wpa2"
    if "enterprise" in sec:
        return "enterprise"
    return sec or "?"


def _security_body(main, data, current=None):
    """Campos de segurança do SSID para o acesso escolhido."""
    kind = data.get("security", "same")
    if kind not in ACCESS:
        raise SSIDError("Escolha o tipo de acesso do SSID.")
    if kind == "same":
        return {k: main[k] for k in SECURITY_FIELDS if k in main}

    ref = {**main, **(current or {})}
    body = {}
    # Sem portal: desliga o captive portal e tira os grupos/lista de exceção copiados do principal
    if "captive-portal" in ref:
        body["captive-portal"] = "disable"
    if ref.get("selected-usergroups"):
        body["selected-usergroups"] = []
    if ref.get("security-exempt-list"):
        body["security-exempt-list"] = ""
    if kind == "open":
        body["security"] = "open"
        return body

    password = str(data.get("passphrase") or "")
    keep = current is not None and access_of(current) == "wpa2" and not password
    if not keep and not (8 <= len(password) <= 63 and password.isascii() and password.isprintable()):
        raise SSIDError("A senha WPA2 deve ter de 8 a 63 caracteres (sem acentos).")
    body.update({"security": "wpa2-only-personal", "encrypt": "AES"})
    if not keep:
        body["passphrase"] = password
    return body


class SSIDError(Exception):
    pass


def _names(value, key="name"):
    """Nomes de um campo de referência em qualquer formato do FortiOS:
    texto ("always" ou '"a" "b"'), objeto ({"name": ...}) ou lista de objetos/textos."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            return [v for v in shlex.split(value) if v]
        except ValueError:
            return value.replace('"', "").split()
    if isinstance(value, dict):
        return [value[key]] if value.get(key) else ([value["name"]] if value.get("name") else [])
    if isinstance(value, list):
        return [n for item in value for n in _names(item, key)]
    return [str(value)]


def _first(value, key="name"):
    names = _names(value, key)
    return names[0] if names else ""


def _schedule_value(fgt, name):
    """Grava o agendamento no mesmo formato que o SSID principal usa (texto ou lista)."""
    main = fgt.get_one(VAP, main_vap_name()) or {}
    return [{"name": name}] if isinstance(main.get("schedule"), list) else name


MAIN_FILE = os.path.join(os.getenv("ACESSOWIFI_CTL", "/var/lib/acessowifi-ctl"), "main-ssid")


def main_vap_name():
    """SSID principal: do .env (instalador) ou escolhido na própria aba SSIDs."""
    name = os.getenv("FGT_MAIN_SSID", "").strip()
    if not name:
        try:
            with open(MAIN_FILE) as f:
                name = f.read().strip()
        except OSError:
            pass
    return name


def set_main_vap(fgt, name):
    if not fgt.get_one(VAP, name):
        raise SSIDError("SSID não encontrado.")
    if os.getenv("FGT_MAIN_SSID", "").strip():
        raise SSIDError("O SSID principal foi definido na instalação; para trocar, refaça a configuração.")
    try:
        with open(MAIN_FILE, "w") as f:
            f.write(name)
    except OSError:
        raise SSIDError("Não consegui salvar a escolha no servidor. Rode ./deploy/install.sh.")


def main_info(fgt):
    """Como está o SSID principal: nome, modo (bridge, switch ou túnel) e software switch."""
    main = main_vap_name()
    vap = fgt.get_one(VAP, main) if main else None
    if not vap:
        return {"name": main, "mode": None, "switch": None}
    if vap.get("local-bridging") == "enable":
        return {"name": main, "mode": "bridge", "switch": None}
    sw = _switch_of(fgt, main)
    return {"name": main, "mode": "switch" if sw else "own", "switch": sw["name"] if sw else None}


def _switch_of(fgt, vap):
    for sw in fgt.get(SWITCH):
        if vap in _names(sw.get("member"), "interface-name"):
            return sw
    return None


def readiness(fgt):
    """O SSID novo pode ficar na mesma rede do principal? Devolve (pronto, motivo, switch)."""
    main = main_vap_name()
    if not main:
        return False, "Escolha abaixo qual é o SSID principal (o modelo e a rede dos SSIDs novos).", None
    vap = fgt.get_one(VAP, main)
    if not vap:
        return False, f"O SSID principal '{main}' não existe mais no FortiGate.", None
    if vap.get("local-bridging") == "enable":
        return True, "", None   # bridge: o SSID novo usa a mesma VLAN
    sw = _switch_of(fgt, main)
    if sw:
        return True, "", sw["name"]
    # Túnel fora de software switch: cada SSID novo ganha rede própria, com a política do principal como modelo
    if not _internet_policy(fgt, main):
        return False, (f"Não achei a política de firewall que dá internet ao SSID '{main}' (origem '{main}', ação "
                       "aceitar). Ela é o modelo da política dos SSIDs novos."), None
    return True, "", None


# ------------------------------------------------------------------ rede própria (SSID em túnel)

def _internet_policy(fgt, vap):
    """Política que dá internet ao SSID: origem = o SSID, ação aceitar."""
    for p in fgt.get(POLICY):
        if vap in _names(p.get("srcintf")) and p.get("action") == "accept" and _names(p.get("dstintf")):
            return p
    return None


def _networks_in_use(fgt):
    used = []
    for i in fgt.get(INTERFACE):
        parts = str(i.get("ip") or "").split()
        if len(parts) == 2 and parts[0] != "0.0.0.0":
            used.append(ipaddress.ip_interface(f"{parts[0]}/{parts[1]}").network)
    for d in fgt.get(DHCP):
        for r in d.get("ip-range") or []:
            if r.get("start-ip"):
                used.append(ipaddress.ip_network(f"{r['start-ip']}/{d.get('netmask', '255.255.255.0')}", strict=False))
    return used


def _free_network(fgt):
    used = _networks_in_use(fgt)
    for net in ipaddress.ip_network(NETWORK_POOL).subnets(new_prefix=24):
        if not any(net.overlaps(u) for u in used):
            return net
    raise SSIDError(f"Não há faixa de IP livre em {NETWORK_POOL} para o SSID novo.")


def _network_of(interfaces, name):
    parts = str((interfaces.get(name) or {}).get("ip") or "").split()
    if len(parts) == 2 and parts[0] != "0.0.0.0":
        return str(ipaddress.ip_interface(f"{parts[0]}/{parts[1]}").network)
    return None


def _create_own_network(fgt, name, access, undo):
    """Faixa de IP, DHCP e política de internet do SSID novo (modelo: a política do principal)."""
    template = _internet_policy(fgt, main_vap_name())
    net = _free_network(fgt)
    gw = net[1]
    fgt.update(INTERFACE, name, {"ip": f"{gw} {net.netmask}", "allowaccess": "ping", "role": "lan"})

    r = fgt.create(DHCP, {"status": "enable", "interface": name, "default-gateway": str(gw),
                          "netmask": str(net.netmask), "dns-service": "default", "lease-time": 28800,
                          "ip-range": [{"id": 1, "start-ip": str(net[10]), "end-ip": str(net[250])}]})
    dhcp_id = r.get("mkey") or next((d["id"] for d in fgt.get(DHCP) if d.get("interface") == name), None)
    if dhcp_id is not None:
        undo.append(lambda: fgt.remove(DHCP, dhcp_id))

    body = {k: template[k] for k in POLICY_FIELDS if k in template}
    if access != "same":   # sem captive portal: grupos na política bloqueariam o acesso
        body.pop("groups", None)
        body.pop("users", None)
    body.update({"name": name, "srcintf": [{"name": name}], "srcaddr": [{"name": "all"}], "action": "accept",
                 "comments": "Criada pelo painel Acesso Wi-Fi (SSID extra)"})
    r = fgt.create(POLICY, body)
    pid = r.get("mkey") or next((p["policyid"] for p in fgt.get(POLICY) if _first(p.get("name")) == name), None)
    if pid is None:
        raise SSIDError("A política de internet do SSID novo não foi criada.")
    undo.append(lambda: fgt.remove(POLICY, pid))
    fgt.move_policy(pid, before=template["policyid"])   # acima da do principal (antes de um eventual "negar tudo")
    return str(net)


def _remove_own_network(fgt, name):
    for p in fgt.get(POLICY):
        if _names(p.get("srcintf")) == [name]:
            fgt.remove(POLICY, p["policyid"])
    for d in fgt.get(DHCP):
        if d.get("interface") == name:
            fgt.remove(DHCP, d["id"])


# ------------------------------------------------------------------ FortiAPs

def _vap_lists(fgt):
    vaps = fgt.get(VAP)
    bridge = [v["name"] for v in vaps if v.get("local-bridging") == "enable"]
    tunnel = [v["name"] for v in vaps if v.get("local-bridging") != "enable"]
    return vaps, tunnel, bridge


def _effective(wtp, profiles, tunnel, bridge):
    """SSIDs que cada rádio do AP transmite hoje: {rádio: [nomes]}."""
    profile = profiles.get(_first(wtp.get("wtp-profile"))) or {}
    result = {}
    for radio in RADIOS:
        prof = profile.get(radio) or {}
        if not prof or prof.get("mode") in ("disabled", "monitor", "sniffer", "sam"):
            continue
        own = wtp.get(radio) or {}
        src = own if own.get("override-vaps") == "enable" else prof
        mode = src.get("vap-all", "tunnel")
        if mode in ("manual", "disable"):
            result[radio] = _names(src.get("vaps"))
        elif mode == "bridge":
            result[radio] = list(bridge)
        else:   # tunnel (padrão) ou enable (versões antigas)
            result[radio] = list(tunnel)
    return result


def broadcast_map(fgt):
    """{nome do SSID: [ids dos APs que o transmitem]} e a lista de APs."""
    _, tunnel, bridge = _vap_lists(fgt)
    profiles = {p["name"]: p for p in fgt.get(PROFILE)}
    aps, where = [], {}
    for wtp in fgt.get(WTP):
        wid = wtp.get("wtp-id")
        aps.append({"id": wid, "name": wtp.get("name") or wid})
        for names in _effective(wtp, profiles, tunnel, bridge).values():
            for n in names:
                where.setdefault(n, set()).add(wid)
    return aps, {k: sorted(v) for k, v in where.items()}


def set_aps(fgt, vap, selected):
    """Faz o SSID 'vap' aparecer só nos APs 'selected', sem tirar o SSID principal de nenhum AP."""
    _, tunnel, bridge = _vap_lists(fgt)
    profiles = {p["name"]: p for p in fgt.get(PROFILE)}
    main = main_vap_name()
    for wtp in fgt.get(WTP):
        wid = wtp["wtp-id"]
        before = _effective(wtp, profiles, tunnel, bridge)
        want = wid in selected
        changes = {}
        for radio, names in before.items():
            if (vap in names) == want:
                continue
            new = [n for n in names if n != vap] + ([vap] if want else [])
            changes[radio] = {"override-vaps": "enable", "vap-all": "manual", "vaps": [{"name": n} for n in new]}
        if not changes:
            continue
        fgt.update(WTP, wid, changes)
        # Segurança: o SSID principal não pode sumir de nenhum AP
        after = _effective(fgt.get_one(WTP, wid) or {}, profiles, tunnel, bridge)
        lost = [r for r, names in before.items() if main in names and main not in after.get(r, [])]
        if lost:
            label = wtp.get("name") or wid
            original = {r: {"override-vaps": (wtp.get(r) or {}).get("override-vaps", "disable"),
                            "vap-all": (wtp.get(r) or {}).get("vap-all", "tunnel"),
                            "vaps": (wtp.get(r) or {}).get("vaps", [])} for r in changes}
            fgt.update(WTP, wid, original)
            restored = _effective(fgt.get_one(WTP, wid) or {}, profiles, tunnel, bridge)
            if any(main not in restored.get(r, []) for r in lost):
                raise SSIDError(f"ATENÇÃO: o SSID principal saiu do AP {label} e não consegui recolocar. "
                                "Confira esse AP no FortiGate (WiFi & Switch Controller > Managed FortiAPs).")
            raise SSIDError(f"A alteração no AP {label} tiraria o SSID principal do ar; foi desfeita.")


# ------------------------------------------------------------------ agendamento

def _parse_input(value, label):
    try:
        return datetime.strptime(str(value or ""), INPUT_FMT)
    except ValueError:
        raise SSIDError(f"Informe {label}.")


def _schedule_for(fgt, vap, data):
    """Devolve o valor do campo 'schedule' do SSID para o modo pedido (cria o agendamento se precisar)."""
    mode = data.get("mode")
    if mode == "on":
        return ALWAYS
    if mode == "off":
        if not fgt.get_one(ONETIME, OFF_SCHEDULE):
            fgt.create(ONETIME, {"name": OFF_SCHEDULE, "start": "00:00 2001/01/01", "end": "00:01 2001/01/01"})
        return OFF_SCHEDULE
    if mode == "window":
        start = _parse_input(data.get("start"), "o início")
        end = _parse_input(data.get("end"), "o fim")
        if end <= start:
            raise SSIDError("O fim precisa ser depois do início.")
        if end <= datetime.now():
            raise SSIDError("O fim precisa ser no futuro.")
        name = PREFIX + vap[len(PREFIX):]   # agendamento com o mesmo nome do SSID
        body = {"start": start.strftime(SCHED_FMT), "end": end.strftime(SCHED_FMT)}
        if fgt.get_one(ONETIME, name):
            fgt.update(ONETIME, name, body)
        else:
            fgt.create(ONETIME, {"name": name, **body})
        return name
    raise SSIDError("Escolha quando o SSID fica no ar.")


def _describe_schedule(vap, schedules, now):
    sched = _first(vap.get("schedule")) or ALWAYS
    if sched == ALWAYS:
        return {"mode": "on", "status": "no ar", "start": None, "end": None}
    if sched == OFF_SCHEDULE:
        return {"mode": "off", "status": "desligado", "start": None, "end": None}
    one = schedules.get(sched)
    if one:
        try:
            start = datetime.strptime(one["start"], SCHED_FMT)
            end = datetime.strptime(one["end"], SCHED_FMT)
        except (KeyError, ValueError):
            return {"mode": "custom", "status": f"agenda '{sched}'", "start": None, "end": None}
        status = "agendado" if now < start else "no ar" if now < end else "encerrado"
        return {"mode": "window", "status": status,
                "start": start.strftime(INPUT_FMT), "end": end.strftime(INPUT_FMT)}
    return {"mode": "custom", "status": f"agenda '{sched}'", "start": None, "end": None}


# ------------------------------------------------------------------ operações

def list_ssids(fgt):
    ready, reason, _ = readiness(fgt)
    aps, where = broadcast_map(fgt)
    schedules = {s["name"]: s for s in fgt.get(ONETIME)}
    main, now = main_vap_name(), datetime.now()
    interfaces = {i.get("name"): i for i in fgt.get(INTERFACE)}
    ssids = []
    for v in fgt.get(VAP):
        ssids.append({
            "name": v["name"],
            "ssid": str(v.get("ssid") or v["name"]),
            "main": v["name"] == main,
            "managed": v["name"].startswith(PREFIX),
            "access": access_of(v),
            "network": _network_of(interfaces, v["name"]),
            "aps": where.get(v["name"], []),
            **_describe_schedule(v, schedules, now),
        })
    ssids.sort(key=lambda s: (not s["main"], not s["managed"], s["ssid"].lower()))
    return {"ready": ready, "reason": reason, "main": main, "main_info": main_info(fgt),
            "can_choose_main": not os.getenv("FGT_MAIN_SSID", "").strip(), "aps": aps, "ssids": ssids}


def _valid_ssid_text(fgt, text, ignore=None):
    text = str(text or "").strip()
    if not 1 <= len(text) <= 32:
        raise SSIDError("O nome do SSID deve ter de 1 a 32 caracteres.")
    for v in fgt.get(VAP):
        if v["name"] != ignore and (v.get("ssid") or "").lower() == text.lower():
            raise SSIDError(f"Já existe um SSID chamado '{text}'.")
    return text


def _valid_aps(fgt, aps):
    known = {w["wtp-id"] for w in fgt.get(WTP)}
    selected = [a for a in aps or [] if a in known]
    if not selected:
        raise SSIDError("Escolha pelo menos um FortiAP.")
    return selected


def create_ssid(fgt, data):
    ready, reason, switch = readiness(fgt)
    if not ready:
        raise SSIDError(reason)
    text = _valid_ssid_text(fgt, data.get("ssid"))
    selected = _valid_aps(fgt, data.get("aps"))

    existing = {v["name"] for v in fgt.get(VAP)}
    slug = re.sub(r"[^a-z0-9]", "", text.lower())[:12] or "ssid"
    name, n = PREFIX + slug, 2
    while name in existing:
        suffix = str(n)
        name, n = PREFIX + slug[:12 - len(suffix)] + suffix, n + 1

    main = fgt.get_one(VAP, main_vap_name())
    undo = []
    try:
        schedule = _schedule_for(fgt, name, data)
        if schedule.startswith(PREFIX) and schedule != OFF_SCHEDULE:
            undo.append(lambda: fgt.remove(ONETIME, schedule))
        body = {k: main[k] for k in NETWORK_FIELDS if k in main}
        body.update(_security_body(main, data))
        body.update({"name": name, "ssid": text, "broadcast-ssid": "enable",
                     "schedule": _schedule_value(fgt, schedule)})
        fgt.create(VAP, body)
        undo.append(lambda: fgt.remove(VAP, name))
        mode = main_info(fgt)["mode"]
        if mode == "own":
            _create_own_network(fgt, name, data.get("security", "same"), undo)
        if switch:
            sw = fgt.get_one(SWITCH, switch)
            members = _names(sw.get("member"), "interface-name")
            fgt.update(SWITCH, switch, {"member": [{"interface-name": m} for m in members + [name]]})
            undo.append(lambda: fgt.update(SWITCH, switch, {"member": [{"interface-name": m} for m in members]}))
        set_aps(fgt, name, selected)
    except (SSIDError, FortiGateError):
        for step in reversed(undo):
            try:
                step()
            except FortiGateError:
                pass
        raise
    return name


def _managed(fgt, name):
    if not name.startswith(PREFIX):
        raise SSIDError("Só é possível alterar SSIDs criados pelo painel.")
    vap = fgt.get_one(VAP, name)
    if not vap:
        raise SSIDError("SSID não encontrado.")
    return vap


def update_ssid(fgt, name, data):
    vap = _managed(fgt, name)
    body = {}
    if "ssid" in data:
        body["ssid"] = _valid_ssid_text(fgt, data["ssid"], ignore=name)
    if "security" in data:
        body.update(_security_body(fgt.get_one(VAP, main_vap_name()) or {}, data, current=vap))
        ours = next((p for p in fgt.get(POLICY) if _names(p.get("srcintf")) == [name]), None)
        if ours:
            template = _internet_policy(fgt, main_vap_name()) or {}
            same = data.get("security") == "same"
            fgt.update(POLICY, ours["policyid"], {"groups": template.get("groups", []) if same else [],
                                                  "users": template.get("users", []) if same else []})
    if "mode" in data:
        old = _first(vap.get("schedule"))
        new_schedule = _schedule_for(fgt, name, data)
        body["schedule"] = _schedule_value(fgt, new_schedule)
    if body:
        fgt.update(VAP, name, body)
        if "schedule" in body and old.startswith(PREFIX) and old not in (OFF_SCHEDULE, new_schedule):
            fgt.remove(ONETIME, old)
    if "aps" in data:
        set_aps(fgt, name, _valid_aps(fgt, data["aps"]))


def delete_ssid(fgt, name):
    vap = _managed(fgt, name)
    set_aps(fgt, name, [])   # tira de todos os APs antes de apagar
    _remove_own_network(fgt, name)
    sw = _switch_of(fgt, name)
    if sw:
        members = [m for m in _names(sw.get("member"), "interface-name") if m != name]
        fgt.update(SWITCH, sw["name"], {"member": [{"interface-name": m} for m in members]})
    fgt.remove(VAP, name)
    sched = _first(vap.get("schedule"))
    if sched.startswith(PREFIX) and sched != OFF_SCHEDULE:
        fgt.remove(ONETIME, sched)
