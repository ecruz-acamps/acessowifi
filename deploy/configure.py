#!/usr/bin/env python3
"""Configuração interativa do painel Acesso Wi-Fi (chamada pelo deploy/install.sh).

Pergunta tudo o que é necessário e, no modo automático, configura o FortiGate por SSH
com um admin informado na hora. O usuário e a senha desse admin NÃO são salvos: servem
só para criar os objetos e gerar o token da API, que é o que o painel usa.

  python deploy/configure.py --env /opt/acessowifi/.env
  python deploy/configure.py --env /tmp/teste.env --demo     # simula um FortiGate
"""
import argparse
import getpass
import os
import re
import secrets
import shlex
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fortigate import FortiGate, FortiGateError  # noqa: E402

DEFAULTS = {
    "guest_group": "WiFi-Cadastrados",
    "mac_group": "WiFi-MAC-Liberados",
    "placeholder": "WiFi-MAC-placeholder",
    "exempt_list": "WiFi-Liberados",
    "profile": "api-acessowifi",
    "api_user": "acessowifi",
    "mac_policy": "acessowifi-mac-liberados",
}
DOCS = "docs/FORTIGATE.md"


# ------------------------------------------------------------------ interface

def title(text):
    print(f"\n\033[1m== {text}\033[0m")


def info(text=""):
    print(f"   {text}")


def warn(text):
    print(f"   \033[33m! {text}\033[0m")


def fail(text):
    print(f"   \033[31mx {text}\033[0m")


def ok(text):
    print(f"   \033[32m✓ {text}\033[0m")


def ask(prompt, default="", validate=None):
    while True:
        answer = input(f"   {prompt}{f' [{default}]' if default else ''}: ").strip() or default
        error = validate(answer) if validate else (None if answer else "Resposta obrigatória.")
        if not error:
            return answer
        warn(error)


def ask_secret(prompt):
    while True:
        answer = getpass.getpass(f"   {prompt}: ")
        if answer:
            return answer


def yes_no(prompt, default=True):
    answer = input(f"   {prompt} [{'S/n' if default else 's/N'}]: ").strip().lower()
    return default if not answer else answer[0] in "sy"


def choose(prompt, options, default=1):
    for i, label in enumerate(options, 1):
        info(f"  {i}) {label}")
    return int(ask(prompt, str(default),
                   lambda a: None if a.isdigit() and 1 <= int(a) <= len(options) else "Escolha um número da lista.")) - 1


# ------------------------------------------------------------------ rede

def tcp_open(host, port, timeout=4):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_ip_towards(host):
    """IP deste servidor usado para falar com o FortiGate (é o que vai no trusted host)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def valid_ipv4(value):
    try:
        socket.inet_aton(value)
        return value.count(".") == 3
    except OSError:
        return False


# ------------------------------------------------------------------ CLI do FortiOS por SSH

class CLIError(Exception):
    pass


class FortiCLI:
    PROMPT = re.compile(r"\S+ (\([^)\n]*\) )?[#$] ?$")
    ERROR = re.compile(r"command parse error|value parse error|Command fail|entry not found|Unknown action|"
                       r"not found in datasource|Permission denied|Attribute '.*' MUST be set", re.I)

    def __init__(self, host, port, user, password):
        import paramiko
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(host, port=port, username=user, password=password, timeout=15,
                                look_for_keys=False, allow_agent=False, banner_timeout=30)
        except paramiko.AuthenticationException:
            raise CLIError("usuário ou senha do admin recusados")
        except (paramiko.SSHException, OSError) as e:
            raise CLIError(f"não foi possível abrir o SSH: {e}")
        self.chan = self.client.invoke_shell(width=511, height=1000)
        self._read(timeout=30)

    def _read(self, timeout=30, answer_yes=False):
        buf, deadline = "", time.time() + timeout
        while time.time() < deadline:
            if self.chan.recv_ready():
                buf += self.chan.recv(65535).decode("utf-8", "replace")
                tail = buf.rstrip("\r\n").splitlines()[-1] if buf.strip() else ""
                if "--More--" in tail:
                    self.chan.send(" ")
                elif "Press 'a' to accept" in tail:
                    self.chan.send("a")
                elif answer_yes and re.search(r"\(y/n\)\s*$", tail):
                    self.chan.send("y")
                elif self.PROMPT.search(tail):
                    return buf
            else:
                time.sleep(0.05)
        raise CLIError("o FortiGate não respondeu a tempo")

    def run(self, command, answer_yes=False):
        self.chan.send(command + "\n")
        out = self._read(answer_yes=answer_yes)
        out = re.sub(r".*--More--[\s\b\r]*", "", out)
        lines = out.replace("\r", "").split("\n")[1:-1]   # tira o eco do comando e o prompt
        return "\n".join(lines)

    def script(self, lines):
        """Executa linhas de configuração; devolve [(linha, erro)]."""
        errors = []
        for line in lines:
            out = self.run(line)
            for o in out.splitlines():
                if self.ERROR.search(o):
                    errors.append((line, o.strip()))
        return errors

    def close(self):
        self.client.close()


def parse_table(text):
    """Converte a saída de 'show <tabela>' em {nome: {campo: valor bruto}}."""
    entries, current, depth = {}, None, 0
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("config "):
            depth += 1
        elif line == "end":
            depth -= 1
        elif depth == 1 and line.startswith("edit "):
            current = entries.setdefault(shlex.split(line[5:])[0], {})
        elif depth == 1 and line == "next":
            current = None
        elif depth == 1 and current is not None and line.startswith("set "):
            key, _, value = line[4:].partition(" ")
            current[key] = value
    return entries


def values(raw):
    return shlex.split(raw or "")


def q(value):
    return '"' + value.replace('"', '\\"') + '"'


# ------------------------------------------------------------------ configuração automática

def auto_configure(host, names, demo=False):
    title("Acesso de administrador ao FortiGate (usado só agora, não é salvo)")
    info("Use um admin com perfil super_admin. O instalador entra por SSH, mostra o que vai")
    info("mudar e só aplica depois da sua confirmação.")
    if demo:
        cli = DemoCLI()
    else:
        ssh_port = int(ask("Porta SSH do FortiGate", "22", lambda a: None if a.isdigit() else "Número inválido."))
        if not tcp_open(host, ssh_port):
            fail(f"A porta SSH {ssh_port} de {host} não responde.")
            info("Habilite SSH na interface (Network > Interfaces > Administrative Access: SSH) ou")
            info("escolha o modo manual. Veja " + DOCS)
            return None
        user = ask("Usuário admin", "admin")
        password = ask_secret("Senha do admin (não aparece enquanto digita)")
        try:
            cli = FortiCLI(host, ssh_port, user, password)
        except CLIError as e:
            fail(str(e))
            return None
        finally:
            del password

    try:
        return _auto_configure(cli, host, names)
    except CLIError as e:
        fail(f"Erro na comunicação com o FortiGate: {e}")
        return None
    finally:
        cli.close()


def _auto_configure(cli, host, names):
    title("Lendo a configuração atual do FortiGate")
    status = cli.run("get system status")
    version = re.search(r"Version:\s*(.+)", status)
    info(f"Versão: {version.group(1).strip() if version else 'desconhecida'}")
    if version and "v7.2" not in version.group(1):
        warn("O painel foi feito para FortiOS 7.2. Em outras versões alguns comandos podem mudar.")
    vdom_mode = re.search(r"Virtual domain configuration:\s*(\S+)", status)
    if vdom_mode and vdom_mode.group(1) != "disable":
        fail("Este FortiGate usa VDOMs. A configuração automática não cobre esse caso.")
        info(f"Use o modo manual seguindo {DOCS}.")
        return None

    fgt_time = re.search(r"System time:\s*(.+)", status)
    if fgt_time:
        try:
            diff = abs((datetime.strptime(fgt_time.group(1).strip(), "%a %b %d %H:%M:%S %Y")
                        - datetime.now()).total_seconds())
            if diff > 600:
                warn(f"O relógio do FortiGate e o deste servidor diferem em {int(diff // 60)} minutos.")
                warn("A validade dos cadastros vai ficar deslocada. Acerte o fuso/horário de um dos dois.")
            else:
                ok("Relógio do FortiGate e do servidor conferem.")
        except ValueError:
            pass

    glob = cli.run("show system global")
    sport = re.search(r"set admin-sport (\d+)", glob)
    https_port = int(sport.group(1)) if sport else 443

    groups = parse_table(cli.run("show user group"))
    addrgrps = parse_table(cli.run("show firewall addrgrp"))
    placeholder = parse_table(cli.run(f"show firewall address {q(names['placeholder'])}"))
    exempt = parse_table(cli.run("show user security-exempt-list"))
    vaps = parse_table(cli.run("show wireless-controller vap"))
    policies = parse_table(cli.run("show firewall policy"))
    interfaces = parse_table(cli.run("show system interface"))
    user_setting = cli.run("show user setting")
    ok(f"{len(vaps)} SSID(s), {len(groups)} grupo(s) de usuário, {len(policies)} política(s)")

    plan = []   # (descrição, [linhas de CLI])

    # 1. Grupo guest
    g = names["guest_group"]
    if g in groups:
        if groups[g].get("group-type") != "guest":
            fail(f"Já existe um grupo '{g}' que não é do tipo guest. Rode de novo e escolha outro nome.")
            return None
        ok(f"Grupo de visitantes '{g}' já existe.")
    else:
        plan.append((f"Criar o grupo de visitantes '{g}' (contas com validade criadas pelo painel)", [
            "config user group", f"edit {q(g)}", "set group-type guest", "set user-id specify",
            "set password specify", "set user-name enable", "set email enable", "set mobile-phone enable",
            "set company optional", "set sponsor optional", "set expire-type immediately", "next", "end"]))

    # 2. Grupo de MACs liberados
    m, ph = names["mac_group"], names["placeholder"]
    if m in addrgrps:
        ok(f"Grupo de endereços '{m}' já existe.")
    else:
        lines = []
        if ph not in placeholder:
            lines += ["config firewall address", f"edit {q(ph)}", "set type mac", "set macaddr 02:00:00:00:00:00",
                      'set comment "Nao remover: mantem o grupo de MACs liberados nao vazio"', "next", "end"]
        lines += ["config firewall addrgrp", f"edit {q(m)}", f"set member {q(ph)}", "next", "end"]
        plan.append((f"Criar o grupo de endereços '{m}' para o \"Permitir sempre\"", lines))

    # 3. SSID
    title("SSID do captive portal")
    if not vaps:
        fail("Nenhum SSID encontrado. Crie o SSID com captive portal e rode o instalador de novo.")
        return None
    vap_names = list(vaps)
    labels, default = [], 1
    for i, name in enumerate(vap_names, 1):
        v = vaps[name]
        captive = v.get("security") == "captive-portal" or "portal-type" in v or v.get("captive-portal") == "enable"
        if captive and default == 1:
            default = i
        labels.append(f"{name}  (SSID \"{values(v.get('ssid', q(name)))[0]}\", "
                      f"segurança: {v.get('security', '?')}{', captive portal' if captive else ''})")
    vap_name = vap_names[choose("Qual SSID usa o captive portal?", labels, default)]
    vap = vaps[vap_name]

    group_key = next((k for k in ("selected-usergroups", "usergroup") if k in vap), "selected-usergroups")
    vap_groups = values(vap.get(group_key))
    info(f"Grupos de usuário hoje no SSID: {', '.join(vap_groups) or 'nenhum'}")
    vap_lines = []
    if g not in vap_groups:
        vap_lines.append(f"append {group_key} {q(g)}")

    # Lista de exceção: reaproveita a do SSID, se já houver
    current_list = values(vap.get("security-exempt-list"))
    list_name = current_list[0] if current_list else names["exempt_list"]
    exempt_text = cli.run(f"show user security-exempt-list {q(list_name)}") if list_name in exempt else ""
    has_rule = f"set srcaddr {q(m)}" in exempt_text or f" {q(m)}" in exempt_text
    exempt_lines = []
    if not has_rule:
        exempt_lines = ["config user security-exempt-list", f"edit {q(list_name)}", "config rule",
                        "edit 0", f"set srcaddr {q(m)}", "next", "end", "next", "end"]
    if not current_list:
        vap_lines.append(f"set security-exempt-list {q(list_name)}")

    if exempt_lines:
        plan.append((f"Lista de exceção do portal '{list_name}': aparelhos de '{m}' navegam sem login", exempt_lines))
    if vap_lines:
        desc = []
        if g not in vap_groups:
            desc.append(f"incluir o grupo '{g}' (os grupos atuais continuam)")
        if not current_list:
            desc.append(f"usar a lista de exceção '{list_name}'")
        plan.append((f"SSID '{vap_name}': " + " e ".join(desc),
                     ["config wireless-controller vap", f"edit {q(vap_name)}", *vap_lines, "next", "end"]))
    if not vap_lines and not exempt_lines:
        ok(f"SSID '{vap_name}' já está configurado.")

    # 4. Política de firewall que exige grupo bloquearia os aparelhos liberados
    blocking = [(pid, p) for pid, p in policies.items()
                if vap_name in values(p.get("srcintf")) and (p.get("groups") or p.get("users"))
                and p.get("action") == "accept"]
    has_mac_policy = any(m in values(p.get("srcaddr")) and vap_name in values(p.get("srcintf"))
                         for p in policies.values())
    if blocking and not has_mac_policy:
        pid, pol = blocking[0]
        title("Política de firewall")
        warn(f"A política {pid} ({values(pol.get('name', q('sem nome')))[0]}) exige grupo de usuário na origem.")
        warn("Aparelhos liberados por MAC não fazem login, então ela não deixaria eles navegarem.")
        info(f"Solução: criar uma cópia dela, logo acima, com origem '{m}' e sem usuário/grupo.")
        if yes_no("Incluir essa política no plano?", True):
            skip = {"name", "uuid", "srcintf", "srcaddr", "groups", "users", "comments", "fsso-groups"}
            copied = [f"set {k} {v}" for k, v in pol.items() if k not in skip]
            plan.append((f"Criar a política '{names['mac_policy']}' acima da {pid}, só para os MACs liberados", [
                "config firewall policy", "edit 0", f"set name {q(names['mac_policy'])}",
                f"set srcintf {q(vap_name)}", f"set srcaddr {q(m)}", *copied,
                'set comments "Criada pelo instalador do Acesso Wi-Fi"', "next", "end",
                f"@move-before {pid}"]))
        else:
            warn(f"Sem essa política o \"Permitir sempre\" não vai funcionar. Veja {DOCS}.")

    # 5. Tempo máximo de sessão
    title("Tempo máximo de sessão do captive portal")
    info("Quando uma conta vence ou é removida, quem já estava logado continua navegando até a sessão")
    info("expirar. Com tempo máximo, todos (inclusive quem entra pelo Google) precisam fazer login de")
    info("novo a cada X horas. Aparelhos com \"Permitir sempre\" não são afetados.")
    current_timeout = re.search(r"set auth-timeout (\d+)", user_setting)
    current_type = re.search(r"set auth-timeout-type (\S+)", user_setting)
    info(f"Hoje: {current_timeout.group(1) if current_timeout else '5'} min, "
         f"tipo {current_type.group(1) if current_type else 'idle-timeout'}")
    if yes_no("Definir um tempo máximo de sessão?", True):
        hours = int(ask("Horas", "4", lambda a: None if a.isdigit() and 1 <= int(a) <= 72 else "Entre 1 e 72."))
        plan.append((f"Sessões do portal duram no máximo {hours} h", [
            "config user setting", f"set auth-timeout {hours * 60}", "set auth-timeout-type hard-timeout", "end"]))

    # 6. HTTPS de administração na interface que o servidor usa
    server_ip = local_ip_towards(host) if valid_ipv4(host) else ""
    server_ip = ask("IP deste servidor visto pelo FortiGate (vai no trusted host do token)", server_ip,
                    lambda a: None if valid_ipv4(a) else "IP inválido.")
    for ifname, iface in interfaces.items():
        ip_mask = values(iface.get("ip"))
        if len(ip_mask) == 2 and _same_subnet(server_ip, *ip_mask):
            access = values(iface.get("allowaccess"))
            if "https" not in access:
                plan.append((f"Liberar HTTPS de administração na interface '{ifname}' (a API usa HTTPS)", [
                    "config system interface", f"edit {q(ifname)}", "append allowaccess https", "next", "end"]))
            break

    # 7. Perfil e admin da API
    plan.append((f"Perfil '{names['profile']}': usuários (leitura/escrita), endereços (leitura/escrita), "
                 "Wi-Fi e políticas (leitura)", [
        "config system accprofile", f"edit {q(names['profile'])}", "set authgrp read-write", "set wifi read",
        "set fwgrp custom", "config fwgrp-permission", "set address read-write", "set policy read", "end",
        "next", "end"]))
    plan.append((f"Admin de API '{names['api_user']}', aceito só a partir de {server_ip}, e geração do token", [
        "config system api-user", f"edit {q(names['api_user'])}", 'set comments "Painel Acesso Wi-Fi"',
        f"set accprofile {q(names['profile'])}", 'set vdom "root"', "config trusthost", "edit 1",
        f"set ipv4-trusthost {server_ip} 255.255.255.255", "next", "end", "next", "end"]))

    # --- confirmação
    title("Alterações que serão feitas no FortiGate")
    for i, (desc, _) in enumerate(plan, 1):
        info(f"{i}. {desc}")
    if yes_no("Ver os comandos exatos?", False):
        for desc, lines in plan:
            print(f"\n   # {desc}")
            for line in lines:
                print(f"     {line}")
    if not yes_no("Aplicar estas alterações no FortiGate?", True):
        warn("Nada foi alterado.")
        return None

    title("Aplicando")
    for desc, lines in plan:
        config_lines = [ln for ln in lines if not ln.startswith("@")]
        errors = cli.script(config_lines)
        if errors:
            fail(desc)
            for line, err in errors:
                info(f"   comando: {line}")
                info(f"   resposta: {err}")
            info("As etapas anteriores já foram aplicadas; as seguintes não. Corrija e rode o instalador de novo.")
            return None
        for ln in lines:
            if ln.startswith("@move-before "):
                target = ln.split()[1]
                new = parse_table(cli.run("show firewall policy"))
                new_id = next(pid for pid, p in new.items() if values(p.get("name")) == [names["mac_policy"]])
                errors = cli.script(["config firewall policy", f"move {new_id} before {target}", "end"])
                if errors:
                    fail(f"Não consegui mover a política {new_id} para cima da {target}: {errors[0][1]}")
                    return None
        ok(desc)

    out = cli.run(f"execute api-user generate-key {q(names['api_user'])}", answer_yes=True)
    key = re.search(r"New API key:\s*(\S+)", out)
    if not key:
        fail("Não consegui gerar o token da API. Resposta do FortiGate:")
        info(out.strip())
        return None
    ok("Token da API gerado.")
    return {"host": host if https_port == 443 else f"{host}:{https_port}", "token": key.group(1)}


def _same_subnet(ip, net_ip, mask):
    def to_int(a):
        return int.from_bytes(socket.inet_aton(a), "big")
    try:
        return to_int(ip) & to_int(mask) == to_int(net_ip) & to_int(mask)
    except OSError:
        return False


# ------------------------------------------------------------------ verificação com o token

def verify_api(host, token, names, demo=False):
    title("Testando a API com o token")
    if demo:
        from mock_data import MockFortiGate
        fgt = MockFortiGate()
    else:
        fgt = FortiGate(host, token, verify=False, timeout=10)
    for attempt in range(5):   # o FortiGate leva alguns segundos para aceitar um token novo
        try:
            users = fgt.local_users()
            break
        except FortiGateError as e:
            if attempt == 4:
                fail(str(e))
                return False
            time.sleep(3)
    ok(f"Conexão OK ({len(users)} usuário(s) local(is)).")
    try:
        groups = fgt.user_groups()
        guest = [x["name"] for x in groups if x.get("group-type") == "guest"]
        if names["guest_group"] in guest:
            ok(f"Grupo de visitantes '{names['guest_group']}' acessível.")
        else:
            warn(f"Grupo '{names['guest_group']}' não encontrado. Grupos guest: {', '.join(guest) or 'nenhum'}")
        fgt.address_group(names["mac_group"])
        ok(f"Grupo de endereços '{names['mac_group']}' acessível.")
        ok(f"{len(fgt.wifi_clients())} cliente(s) Wi-Fi conectado(s) agora.")
    except FortiGateError as e:
        warn(str(e))
    return True


def manual_token(names):
    title("Modo manual")
    info(f"Configure o FortiGate seguindo {DOCS} (ou cole deploy/fortigate-setup.conf na CLI).")
    info("O comando 'execute api-user generate-key acessowifi' mostra o token.")
    return ask_secret("Token da API (não aparece enquanto digita)")


# ------------------------------------------------------------------ principal

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, help="caminho do .env a gerar")
    parser.add_argument("--demo", action="store_true", help="simula o FortiGate (para testes)")
    args = parser.parse_args()
    names = dict(DEFAULTS)

    print("\nEste assistente pergunta tudo o que o painel precisa. Enter aceita o valor entre [colchetes].")

    # --- FortiGate
    title("1/4  FortiGate")
    while True:
        host = ask("IP do FortiGate", "192.168.1.99")
        host = re.sub(r"^https?://", "", host).split("/")[0]
        if args.demo or tcp_open(host.split(":")[0], int(host.split(":")[1]) if ":" in host else 443) \
                or tcp_open(host.split(":")[0], 22):
            break
        warn(f"{host} não responde em HTTPS (443) nem SSH (22). Confira o IP e a rede.")
        if yes_no("Continuar mesmo assim?", False):
            break

    mode = choose("Como configurar o FortiGate?", [
        "Automático (recomendado): o instalador entra por SSH com um admin, mostra as mudanças e aplica",
        "Manual: eu mesmo configuro e colo o token da API",
    ])
    result = None
    if mode == 0:
        result = auto_configure(host.split(":")[0], names, demo=args.demo)
        if result is None:
            if not yes_no("A configuração automática não terminou. Continuar no modo manual?", True):
                sys.exit(1)
    if result is None:
        port = host.split(":")[1] if ":" in host else ask("Porta HTTPS de administração do FortiGate", "443")
        result = {"host": host.split(":")[0] + ("" if str(port) == "443" else f":{port}"),
                  "token": manual_token(names)}

    if not verify_api(result["host"], result["token"], names, demo=args.demo):
        info("Causas comuns: trusted host diferente do IP deste servidor, HTTPS de administração")
        info(f"desligado na interface, porta errada ou token incorreto. Veja {DOCS}.")
        if not yes_no("Gravar a configuração mesmo assim (dá para testar depois)?", False):
            sys.exit(1)

    # --- Fuso
    title("2/4  Fuso horário")
    info("A validade dos cadastros usa a hora local. O fuso deve ser o mesmo do FortiGate.")
    tz_default = os.environ.get("TZ") or _system_timezone() or "America/Sao_Paulo"
    tz = ask("Fuso horário", tz_default)

    # --- Login do painel
    title("3/4  Login do painel")
    panel_user = ask("Usuário para entrar no painel", "admin",
                     lambda a: None if re.fullmatch(r"[\w.@-]{3,64}", a) else "Use 3 a 64 letras, números ou . _ @ -")
    while True:
        pw = ask_secret("Senha do painel (mínimo 8 caracteres)")
        if len(pw) < 8:
            warn("Use pelo menos 8 caracteres.")
        elif pw != ask_secret("Repita a senha"):
            warn("As senhas não conferem.")
        else:
            break

    # --- Acesso
    title("4/4  Acesso ao painel")
    if yes_no("Usar HTTPS com nginx (certificado autoassinado)? Recomendado", True):
        bind, cookie_secure = "127.0.0.1:8080", "true"
    else:
        port = ask("Porta HTTP do painel (sem HTTPS: use só na rede interna)", "8080",
                   lambda a: None if a.isdigit() and 1 <= int(a) <= 65535 else "Porta inválida.")
        bind, cookie_secure = f"0.0.0.0:{port}", "false"

    from werkzeug.security import generate_password_hash
    env = {
        "FGT_HOST": result["host"], "FGT_TOKEN": result["token"], "FGT_VDOM": "root", "FGT_VERIFY_SSL": "false",
        "FGT_GUEST_GROUPS": names["guest_group"], "FGT_MAC_GROUP": names["mac_group"],
        "MAX_VALIDITY_DAYS": "365", "TZ": tz,
        "APP_USER": panel_user, "APP_PASSWORD_HASH": generate_password_hash(pw),
        "SECRET_KEY": secrets.token_hex(32), "COOKIE_SECURE": cookie_secure, "BIND": bind,
    }
    del pw
    path = Path(args.env)
    old_umask = os.umask(0o027)
    try:
        with open(path, "w") as f:
            f.write(f"# Gerado pelo instalador em {datetime.now():%Y-%m-%d %H:%M}. "
                    "Depois de editar: systemctl restart acessowifi\n")
            for key, value in env.items():
                f.write(f"{key}='{value}'\n")
    finally:
        os.umask(old_umask)
    ok(f"Configuração gravada em {path}")


def _system_timezone():
    try:
        return str(Path("/etc/localtime").resolve()).split("zoneinfo/")[1]
    except (IndexError, OSError):
        return None


# ------------------------------------------------------------------ FortiGate simulado (--demo)

class DemoCLI:
    """Respostas fixas de um FortiGate 40F para testar o fluxo sem equipamento."""

    OUTPUTS = {
        "get system status": "Version: FortiGate-40F v7.2.13,build1234,250101 (GA.M)\n"
                             "Virtual domain configuration: disable\n"
                             f"System time: {datetime.now():%a %b %d %H:%M:%S %Y}",
        "show system global": 'config system global\n    set hostname "FGT40F"\nend',
        "show user group": 'config user group\n    edit "WiFi-Google"\n        set member "Google-Workspace"\n'
                           '    next\nend',
        "show wireless-controller vap": 'config wireless-controller vap\n    edit "Acampamento"\n'
                                        '        set ssid "Acampamento"\n        set security captive-portal\n'
                                        '        set selected-usergroups "WiFi-Google"\n    next\n'
                                        '    edit "Equipe"\n        set ssid "Equipe"\n'
                                        '        set security wpa2-only-personal\n    next\nend',
        "show firewall policy": 'config firewall policy\n    edit 3\n        set name "wifi-internet"\n'
                                '        set srcintf "Acampamento"\n        set dstintf "wan"\n'
                                '        set action accept\n        set srcaddr "all"\n        set dstaddr "all"\n'
                                '        set schedule "always"\n        set service "ALL"\n'
                                '        set groups "WiFi-Google"\n        set nat enable\n    next\nend',
        "show system interface": 'config system interface\n    edit "internal"\n'
                                 '        set ip 192.168.1.99 255.255.255.0\n        set allowaccess ping ssh\n'
                                 '    next\nend',
    }

    def __init__(self):
        self.sent = []
        self.moved = False

    def run(self, command, answer_yes=False):
        self.sent.append(command)
        if command.startswith("execute api-user generate-key"):
            return "New API key: demo" + secrets.token_hex(12)
        if command == "show firewall policy" and self.moved is False and any("edit 0" == c for c in self.sent):
            self.moved = True
            return self.OUTPUTS[command].replace("end", '    edit 9\n        set name "acessowifi-mac-liberados"\n'
                                                        "    next\nend")
        return self.OUTPUTS.get(command, "")

    def script(self, lines):
        for line in lines:
            self.run(line)
        return []

    def close(self):
        pass


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado.")
        sys.exit(130)
