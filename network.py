"""Aba Rede: todos os aparelhos que o FortiGate enxerga (Wi-Fi e cabo) e o bloqueio por MAC.

Fontes, juntadas pelo MAC: inventário de aparelhos (monitor/user/device/query), DHCP, tabela ARP
e clientes Wi-Fi dos FortiAPs. Se uma fonte falhar (ex.: falta de permissão), as outras continuam.

Bloqueio: o MAC vira um endereço "acessowifi-blk-<mac>" no grupo FGT_BLOCK_GROUP, e uma política
"negar" (BLOCK_POLICY) no topo da lista barra esse grupo, de qualquer interface para qualquer
interface. O painel cria o grupo e a política no primeiro bloqueio e só mexe nesses objetos.
"""
import json
import os
import re
import time
from pathlib import Path

from fortigate import FortiGateError

BLOCK_GROUP = os.getenv("FGT_BLOCK_GROUP", "WiFi-MAC-Bloqueados")
BLOCK_PREFIX = "acessowifi-blk-"
BLOCK_PLACEHOLDER = "acessowifi-blk-vazio"   # o FortiOS não aceita grupo de endereços vazio
BLOCK_POLICY = "acessowifi-bloqueados"
POLICY = "cmdb/firewall/policy"
NAMES_FILE = Path(os.getenv("ACESSOWIFI_CTL", "/var/lib/acessowifi-ctl")) / "device-names.json"


class NetworkError(Exception):
    pass


def normalize_mac(value):
    digits = re.sub(r"[^0-9a-f]", "", str(value or "").lower())
    if len(digits) != 12:
        raise NetworkError("Endereço MAC inválido.")
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


# ------------------------------------------------------------------ nomes dados pelo administrador

def _load_names():
    try:
        with open(NAMES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_name(mac, name):
    mac = normalize_mac(mac)
    name = str(name or "").strip()[:60]
    names = _load_names()
    if name:
        names[mac] = name
    else:
        names.pop(mac, None)
    try:
        tmp = NAMES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(names, ensure_ascii=False, indent=1))
        tmp.replace(NAMES_FILE)
    except OSError:
        raise NetworkError("Não consegui salvar o nome no servidor. Rode ./deploy/install.sh.")
    return name


# ------------------------------------------------------------------ lista de aparelhos

def _source(label, fn, warnings):
    try:
        return fn()
    except FortiGateError as e:
        if e.status in (401, 403):
            warnings.append(f"{label}: o token não tem permissão para ler isto. No servidor, rode "
                            "\"acessowifi permissoes\".")
        else:
            warnings.append(f"{label}: {e}")
        return []


def blocked_macs(fgt):
    """{mac: endereço} dos aparelhos bloqueados pelo painel."""
    try:
        members = {m.get("name") for m in fgt.address_group(BLOCK_GROUP).get("member") or []}
    except FortiGateError:
        return {}
    out = {}
    for a in fgt.addresses(BLOCK_PREFIX):
        if a.get("name") in members and a.get("name") != BLOCK_PLACEHOLDER:
            mac = a.get("macaddr")
            mac = (mac[0].get("macaddr") if isinstance(mac, list) and mac else mac) or ""
            if mac:
                out[mac.lower()] = a
    return out


def list_devices(fgt, wifi_clients=None, auth_users=None):
    warnings = []
    inventory = _source("Inventário de aparelhos", fgt.device_inventory, warnings)
    leases = _source("DHCP", fgt.dhcp_leases, warnings)
    arp = _source("Tabela ARP", fgt.arp_table, warnings)
    wifi = wifi_clients if wifi_clients is not None else _source("Clientes Wi-Fi", fgt.wifi_clients, warnings)
    auth = {a.get("ipaddr"): a.get("username") for a in (auth_users or []) if a.get("ipaddr")}
    blocked = blocked_macs(fgt)
    names = _load_names()
    now = time.time()

    devices = {}

    def entry(mac):
        mac = (mac or "").lower()
        if not mac or mac == "00:00:00:00:00:00":
            return None
        return devices.setdefault(mac, {"mac": mac, "ip": "", "hostname": "", "vendor": "", "os": "", "type": "",
                                        "interface": "", "ssid": "", "ap": "", "online": False, "last_seen": None,
                                        "sources": []})

    for d in inventory:
        e = entry(d.get("mac"))
        if not e:
            continue
        e.update(ip=d.get("ipv4_address") or e["ip"], hostname=d.get("hostname") or e["hostname"],
                 vendor=d.get("hardware_vendor") or "", os=d.get("os_name") or "", type=d.get("hardware_type") or "",
                 interface=d.get("detected_interface") or e["interface"])
        e["online"] = e["online"] or bool(d.get("is_online"))
        if d.get("last_seen"):
            e["last_seen"] = int(d["last_seen"])
        e["sources"].append("inventário")
    for d in leases:
        e = entry(d.get("mac"))
        if not e:
            continue
        e["ip"] = e["ip"] or d.get("ip", "")
        e["hostname"] = e["hostname"] or d.get("hostname", "")
        e["interface"] = e["interface"] or d.get("interface", "")
        e["sources"].append("DHCP")
    for d in arp:
        e = entry(d.get("mac"))
        if not e:
            continue
        e["ip"] = e["ip"] or d.get("ip", "")
        e["interface"] = e["interface"] or d.get("interface", "")
        e["online"] = True   # na ARP = falou com o FortiGate há pouco
        e["sources"].append("ARP")
    for c in wifi:
        e = entry(c.get("mac"))
        if not e:
            continue
        e.update(ssid=c.get("ssid", ""), ap=c.get("wtp_name") or c.get("wtp_id", ""), online=True)
        e["ip"] = e["ip"] or c.get("ip", "")
        e["hostname"] = e["hostname"] or c.get("hostname") or c.get("host", "")
        e["os"] = e["os"] or c.get("os", "")
        e["sources"].append("Wi-Fi")
    for mac, addr in blocked.items():   # bloqueado e fora da rede agora: aparece mesmo assim
        e = entry(mac)
        if e and not e["sources"]:
            e["hostname"] = ""
            e["sources"].append("bloqueio")

    for e in devices.values():
        e["name"] = names.get(e["mac"], "")
        e["blocked"] = e["mac"] in blocked
        e["user"] = auth.get(e["ip"], "")
        e["connection"] = f"Wi-Fi ({e['ssid']})" if e["ssid"] else ("Cabo ou outra rede" if e["interface"] else "")
        if e["online"] and not e["last_seen"]:
            e["last_seen"] = int(now)
    result = sorted(devices.values(), key=lambda d: (not d["online"], not d["blocked"],
                                                     (d["name"] or d["hostname"] or d["mac"]).lower()))
    return {"devices": result, "warnings": warnings}


# ------------------------------------------------------------------ bloqueio

def _ensure_block_setup(fgt):
    """Grupo de bloqueados e política "negar" no topo. Criados no primeiro bloqueio."""
    try:
        fgt.address_group(BLOCK_GROUP)
    except FortiGateError:
        if not fgt.addresses(BLOCK_PLACEHOLDER):
            fgt.create_address({"name": BLOCK_PLACEHOLDER, "type": "mac", "macaddr": [{"macaddr": "02:00:00:00:0b:0b"}],
                                "comment": "Nao remover: mantem o grupo de bloqueados nao vazio"})
        fgt.create_address_group(BLOCK_GROUP, [BLOCK_PLACEHOLDER], "Aparelhos bloqueados pelo painel Acesso Wi-Fi")

    policies = fgt.get(POLICY)
    ours = next((p for p in policies if p.get("name") == BLOCK_POLICY), None)
    if not ours:
        r = fgt.create(POLICY, {
            "name": BLOCK_POLICY, "srcintf": [{"name": "any"}], "dstintf": [{"name": "any"}],
            "srcaddr": [{"name": BLOCK_GROUP}], "dstaddr": [{"name": "all"}], "action": "deny",
            "schedule": "always", "service": [{"name": "ALL"}], "logtraffic": "all",
            "comments": "Criada pelo painel Acesso Wi-Fi: aparelhos bloqueados na aba Rede"})
        pid = r.get("mkey") or next(p["policyid"] for p in fgt.get(POLICY) if p.get("name") == BLOCK_POLICY)
        policies = fgt.get(POLICY)
        first = next((p["policyid"] for p in policies if p["policyid"] != pid), None)
        if first is not None:
            fgt.move_policy(pid, before=first)   # no topo: vale antes de qualquer "aceitar"
    elif policies and policies[0].get("name") != BLOCK_POLICY:
        fgt.move_policy(ours["policyid"], before=policies[0]["policyid"])


def block(fgt, mac, name="", user="painel", protected_ips=()):
    mac = normalize_mac(mac)
    if mac in blocked_macs(fgt):
        raise NetworkError("Este aparelho já está bloqueado.")
    current = next((d for d in list_devices(fgt)["devices"] if d["mac"] == mac), None)
    if current and current["ip"] and current["ip"] in protected_ips:
        raise NetworkError("Este é o aparelho que você está usando (ou o servidor do painel): bloquear tiraria o "
                           "seu acesso ao painel.")
    _ensure_block_setup(fgt)
    address = BLOCK_PREFIX + mac.replace(":", "")
    label = re.sub(r"[^\w .-]", "", str(name or (current or {}).get("hostname") or ""))[:40]
    fgt.create_address({"name": address, "type": "mac", "macaddr": [{"macaddr": mac}],
                        "comment": f"acessowifi;bloqueado por {user};{label}"})
    try:
        fgt.add_group_member(BLOCK_GROUP, address)
    except FortiGateError:
        fgt.delete_address(address)
        raise
    return mac


def unblock(fgt, mac):
    mac = normalize_mac(mac)
    addr = blocked_macs(fgt).get(mac)
    if not addr:
        raise NetworkError("Este aparelho não está bloqueado pelo painel.")
    fgt.remove_group_member(BLOCK_GROUP, addr["name"])
    fgt.delete_address(addr["name"])
    return mac
