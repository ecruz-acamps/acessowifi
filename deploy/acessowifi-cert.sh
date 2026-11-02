#!/usr/bin/env bash
# Certificado HTTPS do painel, emitido por uma autoridade certificadora (CA) própria do servidor.
#
# A CA é instalada uma vez em cada aparelho (página /certificado) e é PERMANENTE: as atualizações
# não a refazem, mesmo se os IPs do servidor mudarem. Ela é restrita (nameConstraints) a endereços
# internos (redes privadas e nomes locais): mesmo com a chave dela, não dá para emitir certificado
# de sites da internet. Só o certificado do servidor, assinado por ela, acompanha os IPs atuais.
#
#   acessowifi-cert.sh            garante CA + certificado do servidor e recarrega o nginx
#   acessowifi-cert.sh --forcar   gera uma CA nova (os aparelhos precisam reinstalar o certificado)
set -euo pipefail
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

D=${ACESSOWIFI_SSL:-/etc/ssl/acessowifi}
CA_VERSION=2   # 1 = CA presa aos IPs exatos (refeita a cada troca de IP); 2 = permanente
HOST=$(hostname -s 2>/dev/null | tr "A-Z" "a-z"); HOST=${HOST:-servidor}   # nome curto: sempre dentro das restrições
# Redes privadas + 100.64.0.0/10 (endereços de VPN como Tailscale), nunca endereços da internet
PRIVATE='^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)'
IPS=${ACESSOWIFI_IPS:-$(hostname -I 2>/dev/null || true)}
IPS=$(echo "$IPS" | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -E "$PRIVATE" | sort -u | tr '\n' ' ' || true)
mkdir -p "$D"
chmod 755 "$D"

CHANGED=0
if [ "${1:-}" = "--forcar" ] || [ ! -s "$D/ca.pem" ] || [ ! -s "$D/ca.key" ] \
   || [ "$(cat "$D/ca-version" 2>/dev/null)" != "$CA_VERSION" ]; then
  [ -s "$D/ca.pem" ] && echo "Gerando o certificado permanente do servidor (reinstale-o nos aparelhos uma última vez)."
  # Só endereços internos: redes privadas e nomes locais. Sites da internet ficam de fora.
  CONSTRAINTS="permitted;IP:10.0.0.0/255.0.0.0,permitted;IP:172.16.0.0/255.240.0.0,permitted;IP:192.168.0.0/255.255.0.0"
  CONSTRAINTS+=",permitted;IP:100.64.0.0/255.192.0.0"
  CONSTRAINTS+=",permitted;DNS:local,permitted;DNS:lan,permitted;DNS:home.arpa,permitted;DNS:internal"
  [[ "$HOST" == *.* ]] || CONSTRAINTS+=",permitted;DNS:$HOST"
  openssl req -x509 -new -nodes -newkey rsa:3072 -sha256 -days 7300 \
    -subj "/O=Acampamento/CN=Acesso Wi-Fi - certificado do servidor $HOST" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,$CONSTRAINTS" \
    -keyout "$D/ca.key" -out "$D/ca.pem" 2>/dev/null
  echo "$CA_VERSION" > "$D/ca-version"
  rm -f "$D/cert.pem" "$D/scope"
  CHANGED=1
fi

# Certificado do servidor: acompanha o nome e os IPs atuais. Os aparelhos não percebem a troca,
# porque confiam na CA. Refeito se a lista mudar, se não for desta CA ou se faltar menos de 30 dias.
SCOPE="DNS:$HOST DNS:$HOST.local $(for ip in $IPS; do printf 'IP:%s ' "$ip"; done)"
SCOPE=$(echo "$SCOPE" | xargs)
if [ ! -s "$D/cert.pem" ] || [ "$(cat "$D/scope" 2>/dev/null)" != "$SCOPE" ] \
   || ! openssl verify -CAfile "$D/ca.pem" "$D/cert.pem" >/dev/null 2>&1 \
   || ! openssl x509 -in "$D/cert.pem" -noout -checkend $((30 * 86400)) >/dev/null 2>&1; then
  SAN=$(echo "$SCOPE" | tr ' ' ',')
  openssl req -new -nodes -newkey rsa:2048 -subj "/CN=$HOST" -keyout "$D/key.pem" -out "$D/server.csr" 2>/dev/null
  # 825 dias: limite aceito pelo iPhone/Safari
  openssl x509 -req -in "$D/server.csr" -CA "$D/ca.pem" -CAkey "$D/ca.key" -CAcreateserial -days 825 -sha256 \
    -extfile <(printf "subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n" "$SAN") \
    -out "$D/cert.pem" 2>/dev/null
  rm -f "$D/server.csr"
  echo "$SCOPE" > "$D/scope"
  CHANGED=1
fi

chmod 600 "$D/ca.key" "$D/key.pem"
chmod 644 "$D/ca.pem" "$D/cert.pem"

if [ "$CHANGED" = 1 ]; then
  echo "Certificado do painel pronto (vale para: $SCOPE)."
  if command -v nginx >/dev/null && [ -d /run/systemd/system ] && systemctl is-active -q nginx; then
    nginx -t -q && systemctl reload nginx
  fi
fi
