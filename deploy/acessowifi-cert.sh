#!/usr/bin/env bash
# Certificado HTTPS do painel, emitido por uma autoridade certificadora (CA) própria do servidor.
#
# Com o certificado da CA instalado nos aparelhos (página /certificado do painel), o navegador
# confia no HTTPS do painel, e isso é o que permite instalar o painel como app (PWA).
# A CA é restrita (nameConstraints) aos IPs e ao nome deste servidor: mesmo com a chave dela,
# não dá para emitir certificado de outros sites.
#
# Pode rodar quantas vezes quiser: só refaz o que faltar ou estiver desatualizado.
#   acessowifi-cert.sh            garante CA + certificado do servidor e recarrega o nginx
#   acessowifi-cert.sh --forcar   gera tudo de novo (os aparelhos precisam reinstalar a CA)
set -euo pipefail
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

D=${ACESSOWIFI_SSL:-/etc/ssl/acessowifi}
HOST=$(hostname)
IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | sort -u | tr '\n' ' ')
IPS=${IPS:-127.0.0.1}
mkdir -p "$D"
chmod 755 "$D"

# Lista do que o certificado cobre (nome e IPs); se mudar, a CA e o certificado são refeitos
SCOPE="DNS:$HOST DNS:$HOST.local $(for ip in $IPS; do printf 'IP:%s ' "$ip"; done)"
SCOPE=$(echo "$SCOPE" | xargs)

CHANGED=0
if [ "${1:-}" = "--forcar" ] || [ ! -s "$D/ca.pem" ] || [ ! -s "$D/ca.key" ] || [ "$(cat "$D/scope" 2>/dev/null)" != "$SCOPE" ]; then
  [ -s "$D/ca.pem" ] && echo "O nome ou os IPs do servidor mudaram: gerando um certificado novo (reinstale-o nos aparelhos)."
  CONSTRAINTS=""
  for item in $SCOPE; do
    case "$item" in
      DNS:*) CONSTRAINTS+="permitted;${item},";;
      IP:*)  CONSTRAINTS+="permitted;${item}/255.255.255.255,";;
    esac
  done
  openssl req -x509 -new -nodes -newkey rsa:3072 -sha256 -days 3650 \
    -subj "/O=Acampamento/CN=Acesso Wi-Fi - certificado do servidor $HOST" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,${CONSTRAINTS%,}" \
    -keyout "$D/ca.key" -out "$D/ca.pem" 2>/dev/null
  echo "$SCOPE" > "$D/scope"
  rm -f "$D/cert.pem"
  CHANGED=1
fi

# Certificado do servidor: refeito se faltar, se não for desta CA ou se faltar menos de 30 dias
if [ ! -s "$D/cert.pem" ] || ! openssl verify -CAfile "$D/ca.pem" "$D/cert.pem" >/dev/null 2>&1 \
   || ! openssl x509 -in "$D/cert.pem" -noout -checkend $((30 * 86400)) >/dev/null 2>&1; then
  SAN=$(echo "$SCOPE" | tr ' ' ',')
  openssl req -new -nodes -newkey rsa:2048 -subj "/CN=$HOST" -keyout "$D/key.pem" -out "$D/server.csr" 2>/dev/null
  # 825 dias: limite aceito pelo iPhone/Safari
  openssl x509 -req -in "$D/server.csr" -CA "$D/ca.pem" -CAkey "$D/ca.key" -CAcreateserial -days 825 -sha256 \
    -extfile <(printf "subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n" "$SAN") \
    -out "$D/cert.pem" 2>/dev/null
  rm -f "$D/server.csr"
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
