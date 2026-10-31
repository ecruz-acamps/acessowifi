# acessowifi

Painel interno (Flask + HTML/JS puro) que lê, **somente leitura**, a API REST de um FortiGate/FortiWiFi 40F e mostra os usuários cadastrados, os clientes Wi-Fi conectados e os APs.

- `fortigate.py`: cliente da API (`/api/v2/...`, Bearer token, cache de 10 s).
- `app.py`: login da página, normalização e cruzamento dos dados, rotas `/api/clients`, `/api/users`, `/api/aps`.
- `mock_data.py`: dados fictícios usados com `DEMO_MODE=1`.
- `static/`: frontend. Renderize sempre com `textContent`/DOM, nunca com `innerHTML`, porque hostname e usuário vêm dos dispositivos.
- `deploy/`: unidade systemd e exemplo de nginx.

Regras:
- Hospedado on-premise; não adicionar dependências de nuvem/CDN.
- Não escrever no FortiGate: o token tem perfil read-only.
- Nunca expor `FGT_TOKEN` nem o campo `passwd` dos usuários ao frontend.
- Os nomes de campos do FortiOS variam por versão: ao ajustar, valide contra a saída real do `curl` no FortiGate.
