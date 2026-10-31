# Acesso Wi-Fi — painel para FortiGate 40F

Painel interno para o Wi-Fi do acampamento (FortiGate 40F + FortiAP, captive portal com usuário/senha ou Google Workspace, FortiOS 7.4, compatível com 7.2):

- **Cadastrar usuários** de acesso à internet, sempre com **validade**. O FortiGate bloqueia o login sozinho quando a conta vence.
- Renovar validade, gerar nova senha, remover acesso.
- **Permitir sempre** um aparelho: o celular é liberado pelo MAC e não passa mais pelo captive portal.
- Ver **quem está conectado agora**: conta cadastrada, Google Workspace (SAML), liberado por MAC ou ainda sem login.
- **SSIDs extras**: criar redes com o mesmo acesso e a mesma rede do SSID principal, em FortiAPs escolhidos, num período (início e fim) ou ligadas e desligadas na hora. O FortiGate cuida do horário sozinho.
- Ver os FortiAPs e quantos clientes cada um tem.

Roda no seu servidor, sem nada na nuvem.

```
Navegador ──HTTPS──> nginx ──> painel (Flask) ──API REST + token──> FortiGate 40F ──> FortiAPs
```

## Instalação

Em um servidor Debian ou Ubuntu, na mesma rede do FortiGate:

```bash
git clone https://github.com/ecruz-acamps/acessowifi
cd acessowifi
./deploy/install.sh
```

Rode como **root**: `su -` (com o hífen) e depois o comando, ou `sudo ./deploy/install.sh` se o servidor tiver sudo.

O instalador pergunta **tudo** o que é necessário e deixa o painel funcionando:

| Etapa | O que acontece |
|---|---|
| FortiGate | IP do FortiGate e modo de configuração (abaixo) |
| Fuso horário | O mesmo do FortiGate; a validade usa a hora local |
| Login do painel | Usuário e senha para entrar no painel |
| Acesso | HTTPS com nginx (recomendado, certificado autoassinado) ou HTTP numa porta |
| Final | Instala tudo, ativa os serviços, libera a porta no `ufw` (se ativo) e faz um teste completo com o FortiGate |

### Configuração do FortiGate

- **Automática (recomendada):** você informa um admin do FortiGate (super_admin). O instalador entra por SSH, lê a configuração e pergunta qual SSID usa o captive portal. Depois **mostra a lista de mudanças** e só aplica com a sua confirmação. Ele cria o grupo de visitantes, a lista de exceção para "Permitir sempre", o perfil e o admin da API, e gera o token. A senha do admin **não é salva**.
- **Manual:** você configura seguindo **[docs/FORTIGATE.md](docs/FORTIGATE.md)** (GUI ou CLI) e cola o token no instalador.

### Onde fica a conexão com o FortiGate?

O painel **não usa usuário e senha** de admin do FortiGate. Ele usa a **API REST com um token** de um admin de API (`acessowifi`), com só as permissões necessárias e aceito só a partir do IP do servidor. IP e token ficam em `/opt/acessowifi/.env`, legível só pelo root e pelo serviço, e nunca vão para o navegador. O usuário e a senha que você escolhe no instalador são só para entrar no painel.

### Depois de instalado

| Para | Comando |
|---|---|
| Ver a situação do painel | `acessowifi status` |
| Descobrir por que o painel não sobe | `acessowifi diagnostico` |
| Trocar o usuário ou a senha do painel (esqueceu a senha) | `acessowifi senha` |
| Atualizar o painel | `git pull && ./deploy/install.sh` (mantém a configuração) |
| Mudar FortiGate, token, senha ou acesso | `./deploy/install.sh` e responda **sim** para refazer a configuração |
| Testar a conexão com o FortiGate | `acessowifi testar --escrita` |
| Ver o log (inclui quem cadastrou, alterou ou removeu o quê) | `acessowifi log` |

Se algo não funcionar, veja **[Problemas comuns](docs/FORTIGATE.md#problemas-comuns)**.

**Se o nginx não iniciar:** normalmente é porta ocupada por outro programa (Apache, outro painel). O instalador detecta isso: se a 443 estiver em uso, oferece outra porta (ex.: `https://IP:8443`); se a 80 estiver em uso, desativa o site padrão do nginx. Se mesmo assim falhar, ele mostra o motivo e oferece seguir sem nginx, com o painel em HTTP na porta 8080. Para ver o motivo à mão: `journalctl -u nginx -n 20` e `ss -ltnp | grep -E ':(80|443) '`.

**Se aparecer "502 Bad Gateway":** o nginx está funcionando, mas o painel por trás dele não respondeu. Normalmente a porta interna (8080) está ocupada por outro programa ou o serviço caiu. O instalador agora escolhe uma porta livre e só termina depois de confirmar que o painel responde; rode-o de novo (responda **não** para refazer a configuração). Para ver o motivo à mão: `acessowifi status` e `journalctl -u acessowifi -n 50`.

Não publique o painel na internet. Para usar um certificado próprio no lugar do autoassinado, substitua `/etc/ssl/acessowifi/cert.pem` e `key.pem` e rode `systemctl reload nginx`.

## Atualização automática

Se você aceitou na instalação, o servidor consulta o GitHub **a cada 5 minutos** e instala sozinho cada commit novo da branch `master`:

1. baixa o código e instala as bibliotecas novas, se `requirements.txt` mudou;
2. **testa se o painel carrega** antes de reiniciar;
3. reinicia e confirma que o painel responde;
4. **se algo falhar, volta para a versão anterior** e não tenta de novo aquele commit; o próximo commit é tentado normalmente.

A configuração (`.env`: FortiGate, token, senha) nunca é tocada.

| Para | Comando (como root) |
|---|---|
| Ver a versão instalada e o histórico | `acessowifi atualizacoes` |
| Atualizar agora, sem esperar os 5 minutos | `acessowifi atualizar` |
| Ligar ou desligar | `acessowifi auto-atualizar liga` / `desliga` |

Cuidados:
- **Quem pode enviar commits para o repositório controla o servidor.** Use autenticação em dois fatores no GitHub e só dê acesso de escrita a quem você confia.
- Faça as melhorias numa branch e só junte na `master` quando estiverem testadas (`DEMO_MODE=1`, abaixo).
- Funções que precisam de **permissões novas no FortiGate** (por exemplo, criar SSIDs) exigem rodar `./deploy/install.sh` uma vez, respondendo **sim** para refazer a configuração. A atualização automática não altera o FortiGate.
- Repositório **privado**: o servidor precisa de credencial de leitura (deploy key) para consultar o GitHub.

## Como funciona

**Cadastro.** Cada pessoa vira uma conta de visitante (guest) no grupo `WiFi-Cadastrados`, com data e hora de validade. A senha aparece uma única vez após o cadastro. Se ficar em branco, o painel gera uma de 8 caracteres, fácil de digitar no celular. O painel só altera esse grupo e nunca mexe no grupo do Google nem nos admins.

**Permitir sempre.** Na aba *Conectados agora*, o botão aparece em quem já fez login. O MAC do aparelho vira um endereço `acessowifi-<mac>` no grupo `WiFi-MAC-Liberados`, que está na lista de exceção do captive portal. Se a pessoa tem conta, a liberação acompanha a validade dela; para quem entra pelo Google, você escolhe a data. Liberações vencidas são removidas a cada 5 minutos por um timer do systemd.

Limitações do "Permitir sempre":
- **MAC aleatório:** iPhone e Android usam "endereço privado". Ele costuma ser fixo por rede, mas muda se a pessoa esquecer a rede, e no iOS 18 pode ficar no modo rotativo. Aí o aparelho volta para o portal. O painel marca esses aparelhos; para liberar de vez, desative o endereço privado dessa rede no celular.
- **MAC pode ser clonado:** use para aparelhos de confiança.
- O tráfego desses aparelhos não fica associado ao usuário nos logs do FortiGate, porque eles não fazem login.

**Conectados.** O painel cruza os clientes dos FortiAPs com as sessões do captive portal pelo IP e com os aparelhos liberados pelo MAC.

## Testar sem o FortiGate

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DEMO_MODE=1 COOKIE_SECURE=false SECRET_KEY=x APP_USER=admin \
APP_PASSWORD_HASH="$(.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g('teste'))")" \
.venv/bin/python app.py
# http://127.0.0.1:8080  (admin / teste). As alterações ficam só na memória.
```

O assistente de configuração também tem um modo simulado: `.venv/bin/python deploy/configure.py --env /tmp/teste.env --demo`.

## Estrutura

| Arquivo | Conteúdo |
|---|---|
| `app.py` | Painel (rotas, login, regras de cadastro) e comandos `check-fortigate` e `cleanup-devices` |
| `fortigate.py` | Cliente da API REST do FortiOS |
| `ssids.py` | SSIDs extras: criação, agendamento e FortiAPs |
| `static/` | Páginas e JavaScript |
| `deploy/install.sh` | Instalador |
| `deploy/acessowifi-update.sh` | Atualização automática pelo GitHub (timer a cada 5 min) |
| `deploy/acessowifi-cli.sh` | Comando `acessowifi` (status, testar, log, senha, atualizar...) |
| `deploy/configure.py` | Assistente de perguntas e configuração automática do FortiGate |
| `deploy/fortigate-setup.conf` | Comandos de CLI para o modo manual |
| `docs/FORTIGATE.md` | Guia do FortiGate: cada item explicado e problemas comuns |
| `mock_data.py` | FortiGate simulado (`DEMO_MODE=1`) |
