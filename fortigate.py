"""Cliente mínimo da API REST do FortiOS."""
import time
from urllib.parse import quote

import requests
import urllib3


class FortiGateError(Exception):
    pass


class FortiGate:
    def __init__(self, host, token, vdom="root", verify=True, timeout=10, cache_ttl=10):
        self.base = f"https://{host}/api/v2"
        self.vdom = vdom
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache = {}

        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        # verify pode ser True/False ou o caminho do certificado (CA) do FortiGate
        self.session.verify = verify
        if verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(self, method, path, params=None, json=None):
        try:
            r = self.session.request(
                method,
                f"{self.base}/{path}",
                params={"vdom": self.vdom, **(params or {})},
                json=json,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise FortiGateError(f"Falha ao conectar no FortiGate: {e}") from e

        if r.status_code in (401, 403):
            raise FortiGateError(
                f"FortiGate recusou o acesso ({r.status_code}). Verifique o token, "
                "o perfil do admin REST API e o 'trusted host'."
            )
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not r.ok or data.get("status") == "error":
            detail = data.get("cli_error") or data.get("error") or r.reason
            raise FortiGateError(f"FortiGate respondeu {r.status_code} em {method} {path}: {detail}")
        return data

    def get(self, path, **params):
        """GET em /api/v2/<path> e devolve a lista 'results'. Usa cache curto."""
        key = (path, tuple(sorted(params.items())))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1]
        results = self._request("GET", path, params=params).get("results", [])
        self._cache[key] = (time.monotonic(), results)
        return results

    def _write(self, method, path, json=None):
        data = self._request(method, path, json=json)
        self._cache.clear()
        return data

    # --- Configuração (cmdb) ---
    def local_users(self):
        return self.get("cmdb/user/local")

    def user_groups(self):
        return self.get("cmdb/user/group")

    def saml_servers(self):
        return self.get("cmdb/user/saml")

    # --- Usuários visitantes (guest) de um grupo ---
    def _guest_path(self, group, guest_id=None):
        path = f"cmdb/user/group/{quote(group, safe='')}/guest"
        return f"{path}/{guest_id}" if guest_id is not None else path

    def add_guest(self, group, guest):
        return self._write("POST", self._guest_path(group), json=guest)

    def update_guest(self, group, guest_id, fields):
        return self._write("PUT", self._guest_path(group, guest_id), json=fields)

    def delete_guest(self, group, guest_id):
        return self._write("DELETE", self._guest_path(group, guest_id))

    # --- Estado em tempo real (monitor) ---
    def wifi_clients(self):
        return self.get("monitor/wifi/client")

    def firewall_auth_users(self):
        return self.get("monitor/user/firewall")

    def managed_aps(self):
        return self.get("monitor/wifi/managed_ap")
