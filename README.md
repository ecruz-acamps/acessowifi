# Acesso Wi-Fi — painel para FortiGate 40F

Painel interno para o Wi-Fi do acampamento (FortiGate 40F + FortiAP, captive portal, FortiOS 7.2):

- **Cadastrar usuários** de acesso à internet, sempre com **data de validade**. O FortiGate bloqueia o login sozinho quando a conta vence.
- Renovar a validade, gerar nova senha e remover acesso.
- Ver **quem está conectado agora**: quem entrou com usuário cadastrado, quem entrou pelo **Google Workspace (SAML)** e quem está conectado ao SSID mas ainda **não fez login** no portal.
- Ver os FortiAPs e quantos clientes cada um tem.

Roda no seu servidor e conversa com o FortiGate pela API REST.

```
Navegador ──HTTPS──> nginx ──> Flask (este app) ──HTTPS + token──> FortiGate 40F ──> FortiAPs
```

O token do FortiGate fica só no servidor e nunca chega ao navegador.

## Como funciona o cadastro

Os usuários criados pelo painel são **contas de visitante (guest)** dentro de um grupo do tipo *guest* do FortiGate (por padrão, `WiFi-Cadastrados`). É o recurso nativo do FortiOS para contas com expiração:

- cada conta tem sua própria data/hora de validade;
- a expiração é aplicada pelo FortiGate, mesmo com o painel desligado;
- o painel só cria, altera e remove contas nos grupos listados em `FGT_GUEST_GROUPS`. Os grupos do Google, de admins etc. ficam intocados;
- a senha é mostrada **uma única vez**, logo após o cadastro ou a troca. Se ficar em branco, o painel gera uma de 8 caracteres, fácil de digitar no celular.

O login do Google Workspace continua funcionando como hoje. O painel só identifica essas pessoas, pelo grupo que tem o servidor SAML como membro, e as mostra como "Google Workspace" na lista de conectados.

## 1. Preparar o FortiGate (FortiOS 7.2)

**a) Grupo de visitantes**, para as contas criadas pelo painel:
```
config user group
    edit "WiFi-Cadastrados"
        set group-type guest
        set user-id specify
        set password specify
        set user-name enable
        set email enable
        set mobile-phone enable
        set company optional
        set sponsor optional
        set expire-type immediately
    next
end
```

**b) Adicionar o grupo ao captive portal do SSID**: *WiFi & Switch Controller > SSIDs > (seu SSID) > Security Mode: Captive Portal > User Groups*. Inclua `WiFi-Cadastrados` junto com o grupo do Google que já existe. Confira também se a política de firewall do SSID libera esse grupo.

**c) Perfil da API**: leitura e escrita em usuários, leitura no Wi-Fi.
```
config system accprofile
    edit "api-acessowifi"
        set authgrp read-write
        set wifi read
    next
end
```
Com `authgrp read-write`, o token consegue alterar qualquer usuário ou grupo do FortiGate. É o painel que limita as alterações ao grupo guest. Por isso o token precisa de *trusted host* restrito e o `.env` precisa de `chmod 600`.

**d) Admin REST API**, com o trusted host restrito ao IP do servidor:
```
config system api-user
    edit "acessowifi"
        set accprofile "api-acessowifi"
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
O token aparece uma única vez; copie na hora. A interface pela qual o servidor alcança o FortiGate precisa de `set allowaccess https`.

**e) Recomendado: sessão com tempo máximo.** Quando uma conta expira ou é removida, quem já estava logado continua navegando até o *auth-timeout* acabar. Com timeout "hard", o corte acontece no máximo X minutos depois:
```
config user setting
    set auth-timeout 240
    set auth-timeout-type hard-timeout
end
```

**f) Mesmo fuso horário** no FortiGate e no servidor, porque a validade é gravada na hora local:
`config system global` → `set timezone` (Brasília) e `TZ=America/Sao_Paulo` no `.env`.

### Testar a API antes de instalar

Os comandos abaixo são executados a partir do servidor:
```bash
T=SEU_TOKEN; F=https://IP_DO_FORTIGATE/api/v2

# leitura
curl -sk -H "Authorization: Bearer $T" "$F/monitor/wifi/client?vdom=root"
curl -sk -H "Authorization: Bearer $T" "$F/monitor/user/firewall?vdom=root"
curl -sk -H "Authorization: Bearer $T" "$F/cmdb/user/group/WiFi-Cadastrados?vdom=root"

# cadastro de teste (confira na GUI se aparece com a validade certa e depois remova)
curl -sk -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  "$F/cmdb/user/group/WiFi-Cadastrados/guest?vdom=root" \
  -d '{"user-id":"teste.api","password":"teste1234","name":"Teste API","expiration":"2026/12/31 23:59:00"}'
```
Se o teste de cadastro funcionar, o painel vai funcionar. Para remover a conta de teste, use o `id` que aparece no GET do grupo:
`curl -sk -X DELETE -H "Authorization: Bearer $T" "$F/cmdb/user/group/WiFi-Cadastrados/guest/ID?vdom=root"`

## 2. Instalar no servidor (Linux)

```bash
sudo useradd -r -s /usr/sbin/nologin acessowifi
sudo git clone https://github.com/ecruz-acamps/acessowifi /opt/acessowifi && cd /opt/acessowifi
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env      # FGT_HOST, FGT_TOKEN, hash da senha, SECRET_KEY
sudo chown -R acessowifi: /opt/acessowifi && sudo chmod 600 .env

sudo cp deploy/acessowifi.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now acessowifi
journalctl -u acessowifi -f      # log, incluindo quem cadastrou/alterou/removeu cada usuário
```

Gerar a senha do painel e a chave de sessão:
```bash
.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g(input('Senha: ')))"
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```
> No `.env`, coloque o hash entre aspas simples, porque ele contém `$`.

Depois configure o nginx com HTTPS usando `deploy/nginx.conf` como base. Não publique o painel na internet; restrinja à rede da administração.

## 3. Testar sem o FortiGate

```bash
DEMO_MODE=1 COOKIE_SECURE=false SECRET_KEY=x APP_USER=admin \
APP_PASSWORD_HASH="$(.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g('teste'))")" \
.venv/bin/python app.py
# http://127.0.0.1:8080  (admin / teste) — as alterações ficam só na memória
```

## Observações

- Os nomes de campos da API variam um pouco entre versões do FortiOS. Se alguma coluna vier vazia, compare a saída do `curl` com o mapeamento em `connected_clients()`/`registered_users()` no `app.py`.
- Hashes de senha (`passwd`) do FortiGate nunca vão para a página.
- O backend guarda leituras em cache por 10 s e limpa o cache a cada alteração. A página atualiza a cada 30 s.
