# Histórico de versões

Cada versão nova aumenta o número depois do ponto (1.0, 1.1, 1.2...). O botão **Atualizações** do painel
mostra as versões disponíveis e o que cada uma traz, a partir deste arquivo.

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
