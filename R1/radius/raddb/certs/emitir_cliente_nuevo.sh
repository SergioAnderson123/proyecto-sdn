#!/bin/bash
# Emite UN certificado de cliente EAP-TLS nuevo con la CA que ya existe, sin regenerar nada.
#
#   ./emitir_cliente_nuevo.sh <usuario> <carpeta_de_salida> [dias]      p. ej.  ./emitir_cliente_nuevo.sh superadmin01 /dev/shm/superadmin-certs
#
# Por que existe: `regenerar.sh` borra y rehace la CA y TODOS los certificados; para añadir una sola identidad (el equipo `superadmin`, docs/PUESTA_EN_VIVO_SUPERADMIN.md)
# eso rompería las demas. Este script trabaja en una COPIA de esta carpeta (ca.key, ca.pem, index.txt, serial...) y no modifica nada de aqui ni toca RADIUS: el servidor ya
# confia en la CA, asi que un certificado nuevo firmado por ella entra sin cambiar su configuracion (la lista de revocacion ca.crl no lo incluye).
#
# Salida en <carpeta_de_salida> (permisos 0700/0600):
#   <usuario>@example.org.pem   certificado + clave privada CIFRADA (el formato que usa wpa_supplicant, igual que los demas)
#   <usuario>@example.org.p12   lo mismo en PKCS#12
#   <usuario>@example.org.pass  la contraseña de la clave, ALEATORIA (no la de laboratorio de los demas)
#   ca-db/                      index.txt, serial y el .pem numerado nuevo: la parte PUBLICA de la base de la CA, por si se quiere mantener al dia en el repositorio
# La clave privada NO debe versionarse (ver .gitignore). Se rechaza un usuario que ya figure como valido en index.txt.
set -euo pipefail
export LC_ALL=C
[ $# -ge 2 ] || { sed -n '2,3p;5p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
USUARIO="$1"; SALIDA="$2"; DIAS="${3:-365}"
[[ "$USUARIO" =~ ^[a-z][a-z0-9_.-]{1,40}$ ]] || { echo "usuario no valido: $USUARIO (minusculas, digitos, _ . -)"; exit 2; }
CN="$USUARIO@example.org"
AQUI="$(cd "$(dirname "$0")" && pwd)"
PW_CA="${PW_CA:-whatever}"        # contraseña de la clave de la CA de laboratorio (la misma que usa regenerar.sh)

for f in ca.key ca.pem ca.cnf client.cnf xpextensions index.txt serial; do
  [ -f "$AQUI/$f" ] || { echo "falta $AQUI/$f: ejecutar desde el repositorio con la CA en esta carpeta"; exit 1; }
done
if grep -qE "^V\s.*CN=$CN(/|\s|$)" "$AQUI/index.txt"; then
  echo "ya existe un certificado valido para $CN en index.txt; no se emite otro (revocarlo primero si hay que reemplazarlo)"; exit 1
fi

TRABAJO="$(mktemp -d)"; trap 'rm -rf "$TRABAJO"' EXIT
cp -r "$AQUI"/. "$TRABAJO"/ && cd "$TRABAJO"
rm -f ./*.csr ./*.key.new 2>/dev/null || true
PW_CLIENTE="$(openssl rand -base64 24 | tr -d '=+/\n' | cut -c1-24)"
SERIE="$(tr -d '\r\n' < serial)"

sed "s|^emailAddress[[:space:]]*=.*@.*|emailAddress = $CN|; s|^commonName[[:space:]]*=.*@.*|commonName = $CN|; \
     s|^input_password.*|input_password = $PW_CLIENTE|; s|^output_password.*|output_password = $PW_CLIENTE|" client.cnf > .cnf_tmp
grep -q "commonName *= *$CN" .cnf_tmp || { echo "client.cnf no tiene la linea commonName esperada: no se puede fijar el CN"; exit 1; }

openssl req -new -out nuevo.csr -keyout nuevo.key -config ./.cnf_tmp
openssl ca -batch -keyfile ca.key -cert ca.pem -in nuevo.csr -passin pass:"$PW_CA" -out nuevo.crt \
  -extensions xpclient_ext -extfile xpextensions -config ./.cnf_tmp -days "$DIAS"
openssl pkcs12 -export -in nuevo.crt -inkey nuevo.key -out nuevo.p12 -passin pass:"$PW_CLIENTE" -passout pass:"$PW_CLIENTE"
openssl pkcs12 -in nuevo.p12 -out nuevo.pem -passin pass:"$PW_CLIENTE" -passout pass:"$PW_CLIENTE"

openssl verify -CAfile ca.pem nuevo.pem | grep -q ": OK" || { echo "el certificado emitido no verifica contra la CA"; exit 1; }
openssl x509 -in nuevo.pem -noout -subject | grep -q "CN *= *$CN" || { echo "el CN del certificado no es $CN"; exit 1; }

umask 077
mkdir -p "$SALIDA/ca-db" && chmod 700 "$SALIDA" "$SALIDA/ca-db"
cp nuevo.pem "$SALIDA/$CN.pem"; cp nuevo.p12 "$SALIDA/$CN.p12"
printf '%s' "$PW_CLIENTE" > "$SALIDA/$CN.pass"
cp index.txt serial "$SALIDA/ca-db/"; [ -f "$SERIE.pem" ] && cp "$SERIE.pem" "$SALIDA/ca-db/"
chmod 600 "$SALIDA/$CN".* "$SALIDA"/ca-db/*

echo "OK: $CN  serie $SERIE  valido hasta $(openssl x509 -in nuevo.pem -noout -enddate | cut -d= -f2)"
echo "    huella SHA-256: $(openssl x509 -in nuevo.pem -noout -fingerprint -sha256 | cut -d= -f2)"
echo "    salida: $SALIDA/$CN.{pem,p12,pass} (clave privada: NO versionar)"
