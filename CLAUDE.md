# acessowifi

Painel interno (Flask + HTML/JS puro) para o Wi-Fi de um acampamento: FortiGate 40F + FortiAP, FortiOS 7.2.13, SSID com captive portal que aceita usuário/senha **ou** SAML do Google Workspace.

- `fortigate.py`: cliente da API REST (`/api/v2/...`, Bearer token, cache de 10 s limpo a cada escrita).
- `app.py`: login da página, normalização e cruzamento dos dados, rotas:
  - `GET /api/clients` (wifi/client + user/firewall cruzados por IP; `source` = registered | google | mac | unknown | pending)
  - `GET /api/users`, `GET /api/groups`
  - `POST /api/users`, `PUT|DELETE /api/users/<grupo>/<id>`: contas **guest** com `expiration` (`YYYY/MM/DD HH:MM:SS`, hora local)
  - `GET|POST /api/devices`, `DELETE /api/devices/<mac>`: "permitir sempre" — endereço `type mac` (prefixo `acessowifi-`) no grupo `FGT_MAC_GROUP`, que é a exceção do captive portal. O vínculo com o usuário fica no `comment` do endereço (sem banco de dados). A validade segue a conta guest (`group=`) ou é própria (`exp=`), no caso de usuários do Google
  - `GET /api/aps`
- `flask cleanup-devices`: remove liberações vencidas e órfãs. Roda pelo systemd timer a cada 5 min.
- `mock_data.py`: FortiGate fictício e com estado, usado com `DEMO_MODE=1`.
- `static/`: frontend. Renderize sempre com `textContent`/DOM, nunca com `innerHTML`, porque hostname e usuário vêm dos dispositivos.
- `deploy/`: `install.sh` (instalador interativo; gera `/opt/acessowifi/.env`), `fortigate-setup.conf` (CLI para o FortiGate), unidades systemd e nginx.
- `flask --app app check-fortigate [--escrita]`: diagnóstico de conexão, permissões e grupos. Rode-o primeiro quando algo não funcionar.

Regras:
- Hospedado on-premise; não adicionar dependências de nuvem/CDN.
- Todo usuário cadastrado tem validade obrigatória. Não existe senha compartilhada.
- Escritas no FortiGate só em grupos guest permitidos (`FGT_GUEST_GROUPS`) e em endereços com prefixo `acessowifi-` dentro de `FGT_MAC_GROUP`. Nunca alterar grupos SAML/Google, admins ou políticas.
- Rotas que alteram dados exigem o header `X-Requested-With: acessowifi` (proteção CSRF).
- Nunca expor `FGT_TOKEN` nem o campo `passwd` ao frontend. A senha gerada só é devolvida na resposta do cadastro ou da troca de senha.
- Os nomes de campos do FortiOS variam por versão: ao ajustar, valide contra a saída real do `curl` no FortiGate (veja o README).
