# acessowifi

Painel interno (Flask + HTML/JS puro) para o Wi-Fi de um acampamento: FortiGate 40F + FortiAP, FortiOS 7.4.13 (Mature; compatível com 7.2), SSID com captive portal que aceita usuário/senha **ou** SAML do Google Workspace.

- `fortigate.py`: cliente da API REST (`/api/v2/...`, Bearer token, cache de 10 s limpo a cada escrita).
- `app.py`: login da página, normalização e cruzamento dos dados, rotas:
  - `GET /api/clients` (wifi/client + user/firewall cruzados por IP; `source` = registered | google | mac | unknown | pending)
  - `GET /api/users`, `GET /api/groups`
  - `POST /api/users`, `PUT|DELETE /api/users/<grupo>/<id>`: contas **guest** com `expiration` (`YYYY/MM/DD HH:MM:SS`, hora local)
  - `GET|POST /api/devices`, `DELETE /api/devices/<mac>`: "permitir sempre" — endereço `type mac` (prefixo `acessowifi-`) no grupo `FGT_MAC_GROUP`, que é a exceção do captive portal. O vínculo com o usuário fica no `comment` do endereço (sem banco de dados). A validade segue a conta guest (`group=`) ou é própria (`exp=`), no caso de usuários do Google
  - `GET|POST /api/ssids`, `PUT|DELETE /api/ssids/<nome>` (`ssids.py`): SSIDs extras na rede do principal (`FGT_MAIN_SSID`), com acesso `same` (copia a segurança do principal), `wpa2` (senha 8-63, nunca devolvida ao navegador) ou `open`, nome interno `aw-*` (até 15 caracteres; o painel só altera esses), `schedule` do vap = `always` | `aw-desligado` (vencido) | agendamento único `aw-<nome>`; APs via `override-vaps`/`vap-all manual`/`vaps` por rádio em `wireless-controller wtp`, sempre conferindo que o principal continua em todos os APs; mesma rede via software switch (`system/switch-interface`) ou bridge
  - `GET /api/aps`
- `flask cleanup-devices`: remove liberações vencidas e órfãs. Roda pelo systemd timer a cada 5 min.
- `mock_data.py`: FortiGate fictício e com estado, usado com `DEMO_MODE=1`.
- `static/`: frontend. Renderize sempre com `textContent`/DOM, nunca com `innerHTML`, porque hostname e usuário vêm dos dispositivos.
- `deploy/install.sh`: instalador (dependências, cópia para `/opt/acessowifi`, nginx/HTTPS, systemd, ufw, teste final). Chama `deploy/configure.py`.
- `deploy/configure.py`: assistente que pergunta tudo e grava o `.env`. No modo automático, configura o FortiGate por SSH (paramiko) com um admin informado na hora e **não salvo**: lê o estado com `show`, monta um plano idempotente, mostra, pede confirmação, aplica e gera o token. `--demo` simula o FortiGate.
- Diferenças 7.2 → 7.4 já tratadas: SSID com `set captive-portal enable` (em vez de `set security captive-portal`); `set vdom "root"` no `system api-user` é necessário no 7.4 (senão 403 "wrong vdom") e não existe no 7.2 sem VDOMs, por isso é linha opcional (`?`) no configurador.
- `deploy/fortigate-setup.conf` e `docs/FORTIGATE.md`: configuração manual do FortiGate (CLI e GUI) e problemas comuns. Mantenha os três (configure.py, .conf, docs) coerentes ao mudar nomes ou permissões.
- `deploy/acessowifi-cli.sh` → `/usr/local/bin/acessowifi` (status, testar, log, reiniciar, limpar), funciona sem sudo, como root. O `.env` é `root:acessowifi 640`; o instalador reaplica isso em toda execução (nunca incluir o `.env` em `chown -R`).
- `deploy/acessowifi-update.sh` → `/usr/local/sbin/acessowifi-update`: `--verificar` (fetch + conta commits; timer de hora em hora) e `--instalar` (copia o código para `/opt/acessowifi`, `pip install` se `requirements.txt` mudou, units novas, testa `import app`, reinicia, confere `/login`; se falhar volta ao commit anterior). Nada é instalado sozinho: o botão "Atualizações" do painel grava `check`/`install` em `/var/lib/acessowifi-ctl` (única pasta gravável pelo serviço, `ReadWritePaths`) e as units `acessowifi-update.path`/`acessowifi-update-install.path` executam o script como root. O resultado vai para `/var/lib/acessowifi-ctl/status.json`, lido por `GET /api/updates`.
- `flask --app app check-fortigate [--escrita]` (`acessowifi testar`): diagnóstico de conexão, permissões e grupos. Rode-o primeiro quando algo não funcionar.

Regras:
- Tudo que entra na `master` aparece no botão "Atualizações" do servidor e é instalado com um clique. Antes de juntar, teste com `DEMO_MODE=1` (painel) e `deploy/configure.py --demo` (instalador).
- Uma versão nova precisa funcionar com o `.env` que já existe no servidor: variável nova sempre com valor padrão (`os.getenv("X", padrão)`), nunca `os.environ["X"]`. A instalação pelo botão não faz perguntas.
- Permissão nova no FortiGate (ex.: `wifi read-write`, `fwgrp-permission schedule`) não chega pelo botão de atualização: adicione-a ao perfil em `configure.py`, `.conf` e docs, e faça o painel detectar a falta (erro 403 da API) e mostrar "rode ./deploy/install.sh e refaça a configuração".
- Dados que o painel precisa guardar ficam no FortiGate ou em `/var/lib/acessowifi`, nunca dentro de `/opt/acessowifi`, que é sobrescrito a cada atualização e é somente leitura para o serviço.
- Hospedado on-premise; não adicionar dependências de nuvem/CDN.
- Todo usuário cadastrado tem validade obrigatória. O SSID principal não tem senha compartilhada; só os SSIDs extras podem ser WPA2 com senha ou abertos, por escolha do administrador na aba SSIDs.
- O instalador nunca altera o FortiGate sem mostrar o plano e receber confirmação, nem remove grupos já existentes do SSID.
- Escritas do painel no FortiGate só em grupos guest permitidos (`FGT_GUEST_GROUPS`) e em endereços com prefixo `acessowifi-` dentro de `FGT_MAC_GROUP`. Nunca alterar grupos SAML/Google, admins ou políticas.
- Rotas que alteram dados exigem o header `X-Requested-With: acessowifi` (proteção CSRF).
- Nunca expor `FGT_TOKEN` nem o campo `passwd` ao frontend. A senha gerada só é devolvida na resposta do cadastro ou da troca de senha.
- Os nomes de campos do FortiOS variam por versão: ao ajustar, valide contra a saída real do `curl` no FortiGate (veja o README).
