# Acesso Wi-Fi — painel para FortiGate 40F

Página interna para consultar os **usuários cadastrados** para acesso à rede e ver **quem está conectado no Wi-Fi** agora.
Roda no seu servidor e consulta o FortiGate pela API REST, **somente leitura**.

```
Navegador ──HTTPS──> nginx ──> Flask (este app) ──HTTPS + token──> FortiGate 40F
```

O token do FortiGate fica só no servidor e nunca chega ao navegador.

## O que a página mostra

| Aba | Fonte no FortiOS |
|---|---|
| Conectados agora | `monitor/wifi/client` + `monitor/user/firewall` (captive portal, cruzado pelo IP) |
| Usuários cadastrados | `cmdb/user/local` + `cmdb/user/group` (membros e visitantes/guest) |
| Access Points | `monitor/wifi/managed_ap` |

O cruzamento mostra quem está **online**, clientes **sem usuário** (rede PSK/aberta) e usuários conectados que **não estão cadastrados** localmente (por exemplo, quando vêm via RADIUS/LDAP).

## 1. Preparar o FortiGate

**a) Perfil somente leitura** (System > Admin Profiles > Create New), nome `api-readonly`:
- *User & Device*: **Read**
- *WiFi & Switch*: **Read**
- *Log & Report*: **Read**
- o resto: **None**

Ou pela CLI:
```
config system accprofile
    edit "api-readonly"
        set secfabgrp read
        set wifi read
        set loggrp read
        set authgrp read
    next
end
```

**b) Admin REST API** (System > Administrators > Create New > REST API Admin):
- Nome: `acessowifi`, perfil: `api-readonly`
- **Trusted Hosts**: o IP do seu servidor (ex.: `192.168.1.50/32`). Não deixe aberto.
- Copie o token gerado (ele só aparece uma vez).

```
config system api-user
    edit "acessowifi"
        set accprofile "api-readonly"
        set vdom "root"
        config trusthost
            edit 1
                set ipv4-trusthost 192.168.1.50 255.255.255.255
            next
        end
    next
end
execute api-user generate-key acessowifi
```

**c) HTTPS de administração** habilitado na interface pela qual o servidor alcança o FortiGate (`set allowaccess https`).

Teste a partir do servidor:
```bash
curl -k -H "Authorization: Bearer SEU_TOKEN" "https://IP_DO_FORTIGATE/api/v2/monitor/wifi/client?vdom=root"
```

## 2. Instalar no servidor (Linux)

```bash
sudo useradd -r -s /usr/sbin/nologin acessowifi
sudo git clone <este-repo> /opt/acessowifi && cd /opt/acessowifi
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env      # preencha FGT_HOST, FGT_TOKEN, hash da senha, SECRET_KEY
sudo chown -R acessowifi: /opt/acessowifi && sudo chmod 600 .env

sudo cp deploy/acessowifi.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now acessowifi
```

Gerar a senha e a chave:
```bash
.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g(input('Senha: ')))"
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```
> No `.env`, coloque o hash entre aspas simples, porque ele contém `$`.

Depois configure o nginx com HTTPS usando `deploy/nginx.conf` como base. Sem HTTPS, use `COOKIE_SECURE=false` e acesse apenas pela rede interna.

## 3. Testar sem o FortiGate

```bash
DEMO_MODE=1 COOKIE_SECURE=false SECRET_KEY=x APP_USER=admin \
APP_PASSWORD_HASH="$(.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g('teste'))")" \
.venv/bin/python app.py
# http://127.0.0.1:8080  (admin / teste)
```

## Observações

- **FortiWiFi 40F** (Wi-Fi embutido) e **FortiGate 40F + FortiAP** usam os mesmos endpoints. O rádio interno aparece como um AP "local".
- Os nomes de campos da API variam um pouco entre versões do FortiOS. Se alguma coluna vier vazia, compare a saída do `curl` acima com o mapeamento em `connected_clients()`/`registered_users()` no `app.py`.
- Hashes de senha dos usuários (`passwd`) são descartados pelo backend e nunca vão para a página.
- O backend guarda as respostas em cache por 10 s, e a página atualiza a cada 30 s.
