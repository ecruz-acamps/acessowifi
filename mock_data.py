"""Dados fictícios no formato da API do FortiOS, para testar sem o FortiGate (DEMO_MODE=1)."""
import random
import time


class MockFortiGate:
    def local_users(self):
        return [
            {"name": "ana.souza", "status": "enable", "type": "password",
             "email-to": "ana@empresa.com.br", "sms-phone": "+5511988887777", "two-factor": "disable",
             "passwd": "ENC xxxxx"},
            {"name": "bruno.lima", "status": "enable", "type": "password",
             "email-to": "bruno@empresa.com.br", "sms-phone": "", "two-factor": "email"},
            {"name": "carla.reis", "status": "disable", "type": "password",
             "email-to": "carla@empresa.com.br", "sms-phone": "", "two-factor": "disable"},
            {"name": "diego.alves", "status": "enable", "type": "radius",
             "email-to": "", "sms-phone": "", "two-factor": "disable"},
            {"name": "recepcao", "status": "enable", "type": "password",
             "email-to": "", "sms-phone": "", "two-factor": "disable"},
        ]

    def user_groups(self):
        return [
            {"name": "WiFi-Funcionarios", "group-type": "firewall",
             "member": [{"name": "ana.souza"}, {"name": "bruno.lima"}, {"name": "carla.reis"}, {"name": "diego.alves"}],
             "guest": []},
            {"name": "WiFi-Recepcao", "group-type": "firewall",
             "member": [{"name": "recepcao"}], "guest": []},
            {"name": "WiFi-Visitantes", "group-type": "guest", "member": [],
             "guest": [
                 {"user-id": "visitante01", "name": "João Pereira", "email": "joao@gmail.com",
                  "mobile-phone": "+5511977776666", "company": "Fornecedor X",
                  "expiration": "2026/11/01 18:00:00", "sponsor": "ana.souza"},
                 {"user-id": "visitante02", "name": "Marta Gomes", "email": "",
                  "mobile-phone": "", "company": "", "expiration": "2026/10/30 18:00:00", "sponsor": ""},
             ]},
        ]

    def wifi_clients(self):
        now = int(time.time())
        base = [
            ("ana.souza", "10.10.10.21", "a4:83:e7:11:22:33", "ANA-NOTEBOOK", "Windows", "Empresa-Corp", "AP-Sala", "802.11ax-5G", 36),
            ("bruno.lima", "10.10.10.34", "f0:18:98:aa:bb:cc", "iPhone-Bruno", "iOS", "Empresa-Corp", "AP-Recepcao", "802.11ac", 149),
            ("", "10.10.30.50", "3c:22:fb:44:55:66", "Galaxy-S23", "Android", "Empresa-Visitantes", "AP-Recepcao", "802.11n-2G", 6),
            ("", "10.10.10.77", "dc:a6:32:01:02:03", "raspberrypi", "Linux", "Empresa-Corp", "FWF40F-Interno", "802.11n-2G", 11),
        ]
        return [
            {"user": u, "ip": ip, "mac": mac, "hostname": host, "os": os_, "ssid": ssid,
             "wtp_name": ap, "radio_type": radio, "channel": ch,
             "signal": random.randint(-75, -40), "snr": random.randint(15, 45),
             "data_rate_bps": random.choice([72, 144, 433, 866, 1201]) * 1_000_000,
             "vlan_id": 30 if "Visit" in ssid else 10,
             "association_time": now - random.randint(60, 4 * 3600),
             "idle_time": random.randint(0, 120), "health": {}}
            for u, ip, mac, host, os_, ssid, ap, radio, ch in base
        ]

    def firewall_auth_users(self):
        # Usuário de captive portal: aparece aqui pelo IP, não no cliente Wi-Fi
        return [
            {"username": "visitante01", "ipaddr": "10.10.30.50", "usergroup": [{"name": "WiFi-Visitantes"}],
             "duration": 1834, "expiry": 26966, "method": "Captive portal",
             "traffic_vol_bytes": 52_340_112},
        ]

    def managed_aps(self):
        return [
            {"name": "FWF40F-Interno", "wtp_id": "FWF40F-WIFI0", "status": "connected", "local_ipv4_addr": "",
             "os_version": "", "clients": 1, "is_local": True},
            {"name": "AP-Sala", "wtp_id": "FP231FTF00000001", "status": "connected", "local_ipv4_addr": "10.10.0.11",
             "os_version": "FP231F-v7.4", "clients": 1},
            {"name": "AP-Recepcao", "wtp_id": "FP231FTF00000002", "status": "connected", "local_ipv4_addr": "10.10.0.12",
             "os_version": "FP231F-v7.4", "clients": 2},
        ]
