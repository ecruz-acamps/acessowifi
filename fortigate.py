"""Cliente mínimo (somente leitura) da API REST do FortiOS."""
import time

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

    def get(self, path, **params):
        """GET em /api/v2/<path> e devolve a lista 'results'. Usa cache curto."""
        key = (path, tuple(sorted(params.items())))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1]

        try:
            r = self.session.get(
                f"{self.base}/{path}",
                params={"vdom": self.vdom, **params},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise FortiGateError(f"Falha ao conectar no FortiGate: {e}") from e

        if r.status_code in (401, 403):
            raise FortiGateError(
                f"FortiGate recusou o acesso ({r.status_code}). Verifique o token, "
                "o perfil do admin REST API e o 'trusted host'."
            )
        if not r.ok:
            raise FortiGateError(f"FortiGate respondeu {r.status_code} em {path}")

        results = r.json().get("results", [])
        self._cache[key] = (time.monotonic(), results)
        return results

    # --- Configuração (cmdb) ---
    def local_users(self):
        return self.get("cmdb/user/local")

    def user_groups(self):
        return self.get("cmdb/user/group")

    # --- Estado em tempo real (monitor) ---
    def wifi_clients(self):
        return self.get("monitor/wifi/client")

    def firewall_auth_users(self):
        return self.get("monitor/user/firewall")

    def managed_aps(self):
        return self.get("monitor/wifi/managed_ap")
