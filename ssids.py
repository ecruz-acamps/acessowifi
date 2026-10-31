"""SSIDs extras: criados a partir do SSID principal, com agendamento e escolha de FortiAPs.

Tudo fica no próprio FortiGate, que liga e desliga os SSIDs sozinho:
- o SSID novo copia a segurança do principal (captive portal, grupos, lista de exceção);
- mesma rede do principal: mesma VLAN (SSID em bridge) ou o mesmo software switch (SSID em túnel);
- agendamento: o campo "schedule" do SSID aponta para "always" (ligado), para um agendamento
  vencido (desligado) ou para um agendamento único do firewall com início e fim (período);
- FortiAPs: cada AP recebe, por rádio, a lista de SSIDs que deve transmitir (override-vaps).

O painel só altera SSIDs com o prefixo "aw-" (os que ele mesmo criou).
"""
import os
import re
from datetime import datetime

from fortigate import FortiGateError

VAP = "cmdb/wireless-controller/vap"
WTP = "cmdb/wireless-controller/wtp"
PROFILE = "cmdb/wireless-controller/wtp-profile"
ONETIME = "cmdb/firewall.schedule/onetime"
SWITCH = "cmdb/system/switch-interface"

PREFIX = "aw-"                 # nome interno dos SSIDs criados pelo painel (FortiOS: até 15 caracteres)
OFF_SCHEDULE = "aw-desligado"  # agendamento já vencido: o SSID fica fora do ar
ALWAYS = "always"
RADIOS = ("radio-1", "radio-2", "radio-3", "radio-4")
SCHED_FMT = "%H:%M %Y/%m/%d"   # formato do FortiOS para agendamento único
INPUT_FMT = "%Y-%m-%dT%H:%M"   # formato do <input type=datetime-local>

# Campos de segurança/rede copiados do SSID principal
COPY_FIELDS = (
    "security", "captive-portal", "portal-type", "auth", "selected-usergroups", "usergroup",
    "security-exempt-list", "security-redirect-url", "external-web", "external-logout",
    "portal-message-override-group", "local-authentication", "intra-vap-privacy",
    "local-bridging", "vlanid", "pmf",
)


class SSIDError(Exception):
    pass


def _names(items, key="name"):
    return [i.get(key) for i in items or [] if i.get(key)]


def main_vap_name():
    return os.getenv("FGT_MAIN_SSID", "").strip()


def _switch_of(fgt, vap):
    for sw in fgt.get(SWITCH):
        if vap in _names(sw.get("member"), "interface-name"):
            return sw
    return None


def readiness(fgt):
    """O SSID novo pode ficar na mesma rede do principal? Devolve (pronto, motivo, switch)."""
    main = main_vap_name()
    if not main:
        return False, ("O SSID principal não está configurado. Rode ./deploy/install.sh e responda sim "
                       "para refazer a configuração."), None
    vap = fgt.get_one(VAP, main)
    if not vap:
        return False, f"O SSID principal '{main}' não existe mais no FortiGate.", None
    if vap.get("local-bridging") == "enable":
        return True, "", None   # bridge: o SSID novo usa a mesma VLAN
    sw = _switch_of(fgt, main)
    if sw:
        return True, "", sw["name"]
    return False, (f"O SSID principal '{main}' é uma rede própria (modo túnel, fora de um software switch). "
                   "Para outro SSID usar a mesma rede, os dois precisam estar num software switch. "
                   "Veja docs/FORTIGATE.md, \"SSIDs extras\"."), None


# ------------------------------------------------------------------ FortiAPs

def _vap_lists(fgt):
    vaps = fgt.get(VAP)
    bridge = [v["name"] for v in vaps if v.get("local-bridging") == "enable"]
    tunnel = [v["name"] for v in vaps if v.get("local-bridging") != "enable"]
    return vaps, tunnel, bridge


def _effective(wtp, profiles, tunnel, bridge):
    """SSIDs que cada rádio do AP transmite hoje: {rádio: [nomes]}."""
    profile = profiles.get(wtp.get("wtp-profile")) or {}
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
    sched = (vap.get("schedule") or ALWAYS).strip().strip('"')
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
    ssids = []
    for v in fgt.get(VAP):
        ssids.append({
            "name": v["name"],
            "ssid": v.get("ssid") or v["name"],
            "main": v["name"] == main,
            "managed": v["name"].startswith(PREFIX),
            "aps": where.get(v["name"], []),
            **_describe_schedule(v, schedules, now),
        })
    ssids.sort(key=lambda s: (not s["main"], not s["managed"], s["ssid"].lower()))
    return {"ready": ready, "reason": reason, "main": main, "aps": aps, "ssids": ssids}


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
        body = {k: main[k] for k in COPY_FIELDS if k in main}
        body.update({"name": name, "ssid": text, "broadcast-ssid": "enable", "schedule": schedule})
        fgt.create(VAP, body)
        undo.append(lambda: fgt.remove(VAP, name))
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
    if "mode" in data:
        old = (vap.get("schedule") or "").strip('"')
        body["schedule"] = _schedule_for(fgt, name, data)
    if body:
        fgt.update(VAP, name, body)
        if "schedule" in body and old.startswith(PREFIX) and old not in (OFF_SCHEDULE, body["schedule"]):
            fgt.remove(ONETIME, old)
    if "aps" in data:
        set_aps(fgt, name, _valid_aps(fgt, data["aps"]))


def delete_ssid(fgt, name):
    vap = _managed(fgt, name)
    set_aps(fgt, name, [])   # tira de todos os APs antes de apagar
    sw = _switch_of(fgt, name)
    if sw:
        members = [m for m in _names(sw.get("member"), "interface-name") if m != name]
        fgt.update(SWITCH, sw["name"], {"member": [{"interface-name": m} for m in members]})
    fgt.remove(VAP, name)
    sched = (vap.get("schedule") or "").strip('"')
    if sched.startswith(PREFIX) and sched != OFF_SCHEDULE:
        fgt.remove(ONETIME, sched)
