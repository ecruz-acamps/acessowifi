# Histórico de versões

Cada versão nova aumenta o número depois do ponto (1.0, 1.1, 1.2...). O botão **Atualizações** do painel
mostra as versões disponíveis e o que cada uma traz, a partir deste arquivo.

## 1.10 — 2026-11-02
- Nova aba **Rede**: todos os aparelhos que o FortiGate enxerga, no Wi-Fi e no cabo (televisões, computadores, impressoras...), com IP, MAC, fabricante, sistema, por onde estão conectados e quando foram vistos pela última vez.
- Botões **Bloquear** e **Desbloquear**: o aparelho bloqueado fica sem internet e sem acesso às outras redes, pelo Wi-Fi ou pelo cabo, até ser desbloqueado. O painel não deixa bloquear o aparelho que você está usando nem o servidor do painel.
- Dá para dar nome aos aparelhos (ex.: "Televisão do refeitório").

## 1.9 — 2026-10-31
- Servidor: some do log o erro "Control server error: Read-only file system" que as versões novas do gunicorn mostravam a cada inicialização. O painel já funcionava normalmente; era só um aviso.

## 1.8 — 2026-10-31
- App (PWA): a tela "Sem conexão" só aparece quando o aparelho está realmente sem rede. Antes ela aparecia também quando o servidor recusava a conexão por causa do certificado (por exemplo, logo depois da troca para o certificado próprio do servidor), escondendo o aviso do navegador e impedindo até de abrir a página `/certificado`.

## 1.7 — 2026-10-31
- Novo comando `acessowifi permissoes` (no servidor, como root): dá ao token do painel as permissões que as versões novas precisam, como criar a rede própria dos SSIDs. Ele pede o admin do FortiGate, mostra o que vai mudar e aplica, sem refazer toda a configuração e sem trocar o token.
- A mensagem de "acesso recusado (403)" agora diferencia falta de permissão de uma função nova (o resto do painel funciona) de problema no token ou no trusted host (nada funciona).

## 1.6 — 2026-10-31
- O painel pode ser instalado como app (PWA) no celular e no computador: ícone próprio, abre em tela cheia, botão "Instalar app" no topo (no iPhone: Compartilhar → Adicionar à Tela de Início).
- O servidor passa a ter um certificado HTTPS próprio, que pode ser instalado nos aparelhos pela página `/certificado` (com passo a passo para Android, iPhone, Windows e Mac). Isso tira o aviso de "conexão não particular" e é o que permite instalar o app. Por segurança, o certificado só vale para o endereço deste servidor.
- Sem conexão com o servidor, o app mostra uma tela avisando, em vez de um erro do navegador. Nenhum dado do painel fica guardado no aparelho.

## 1.5 — 2026-10-31
- Aba SSIDs: ao criar um SSID com rede própria, o formulário pergunta tudo o que é preciso para ele funcionar, já preenchido com sugestões: faixa de IP, gateway, faixa do DHCP, DNS (FortiGate, Google, Cloudflare ou outro), tempo de concessão do IP, filtros de internet (iguais aos do principal ou só internet) e isolamento entre aparelhos. Não é preciso mexer no FortiGate.
- O painel confere as respostas antes de aplicar: faixa sobreposta a outra rede, gateway fora da faixa, DHCP incluindo o gateway etc.
- Na edição, DHCP, DNS, concessão, filtros e isolamento podem ser alterados; a faixa de IP fica fixa.

## 1.4 — 2026-10-31
- Aba SSIDs: quando o SSID principal está em modo túnel, cada SSID novo ganha **rede própria**: uma faixa de IP livre, servidor DHCP e uma política de internet copiada da política do SSID principal (mesma saída, NAT e perfis de segurança). Ao remover o SSID, tudo isso é apagado junto.
- A lista de SSIDs mostra a rede (faixa de IP) de cada SSID criado pelo painel.
- O token do FortiGate precisa poder criar políticas: rode o instalador e refaça a configuração uma vez.

## 1.3 — 2026-10-31
- Aba SSIDs: o botão "Novo SSID" fica sempre visível. Quando falta algo, ele aparece desativado e o aviso amarelo explica o quê.
- Aba SSIDs: mostra como o SSID principal está montado (bridge, software switch ou túnel) e se os SSIDs novos vão ter rede.
- Aba SSIDs: se o SSID principal não foi definido na instalação, dá para escolher na própria aba.

## 1.2 — 2026-10-31
- Aba SSIDs: corrigido o "Erro 500" ao abrir a aba. O FortiOS 7.4 devolve alguns campos (como o agendamento do SSID) em formato de lista, e o painel só entendia texto. Agora ele aceita os dois e grava no mesmo formato que o FortiGate usa.
- Erros inesperados aparecem na tela com o motivo, e não só "Erro 500"; o detalhe completo vai para o log (`acessowifi log`).

## 1.1 — 2026-10-31
- Validade das contas: o painel descobre sozinho o formato de data que o FortiGate aceita (no FortiOS 7.4.13 a validade estava sendo gravada como 0). Se nenhum formato funcionar, a conta não é criada e a mensagem mostra o que o FortiGate respondeu.
- Instalador: corrigido o erro "acessowifi-update.path does not exist", que impedia o painel de reiniciar no fim da instalação.
- Instalador: ao atualizar, avisa quando uma função nova (como a aba SSIDs) ainda precisa de configuração e oferece refazê-la.

## 1.0 — 2026-10-31
- Primeira versão numerada do painel.
- Cadastro de usuários do Wi-Fi com validade, nova senha e remoção.
- Lista de quem está conectado: conta cadastrada, Google Workspace, liberado por MAC ou aguardando login.
- "Permitir sempre": libera um aparelho pelo MAC, sem passar pelo captive portal.
- Aba SSIDs: SSIDs extras na mesma rede do principal, com acesso igual ao principal, WPA2 ou sem senha, agendamento e escolha de FortiAPs.
- Botão de atualizações: mostra as versões novas e instala com um clique, voltando à anterior se algo falhar.
- Instalador que configura o FortiGate e o servidor, e o comando `acessowifi` para diagnóstico e manutenção.
