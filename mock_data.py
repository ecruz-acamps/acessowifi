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

    # --- Estado em tempo real ---
    def wifi_clients(self):
        now = int(time.time())
        # No captive portal o FortiAP não informa o usuário; o painel cruza pelo IP
        base = [
            ("10.10.30.21", "a4:83:e7:11:22:33", "Galaxy-A54", "Android", "AP-Refeitorio", "802.11ax-5G", 36),
            ("10.10.30.34", "f0:18:98:aa:bb:cc", "iPhone", "iOS", "AP-Refeitorio", "802.11ac", 149),
            ("10.10.30.50", "3c:22:fb:44:55:66", "NOTE-COORD", "Windows", "AP-Secretaria", "802.11ax-5G", 44),
            ("10.10.30.77", "dc:a6:32:01:02:03", "Redmi-Note", "Android", "AP-Alojamento", "802.11n-2G", 6),
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
