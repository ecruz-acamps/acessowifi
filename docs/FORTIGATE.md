# Configuração do FortiGate para o painel Acesso Wi-Fi

Guia para FortiGate 40F com FortiAP e **FortiOS 7.4** (testado no formato do 7.4.13; também funciona no 7.2). Em geral **você não precisa fazer nada disto**: o instalador (`sudo ./deploy/install.sh`, modo automático) faz tudo por SSH e mostra cada mudança antes de aplicar. Use este guia para:

- configurar à mão (modo manual do instalador);
- entender o que o instalador mudou;
- resolver problemas (veja [Problemas comuns](#problemas-comuns)).

## O que o painel precisa no FortiGate

| Item | Para quê | Nome padrão |
|---|---|---|
| Grupo de usuários do tipo **guest** | Contas com validade criadas pelo painel | `WiFi-Cadastrados` |
| Grupo de endereços MAC | Aparelhos com "Permitir sempre" | `WiFi-MAC-Liberados` |
| Endereço MAC fictício | O FortiOS não aceita grupo vazio | `WiFi-MAC-placeholder` |
| Lista de exceção do captive portal | Aparelhos do grupo acima navegam sem login | `WiFi-Liberados` |
| SSID com o grupo guest e a lista de exceção | O portal aceitar as contas do painel | o seu SSID |
| Política para os MACs liberados | Só se a política atual exigir grupo de usuário | `acessowifi-mac-liberados` |
| Perfil de admin | Permissões mínimas do token | `api-acessowifi` |
| Admin de API com trusted host | O token que o painel usa | `acessowifi` |
| HTTPS de administração na interface | A API REST funciona por HTTPS | — |
| SSID principal num software switch (ou em bridge) | SSIDs extras na mesma rede do principal | o seu switch |

O painel se conecta **só pela API REST, com token**. Ele não guarda usuário nem senha de admin do FortiGate.

---

## Modo automático (instalador)

O instalador pede o usuário e a senha de um admin **super_admin**, que servem só durante a instalação e não são salvos. Depois ele:

1. entra por SSH e lê a configuração atual (versão, SSIDs, grupos, políticas, interfaces);
2. pergunta qual SSID usa o captive portal e se você quer tempo máximo de sessão;
3. **mostra a lista de mudanças** (e, se quiser, os comandos exatos) e pede confirmação;
4. aplica só o que falta. O que já existe não é recriado, e os grupos atuais do SSID (como o do Google) são mantidos;
5. gera o token e testa a API com ele.

Requisitos:
- SSH habilitado na interface que o servidor alcança (*Network > Interfaces > Administrative Access: SSH*);
- FortiGate sem VDOMs (o padrão no 40F).

Se algum requisito faltar, o instalador avisa e oferece o modo manual.

---

## Modo manual, passo a passo

Os mesmos comandos estão prontos em `deploy/fortigate-setup.conf`: troque o IP do servidor e cole na CLI (*GUI > ícone `>_` no topo*, ou SSH). Abaixo, cada parte explicada, pela GUI e pela CLI.

### 1. Grupo de visitantes (contas do painel)

**GUI:** *User & Authentication > User Groups > Create New*
- Name: `WiFi-Cadastrados`
- Type: **Guest**
- User ID: **Specify**; Password: **Specify**
- Enable Name, Email, SMS (telefone); Company e Sponsor: **Optional**
- Expiration: **Immediately** (a validade de cada conta vem do painel)

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

### 2. Grupo de endereços para "Permitir sempre"

**GUI:** *Policy & Objects > Addresses > Create New > Address*
- Name: `WiFi-MAC-placeholder`, Type: **MAC Address**, MAC: `02:00:00:00:00:00`, comentário "Não remover"

Depois, em *Create New > Address Group*: Name `WiFi-MAC-Liberados`, Members `WiFi-MAC-placeholder`.

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
```
> Não remova o `WiFi-MAC-placeholder`. Os aparelhos que o painel liberar entram nesse grupo como `acessowifi-<mac>`.

### 3. SSID: grupo guest e lista de exceção

**GUI:** *WiFi & Switch Controller > SSIDs > (seu SSID) > Edit*
- **Captive Portal** ligado. No 7.4 é uma opção própria (`set captive-portal enable`); no 7.2 era um *Security Mode*
- **User Groups**: adicione `WiFi-Cadastrados` e **mantenha** o grupo do Google
- **Exempt Sources**: `WiFi-MAC-Liberados`

Pela CLI, crie a lista de exceção e ligue ao SSID. `append` adiciona o grupo sem tirar os atuais:
```
config user security-exempt-list
    edit "WiFi-Liberados"
        config rule
            edit 0
                set srcaddr "WiFi-MAC-Liberados"
            next
        end
    next
end
config wireless-controller vap
    edit "NOME_DO_SSID"
        append selected-usergroups "WiFi-Cadastrados"
        set security-exempt-list "WiFi-Liberados"
    next
end
```
> Se o SSID já tiver uma `security-exempt-list`, não troque: adicione a regra com `srcaddr "WiFi-MAC-Liberados"` na lista que já existe. Confira com `show wireless-controller vap NOME_DO_SSID`.

### 4. Política de firewall (só se precisar)

Veja a política do SSID para a internet (*Policy & Objects > Firewall Policy*). Se o campo **Source** tiver **grupo de usuário**, os aparelhos liberados por MAC não passam, porque não fazem login. Nesse caso:

- clone a política (botão direito > *Clone*);
- na cópia, Source = `WiFi-MAC-Liberados`, **sem** usuário ou grupo;
- arraste a cópia para **cima** da original.

Se a política não tem grupo na origem (o comum quando o portal é configurado no SSID), não precisa fazer nada.

### 5. Tempo máximo de sessão (recomendado)

Quando uma conta vence ou é removida, quem já estava logado continua navegando até a sessão expirar. Com *hard timeout*, todos precisam fazer login de novo a cada X horas; aparelhos com "Permitir sempre" não são afetados. Por padrão, o FortiGate desconecta após 5 minutos **sem uso**.

```
config user setting
    set auth-timeout 240
    set auth-timeout-type hard-timeout
end
```

### 6. HTTPS de administração na interface

A API funciona pelo mesmo HTTPS da interface web de administração. Na interface pela qual o servidor do painel alcança o FortiGate:

**GUI:** *Network > Interfaces > (interface) > Administrative Access: HTTPS*

```
config system interface
    edit "internal"
        append allowaccess https
    next
end
```
Se a porta HTTPS de administração não for 443 (*System > Settings > HTTPS port*), informe-a no instalador, por exemplo `192.168.1.99:8443`.

### 7. Perfil e admin da API (o token)

**Perfil** — *System > Admin Profiles > Create New*, nome `api-acessowifi`:
- *User & Authentication*: **Read/Write**
- *WiFi & Switch*: **Read/Write** (criar SSIDs e escolher em quais FortiAPs aparecem)
- *Firewall*: **Custom** → *Address*: **Read/Write**, *Policy*: **Read/Write** (políticas dos SSIDs extras), *Schedule*: **Read/Write**, o resto **None**
- *Network*: **Custom** → *Configuration*: **Read/Write** (IP e DHCP dos SSIDs extras, ou incluí-los no software switch), o resto **None**
- todo o resto: **None**

**Admin** — *System > Administrators > Create New > REST API Admin*:
- Username: `acessowifi`
- Administrator profile: `api-acessowifi`
- PKI Group: desligado
- **Trusted Hosts**: o IP do servidor do painel, com máscara `/32`
- Ao clicar OK, o FortiGate mostra o **token** uma única vez. Copie e cole no instalador (modo manual).

```
config system accprofile
    edit "api-acessowifi"
        set authgrp read-write
        set wifi read-write
        set fwgrp custom
        config fwgrp-permission
            set address read-write
            set policy read-write
            set schedule read-write
        end
        set netgrp custom
        config netgrp-permission
            set cfg read-write
        end
    next
end
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
> **`set vdom "root"`:** no FortiOS 7.4 essa linha é **obrigatória**, mesmo sem VDOMs; sem ela a API responde `403` ("wrong vdom"). No 7.2 sem VDOMs o campo não existe e a linha dá *parse error*; apague-a. O instalador trata as duas versões sozinho.
>
> O painel envia o token no cabeçalho `Authorization`, que é o jeito exigido a partir do 7.4.5 (o token na URL foi desativado por padrão). Não é preciso habilitar `rest-api-key-url-query`.

### 8. Mesmo horário

A validade das contas é gravada na **hora local**. O FortiGate (*System > Settings > Time zone*, de preferência com NTP) e o servidor do painel precisam estar no mesmo fuso. O instalador compara os relógios e avisa se estiverem diferentes.

---

## SSIDs extras (aba SSIDs)

A aba **SSIDs** do painel cria redes Wi-Fi adicionais, por exemplo "Acampamento-Palestra", só no AP do auditório e só durante o evento:

- **Acesso**, escolhido em cada SSID:
  - **Igual ao principal:** copia o captive portal, os grupos (contas cadastradas e Google) e a lista de exceção;
  - **WPA2 com senha:** `security wpa2-only-personal`, com a senha definida no painel. Ela fica só no FortiGate e não volta para o navegador;
  - **Sem senha:** rede aberta. Ela fica na mesma rede do principal, então pode dar internet **sem login** se o captive portal estiver configurado no SSID e não no software switch.
- **Quando fica no ar:** num período (início e fim), ligado até você desligar, ou desligado. O painel usa o **agendamento nativo do SSID** (campo `schedule` em `config wireless-controller vap`) com agendamentos únicos do firewall (`aw-<nome>`), então **o FortiGate liga e desliga sozinho**, mesmo com o painel fora do ar. "Desligado" aponta para o agendamento vencido `aw-desligado`.
- **Em quais FortiAPs:** cada AP passa a ter a própria lista de SSIDs por rádio (*Override* em *Managed FortiAPs*). Depois de cada mudança, o painel confere se o **SSID principal continua em todos os APs onde estava**; se não, desfaz a mudança.
- O painel só altera ou remove SSIDs criados por ele (nome interno começando com `aw-`). O principal e os outros SSIDs aparecem na lista, mas não podem ser alterados pela aba.

### Rede dos SSIDs extras

Depende de como o SSID principal está montado. O instalador, o `acessowifi testar` e a própria aba dizem qual é o caso:

| SSID principal | Rede de cada SSID novo |
|---|---|
| **Túnel** (o padrão), fora de software switch | **Rede própria**, criada pelo painel: uma faixa `/24` livre de `10.250.0.0/16` (por exemplo `10.250.2.0/24`, gateway `.1`), servidor DHCP (`.10` a `.250`, DNS do FortiGate) e uma política de internet `aw-<nome>` **copiada da política do SSID principal** (mesma saída, NAT e perfis de segurança), colocada logo acima dela |
| **Software switch** | Entra no mesmo switch: mesma rede, DHCP e políticas do principal |
| **Bridge** (*local bridging*) | Mesma VLAN do principal |

- A faixa pode ser trocada com `FGT_SSID_NETWORKS` no `.env` (padrão `10.250.0.0/16`). O painel pula faixas já usadas por qualquer interface ou DHCP.
- Na rede própria, o acesso "igual ao principal" mantém os grupos de usuário da política. "WPA2" e "sem senha" ficam sem grupos, senão ninguém navegaria.
- Ao remover o SSID, o painel apaga junto a política, o DHCP e a interface dele.
- Na rede própria, a política modelo é a primeira política que aceita tráfego com origem no SSID principal. Se ela não existir, a aba avisa e não cria SSIDs.

## Como conferir

No servidor, como root:
```bash
acessowifi testar --escrita
```
Ele testa a conexão, as permissões e os grupos. Também cria e apaga um usuário e um endereço de teste. Cada linha mostra `[OK]` ou `[ERRO]` com o motivo.

## Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `recusou o acesso (401)` | Token errado ou regenerado | Gere outro (`execute api-user generate-key acessowifi`) e rode o instalador de novo |
| `recusou o acesso (403)` | IP do servidor fora do *trusted host*, ou perfil sem permissão | Confira o trusted host (passo 7); o IP tem de ser o que o FortiGate vê, não um IP de NAT |
| `Falha ao conectar` / timeout | HTTPS de administração desligado na interface, porta errada ou rota | Passo 6; teste com `curl -k https://IP_DO_FORTIGATE` a partir do servidor |
| `grupo guest não encontrado` | Grupo não criado, com outro nome ou de tipo diferente | Passo 1 (Type: Guest) |
| `Grupo de endereços ... não existe` | Passo 2 não feito | Passo 2 |
| "não gravou a validade em nenhum formato conhecido" | O FortiGate não aceitou a data em nenhum dos formatos que o painel tenta. Desde a versão 1.1, o painel detecta o formato sozinho (no 7.4.13 o formato antigo virava `0`) | Mande a mensagem completa, que lista o que o FortiGate gravou em cada tentativa |
| Conta cadastrada não consegue entrar no portal | Grupo guest fora dos *User Groups* do SSID | Passo 3 |
| Conta vencida continua navegando | Sessão ainda ativa | Passo 5 (hard timeout) |
| Aparelho "Permitir sempre" continua caindo no portal | Lista de exceção fora do SSID; MAC aleatório mudou | Passo 3; veja o aviso de MAC aleatório no painel |
| Aparelho liberado conecta mas não navega | Política exige grupo de usuário | Passo 4 |
| Validade adiantada ou atrasada algumas horas | Fusos diferentes | Passo 8 |
| Aba SSIDs: "o token ainda não tem essa permissão" / 403 | Instalação feita antes da aba SSIDs | `./deploy/install.sh`, responda **sim** para refazer a configuração (o perfil ganha as permissões novas) |
| Aba SSIDs: "Não achei a política de firewall que dá internet ao SSID" | O SSID principal em túnel não tem política com origem nele (ex.: a política usa um zone) | Veja [SSIDs extras](#ssids-extras-aba-ssids) |
| Modo automático: `Permission denied` / `Command fail` | Admin informado não é super_admin | Use um admin com perfil super_admin |
| Modo automático: "usa VDOMs" | VDOMs habilitados | Use o modo manual, com os comandos dentro de `config vdom` / `config global` |
| `command parse error before 'vdom'` no admin de API | FortiOS 7.2 sem VDOMs não tem esse campo | Apague a linha `set vdom "root"` (só no 7.2) |
| `recusou o acesso (403)` logo após atualizar para o 7.4 | Admin de API sem `set vdom "root"` (bug conhecido do 7.4.5, corrigido no 7.4.6) ou permissões ajustadas pela atualização | `config system api-user` → `edit "acessowifi"` → `set vdom "root"`; se continuar, gere um token novo e rode o instalador de novo |

## Como desfazer

Para remover o painel do FortiGate, primeiro apague os aparelhos liberados pela aba **Aparelhos liberados** do painel. Depois:
```
config system api-user
    delete "acessowifi"
end
config system accprofile
    delete "api-acessowifi"
end
config wireless-controller vap
    edit "NOME_DO_SSID"
        unselect selected-usergroups "WiFi-Cadastrados"
        unset security-exempt-list
    next
end
config firewall policy
    delete <id da política acessowifi-mac-liberados, se criada>
    delete <id de cada política aw-*, dos SSIDs extras>
end
config user security-exempt-list
    delete "WiFi-Liberados"
end
config firewall addrgrp
    delete "WiFi-MAC-Liberados"
end
config firewall address
    delete "WiFi-MAC-placeholder"
end
config user group
    delete "WiFi-Cadastrados"
end
```

## Trocar o token

Se o token vazar ou você quiser renová-lo: `execute api-user generate-key acessowifi` gera um novo e invalida o anterior. Depois, no servidor, rode `sudo ./deploy/install.sh`, responda **sim** para refazer a configuração e escolha o modo manual para colar o token novo.
