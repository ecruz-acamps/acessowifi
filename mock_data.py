"""Dados fictícios no formato da API do FortiOS, para testar sem o FortiGate (DEMO_MODE=1)."""
import copy
import random
import time
from datetime import datetime, timedelta

from fortigate import FortiGateError


def _fgt_date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y/%m/%d 23:59:00")


class MockFortiGate:
    def __init__(self):
        self._groups = [
            {"name": "WiFi-Cadastrados", "group-type": "guest", "member": [],
             "guest": [
                 {"id": 1, "user-id": "joao.pereira", "name": "João Pereira", "email": "joao@gmail.com",
                  "mobile-phone": "+5511977776666", "company": "Equipe Cozinha",
                  "expiration": _fgt_date(20), "sponsor": "admin"},
                 {"id": 2, "user-id": "marta.gomes", "name": "Marta Gomes", "email": "",
                  "mobile-phone": "", "company": "Participante", "expiration": _fgt_date(0), "sponsor": "admin"},
                 {"id": 3, "user-id": "pedro.santos", "name": "Pedro Santos", "email": "",
                  "mobile-phone": "", "company": "Participante", "expiration": _fgt_date(-3), "sponsor": "admin"},
             ]},
            {"name": "WiFi-Google", "group-type": "firewall",
             "member": [{"name": "Google-Workspace"}], "guest": []},
        ]
        self._next_id = 4
        self._addresses = {
            "WiFi-MAC-placeholder": {"name": "WiFi-MAC-placeholder", "type": "mac",
                                     "macaddr": [{"macaddr": "02:00:00:00:00:00"}], "comment": ""},
            "acessowifi-8cfabb112233": {"name": "acessowifi-8cfabb112233", "type": "mac",
                                        "macaddr": [{"macaddr": "8c:fa:bb:11:22:33"}],
                                        "comment": f"acessowifi;user=secretaria@acampamento.org.br;exp={_fgt_date(60)};host=iPad-Secretaria"},
        }
        self._addrgrp = {"WiFi-MAC-Liberados": {"name": "WiFi-MAC-Liberados", "member": [
            {"name": "WiFi-MAC-placeholder"}, {"name": "acessowifi-8cfabb112233"}]}}

    # --- Configuração ---
    def local_users(self):
        return [{"name": "admin-rede", "status": "enable", "type": "password",
                 "email-to": "", "sms-phone": "", "two-factor": "disable", "passwd": "ENC xxxxx"}]

    def user_groups(self):
        return copy.deepcopy(self._groups)

    def saml_servers(self):
        return [{"name": "Google-Workspace", "idp-entity-id": "https://accounts.google.com/o/saml2?idpid=XXXX"}]

    def _group(self, name):
        for g in self._groups:
            if g["name"] == name:
                return g
        raise FortiGateError(f"FortiGate respondeu 404: grupo {name} não existe")

    def add_guest(self, group, guest):
        g = self._group(group)
        if any(x["user-id"] == guest["user-id"] for x in g["guest"]):
            raise FortiGateError("FortiGate respondeu 500: entrada duplicada")
        g["guest"].append({"id": self._next_id, **{k: v for k, v in guest.items() if k != "password"}})
        self._next_id += 1
        return {"status": "success", "mkey": self._next_id - 1}

    def update_guest(self, group, guest_id, fields):
        for x in self._group(group)["guest"]:
            if x["id"] == guest_id:
                x.update({k: v for k, v in fields.items() if k != "password"})
                return {"status": "success"}
        raise FortiGateError("FortiGate respondeu 404: usuário não encontrado")

    def delete_guest(self, group, guest_id):
        g = self._group(group)
        g["guest"] = [x for x in g["guest"] if x["id"] != guest_id]
        return {"status": "success"}

    # --- Endereços MAC ---
    def addresses(self, prefix):
        return [copy.deepcopy(a) for n, a in self._addresses.items() if prefix in n]

    def address_group(self, group):
        if group not in self._addrgrp:
            raise FortiGateError(f"Grupo de endereços '{group}' não existe no FortiGate.")
        return copy.deepcopy(self._addrgrp[group])

    def create_address(self, address):
        if address["name"] in self._addresses:
            raise FortiGateError("FortiGate respondeu 500: entrada duplicada")
        self._addresses[address["name"]] = copy.deepcopy(address)
        return {"status": "success"}

    def delete_address(self, name):
        if any(m["name"] == name for g in self._addrgrp.values() for m in g["member"]):
            raise FortiGateError("FortiGate respondeu 500: endereço em uso")
        self._addresses.pop(name, None)
        return {"status": "success"}

    def add_group_member(self, group, name):
        self._addrgrp[group]["member"].append({"name": name})
        return {"status": "success"}

    def remove_group_member(self, group, name):
        g = self._addrgrp[group]
        g["member"] = [m for m in g["member"] if m["name"] != name]
        return {"status": "success"}

    def vaps(self):
        return self.get("cmdb/wireless-controller/vap")

    # --- Tabelas genéricas do cmdb (SSIDs, FortiAPs, agendamentos, software switch) ---
    KEYS = {"cmdb/wireless-controller/vap": "name", "cmdb/wireless-controller/wtp": "wtp-id",
            "cmdb/wireless-controller/wtp-profile": "name", "cmdb/firewall.schedule/onetime": "name",
            "cmdb/system/switch-interface": "name"}

    def _init_tables(self):
        radios = {"radio-1": {"mode": "ap", "vap-all": "tunnel", "vaps": []},
                  "radio-2": {"mode": "ap", "vap-all": "tunnel", "vaps": []},
                  "radio-3": {"mode": "monitor"}}
        self._tables = {
            "cmdb/wireless-controller/vap": [
                {"name": "Acampamento", "ssid": "Acampamento", "security": "open", "captive-portal": "enable",
                 "portal-type": "auth", "selected-usergroups": [{"name": "WiFi-Google"}, {"name": "WiFi-Cadastrados"}],
                 "security-exempt-list": "WiFi-Liberados", "local-bridging": "disable", "schedule": "always"},
                {"name": "Equipe", "ssid": "Equipe-Staff", "security": "wpa2-only-personal",
                 "local-bridging": "disable", "schedule": "always"},
            ],
            "cmdb/wireless-controller/wtp-profile": [{"name": "FAP231F-default", **copy.deepcopy(radios)}],
            "cmdb/wireless-controller/wtp": [
                {"wtp-id": "FP231FTF00000001", "name": "AP-Refeitorio", "wtp-profile": "FAP231F-default",
                 "radio-1": {"override-vaps": "disable"}, "radio-2": {"override-vaps": "disable"}},
                {"wtp-id": "FP231FTF00000002", "name": "AP-Secretaria", "wtp-profile": "FAP231F-default",
                 "radio-1": {"override-vaps": "disable"}, "radio-2": {"override-vaps": "disable"}},
                {"wtp-id": "FP231FTF00000003", "name": "AP-Alojamento", "wtp-profile": "FAP231F-default",
                 "radio-1": {"override-vaps": "disable"}, "radio-2": {"override-vaps": "disable"}},
            ],
            "cmdb/firewall.schedule/onetime": [],
            "cmdb/system/switch-interface": [
                {"name": "wifi-sw", "type": "switch", "member": [{"interface-name": "Acampamento"}]}],
        }

    def _table(self, path):
        if not hasattr(self, "_tables"):
            self._init_tables()
        base = next((t for t in self.KEYS if path == t or path.startswith(t + "/")), None)
        if base is None:
            raise FortiGateError(f"FortiGate respondeu 404 em GET {path}", status=404)
        mkey = path[len(base) + 1:] or None
        return self._tables[base], self.KEYS[base], mkey

    def get(self, path, **params):
        rows, key, mkey = self._table(path)
        if mkey is None:
            return copy.deepcopy(rows)
        found = [copy.deepcopy(r) for r in rows if str(r.get(key)) == mkey]
        if not found:
            raise FortiGateError(f"FortiGate respondeu 404 em GET {path}", status=404)
        return found

    def get_one(self, path, mkey):
        try:
            return self.get(f"{path}/{mkey}")[0]
        except FortiGateError:
            return None

    def create(self, path, data):
        rows, key, _ = self._table(path)
        if any(r.get(key) == data.get(key) for r in rows):
            raise FortiGateError(f"FortiGate respondeu 500 em POST {path}: entrada duplicada", status=500)
        if path.endswith("/vap") and len(data["name"]) > 15:
            raise FortiGateError("FortiGate respondeu 500: nome do SSID com mais de 15 caracteres", status=500)
        rows.append(copy.deepcopy(data))
        return {"status": "success"}

    def update(self, path, mkey, data):
        rows, key, _ = self._table(path)
        for r in rows:
            if str(r.get(key)) == str(mkey):
                for k, v in copy.deepcopy(data).items():
                    if isinstance(v, dict) and isinstance(r.get(k), dict):
                        r[k].update(v)
                    else:
                        r[k] = v
                return {"status": "success"}
        raise FortiGateError(f"FortiGate respondeu 404 em PUT {path}/{mkey}", status=404)

    def remove(self, path, mkey):
        rows, key, _ = self._table(path)
        rows[:] = [r for r in rows if str(r.get(key)) != str(mkey)]
        return {"status": "success"}

    def policies(self):
        return [{"policyid": 3, "name": "wifi-internet", "srcintf": [{"name": "Acampamento"}],
                 "dstintf": [{"name": "wan"}], "groups": [], "users": []}]

    # --- Estado em tempo real ---
    def wifi_clients(self):
        now = int(time.time())
        # No captive portal o FortiAP não informa o usuário; o painel cruza pelo IP
        base = [
            ("10.10.30.21", "a4:83:e7:11:22:33", "Galaxy-A54", "Android", "AP-Refeitorio", "802.11ax-5G", 36),
            ("10.10.30.34", "f2:18:98:aa:bb:cc", "iPhone", "iOS", "AP-Refeitorio", "802.11ac", 149),
            ("10.10.30.50", "3c:22:fb:44:55:66", "NOTE-COORD", "Windows", "AP-Secretaria", "802.11ax-5G", 44),
            ("10.10.30.77", "dc:a6:32:01:02:03", "Redmi-Note", "Android", "AP-Alojamento", "802.11n-2G", 6),
            ("10.10.30.90", "8c:fa:bb:11:22:33", "iPad-Secretaria", "iOS", "AP-Secretaria", "802.11ax-5G", 44),
        ]
        return [
            {"user": "", "ip": ip, "mac": mac, "hostname": host, "os": os_, "ssid": "Acampamento",
             "wtp_name": ap, "radio_type": radio, "channel": ch,
             "signal": random.randint(-75, -40), "snr": random.randint(15, 45),
             "data_rate_bps": random.choice([72, 144, 433, 866]) * 1_000_000, "vlan_id": 30,
             "association_time": now - random.randint(60, 4 * 3600), "idle_time": random.randint(0, 120)}
            for ip, mac, host, os_, ap, radio, ch in base
        ]

    def firewall_auth_users(self):
        return [
            {"username": "joao.pereira", "ipaddr": "10.10.30.21", "usergroup": [{"name": "WiFi-Cadastrados"}],
             "duration": 1834, "expiry": 26966, "method": "Captive portal", "traffic_vol_bytes": 52_340_112},
            {"username": "coordenacao@acampamento.org.br", "ipaddr": "10.10.30.50",
             "usergroup": [{"name": "WiFi-Google"}], "duration": 3600, "expiry": 25000,
             "method": "Captive portal", "traffic_vol_bytes": 210_334_001},
            {"username": "visitante.antigo", "ipaddr": "10.10.30.34", "usergroup": [{"name": "WiFi-Antigo"}],
             "duration": 120, "expiry": 28000, "method": "Captive portal", "traffic_vol_bytes": 1_200_000},
        ]

    def managed_aps(self):
        return [
            {"name": "AP-Refeitorio", "wtp_id": "FP231FTF00000001", "status": "connected",
             "local_ipv4_addr": "10.10.0.11", "os_version": "FP231F-v7.2"},
            {"name": "AP-Secretaria", "wtp_id": "FP231FTF00000002", "status": "connected",
             "local_ipv4_addr": "10.10.0.12", "os_version": "FP231F-v7.2"},
            {"name": "AP-Alojamento", "wtp_id": "FP231FTF00000003", "status": "connected",
             "local_ipv4_addr": "10.10.0.13", "os_version": "FP231F-v7.2"},
        ]
