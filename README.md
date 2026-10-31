# Acesso Wi-Fi — painel para FortiGate 40F

Painel interno para o Wi-Fi do acampamento (FortiGate 40F + FortiAP, captive portal, FortiOS 7.2):

- **Cadastrar usuários** de acesso à internet, sempre com **data de validade**. O FortiGate bloqueia o login sozinho quando a conta vence.
- Renovar a validade, gerar nova senha e remover acesso.
- Ver **quem está conectado agora**: quem entrou com usuário cadastrado, quem entrou pelo **Google Workspace (SAML)** e quem está conectado ao SSID mas ainda **não fez login** no portal.
- **Permitir sempre** um aparelho: o celular é liberado pelo MAC e não passa mais pelo captive portal.
- Ver os FortiAPs e quantos clientes cada um tem.

Roda no seu servidor e conversa com o FortiGate pela API REST.

```
Navegador ──HTTPS──> nginx ──> Flask (este app) ──HTTPS + token──> FortiGate 40F ──> FortiAPs
```

O token do FortiGate fica só no servidor e nunca chega ao navegador.

## Implantação em 3 passos

1. **FortiGate:** abra `deploy/fortigate-setup.conf`, troque o IP do servidor e cole os comandos na CLI do FortiGate. O último comando gera o **token da API**; copie na hora.
2. **Servidor:** `sudo ./deploy/install.sh` e responda às perguntas (IP do FortiGate, token, usuário e senha do painel).
3. **Navegador:** abra o endereço que o instalador mostrar no final.

### Onde ficam o "usuário e senha" do FortiGate?

O painel **não usa usuário e senha** de admin do FortiGate. Ele se conecta pela **API REST com um token**, que é o jeito recomendado pela Fortinet:

- o token pertence a um *REST API Admin* (`acessowifi`) com permissões só do que o painel precisa;
- ele só funciona a partir do IP do servidor (*trusted host*);
- ele fica em `/opt/acessowifi/.env` (`FGT_HOST` e `FGT_TOKEN`), legível só pelo root e pelo serviço, e nunca vai para o navegador.

O **usuário e a senha que você escolhe no instalador** são só para entrar no painel.

Se o token vazar ou você quiser trocá-lo: `execute api-user generate-key acessowifi` no FortiGate gera um novo e invalida o anterior. Atualize `FGT_TOKEN` no `.env` e reinicie o serviço.

## Como funciona o cadastro

Os usuários criados pelo painel são **contas de visitante (guest)** dentro de um grupo do tipo *guest* do FortiGate (por padrão, `WiFi-Cadastrados`). É o recurso nativo do FortiOS para contas com expiração:

- cada conta tem sua própria data/hora de validade;
- a expiração é aplicada pelo FortiGate, mesmo com o painel desligado;
- o painel só cria, altera e remove contas nos grupos listados em `FGT_GUEST_GROUPS`. Os grupos do Google, de admins etc. ficam intocados;
- a senha é mostrada **uma única vez**, logo após o cadastro ou a troca. Se ficar em branco, o painel gera uma de 8 caracteres, fácil de digitar no celular.

O login do Google Workspace continua funcionando como hoje. O painel só identifica essas pessoas, pelo grupo que tem o servidor SAML como membro, e as mostra como "Google Workspace" na lista de conectados.

## Permitir sempre (liberação por MAC)

Na aba **Conectados agora**, o botão **Permitir sempre** aparece em quem já fez login. O painel pega o MAC daquele aparelho e o coloca na lista de exceção do captive portal. A partir daí o aparelho navega direto, sem portal.

- **Validade:** se a pessoa tem conta cadastrada no painel, a liberação **segue a conta**. Renovou a conta, o aparelho continua liberado; venceu ou foi removida, a liberação sai. Para usuários do Google, você escolhe a data na hora.
- A remoção das liberações vencidas é feita por um *timer* do systemd a cada 5 minutos (`flask cleanup-devices`).
- A aba **Aparelhos liberados** lista tudo e permite remover.
- No FortiGate, cada aparelho vira um endereço `acessowifi-<mac>` do tipo MAC, membro do grupo `WiFi-MAC-Liberados`. O vínculo com o usuário fica no comentário do endereço.

**Limitações importantes:**
- **MAC aleatório:** iPhone e Android usam "endereço privado" por padrão. Ele costuma ser fixo por rede, mas muda se a pessoa "esquecer a rede", e no iOS 18 pode ficar "rotativo". Aí o aparelho volta a cair no portal. O painel marca esses aparelhos; para liberar de vez, desative o endereço privado dessa rede no celular e libere de novo.
- **MAC pode ser clonado:** quem descobrir um MAC liberado consegue usá-lo para entrar sem login. Use para aparelhos de confiança.
- O tráfego de aparelhos liberados não fica associado ao usuário nos logs do FortiGate, porque eles não fazem login. O painel mostra o dono pelo vínculo salvo.

## 1. Preparar o FortiGate (FortiOS 7.2)

Tudo desta seção está pronto para colar em `deploy/fortigate-setup.conf`. Abaixo, a explicação de cada parte.

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

**c) Lista de exceção do captive portal** (para "Permitir sempre"). O FortiOS não aceita grupo de endereços vazio, então o grupo começa com um endereço fictício que não deve ser removido:
```
config firewall address
    edit "WiFi-MAC-placeholder"
        set type mac
        set macaddr 02:00:00:00:00:00
        set comment "Nao remover: mantem o grupo WiFi-MAC-Liberados nao vazio"
    next
end
config firewall addrgrp
    edit "WiFi-MAC-Liberados"
        set member "WiFi-MAC-placeholder"
    next
end
config user security-exempt-list
    edit "WiFi-Liberados"
        config rule
            edit 1
                set srcaddr "WiFi-MAC-Liberados"
            next
        end
    next
end
config wireless-controller vap
    edit "NOME_DO_SSID"
        set security-exempt-list "WiFi-Liberados"
    next
end
```
Pela GUI: SSID → Captive Portal → **Exempt sources** → `WiFi-MAC-Liberados`.
> Se a política de firewall do SSID para a internet exigir **grupos de usuário** na origem, o aparelho liberado não vai casar com ela, porque não fez login. Nesse caso, crie uma política acima com origem `WiFi-MAC-Liberados`, sem usuário.

**d) Perfil da API**: escrita em usuários e endereços, leitura no Wi-Fi.
```
config system accprofile
    edit "api-acessowifi"
        set authgrp read-write
        set wifi read
        set fwgrp custom
        config fwgrp-permission
            set address read-write
        end
    next
end
```
Com essas permissões, o token consegue alterar qualquer usuário, grupo ou endereço do FortiGate. É o painel que limita as alterações ao grupo guest e aos endereços `acessowifi-*`. Por isso o token precisa de *trusted host* restrito e o `.env` precisa de `chmod 600`.

**e) Admin REST API**, com o trusted host restrito ao IP do servidor:
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

**f) Recomendado: sessão com tempo máximo.** Quando uma conta expira ou é removida, quem já estava logado continua navegando até o *auth-timeout* acabar. Com timeout "hard", o corte acontece no máximo X minutos depois:
```
config user setting
    set auth-timeout 240
    set auth-timeout-type hard-timeout
end
```

**g) Mesmo fuso horário** no FortiGate e no servidor, porque a validade é gravada na hora local:
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
Teste também a liberação por MAC (e confira se o endereço aparece em *Policy & Objects > Addresses*):
```bash
curl -sk -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  "$F/cmdb/firewall/address?vdom=root" \
  -d '{"name":"acessowifi-teste","type":"mac","macaddr":[{"macaddr":"02:11:22:33:44:55"}],"comment":"teste"}'
curl -sk -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  "$F/cmdb/firewall/addrgrp/WiFi-MAC-Liberados/member?vdom=root" -d '{"name":"acessowifi-teste"}'
# desfazer
curl -sk -X DELETE -H "Authorization: Bearer $T" "$F/cmdb/firewall/addrgrp/WiFi-MAC-Liberados/member/acessowifi-teste?vdom=root"
curl -sk -X DELETE -H "Authorization: Bearer $T" "$F/cmdb/firewall/address/acessowifi-teste?vdom=root"
```
Se esses testes funcionarem, o painel vai funcionar. Para remover a conta de teste, use o `id` que aparece no GET do grupo:
`curl -sk -X DELETE -H "Authorization: Bearer $T" "$F/cmdb/user/group/WiFi-Cadastrados/guest/ID?vdom=root"`

## 2. Instalar no servidor (Linux)

Debian/Ubuntu com systemd. O instalador faz tudo e pergunta só o necessário:

```bash
git clone https://github.com/ecruz-acamps/acessowifi
cd acessowifi
sudo ./deploy/install.sh
```

Ele instala o Python e as bibliotecas, copia o painel para `/opt/acessowifi`, pede **IP e token do FortiGate** e o **usuário e a senha do painel**, cria o `.env` protegido e (opcionalmente) instala o nginx com HTTPS. Depois ativa os serviços e **testa a conexão com o FortiGate**, inclusive a escrita.

- **Atualizar:** `git pull && sudo ./deploy/install.sh`. A configuração em `/opt/acessowifi/.env` é mantida.
- **Mudar IP/token/senha:** edite `/opt/acessowifi/.env` e rode `sudo systemctl restart acessowifi`. Para refazer tudo pelas perguntas, apague o `.env` e rode o instalador de novo.
- **Testar o FortiGate a qualquer momento:**
  `cd /opt/acessowifi && sudo -u acessowifi .venv/bin/flask --app app check-fortigate --escrita`
- **Log** (inclui quem cadastrou, alterou ou removeu cada usuário e aparelho): `journalctl -u acessowifi -f`

Não publique o painel na internet; restrinja à rede da administração. Para trocar o certificado autoassinado por um seu, substitua `/etc/ssl/acessowifi/cert.pem` e `key.pem` e rode `systemctl reload nginx`.

<details><summary>Instalação manual (sem o instalador)</summary>

```bash
sudo useradd -r -s /usr/sbin/nologin acessowifi
sudo git clone https://github.com/ecruz-acamps/acessowifi /opt/acessowifi && cd /opt/acessowifi
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env
sudo chown root:acessowifi .env && sudo chmod 640 .env
sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now acessowifi acessowifi-cleanup.timer
```
Hash da senha do painel e chave de sessão:
```bash
.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g(input('Senha: ')))"
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```
No `.env`, coloque o hash entre aspas simples, porque ele contém `$`. Para HTTPS, use `deploy/nginx.conf` como base.
</details>

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
