# Configuração do gunicorn (lida automaticamente da pasta do painel).
# O gunicorn 26+ cria um "control socket" na pasta do painel, que é somente leitura por segurança,
# e registra "Control server error: Read-only file system" a cada inicialização. O painel não usa
# esse recurso. Versões antigas ignoram esta opção, então ela é segura em qualquer versão.
control_socket_disable = True
