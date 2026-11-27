#!/bin/bash
# Regenera TODOS los certificados de EAP-TLS: la CA, el certificado del servidor RADIUS y el de cada usuario.
# Equivale a `make destroycerts && make` mas los certificados de cliente, sin necesitar `make` (los .cnf de FreeRADIUS
# traen default_days = 60, por eso caducan a los 2 meses).
#
#   ./regenerar.sh [dias]        dias de validez de todo (por defecto 365)
#
# Despues: reconstruir la imagen de RADIUS (docker compose build radius && docker compose up -d radius) y repartir los
# certificados a los hosts (ansible-playbook ... hosts.yml). Las claves privadas conservan sus contrasenas de laboratorio.
set -e
export LC_ALL=C
cd "$(dirname "$0")"
DIAS="${1:-365}"
PW_CA=whatever
SUPP=../../../shared/mininet/supplicants/certs
USUARIOS="profesor01:professor_password profesor02:professor_password alumno01:student_password alumno02:student_password alumno03:student_password support01:support_password"
[ -f ca.cnf ] && [ -f server.cnf ] && [ -f client.cnf ] || { echo "Ejecutar desde R1/radius/raddb/certs (faltan los .cnf)"; exit 1; }

echo "[*] Borrando los certificados anteriores (se conservan dh, los .cnf, xpextensions y los README)"
rm -f ./*.csr ./*.crt ./*.p12 ./*.der ./*.key ./*.pem ./index.txt* ./serial* ./ca.crl ./*.old ./.cnf_tmp
touch index.txt; echo 01 > serial
[ -f dh ] || openssl dhparam -out dh -2 2048

echo "[*] CA ($DIAS dias)"
openssl req -new -x509 -keyout ca.key -out ca.pem -days "$DIAS" -config ./ca.cnf -passin pass:$PW_CA -passout pass:$PW_CA
openssl x509 -inform PEM -outform DER -in ca.pem -out ca.der
openssl ca -gencrl -keyfile ca.key -cert ca.pem -config ./ca.cnf -out ca-crl.pem -passin pass:$PW_CA -crldays "$DIAS"
openssl crl -in ca-crl.pem -outform der -out ca.crl; rm -f ca-crl.pem

echo "[*] Servidor RADIUS"
openssl req -new -out server.csr -keyout server.key -config ./server.cnf
openssl ca -batch -keyfile ca.key -cert ca.pem -in server.csr -passin pass:$PW_CA -out server.crt \
  -extensions xpserver_ext -extfile xpextensions -config ./server.cnf -days "$DIAS"
openssl pkcs12 -export -in server.crt -inkey server.key -out server.p12 -passin pass:whatever -passout pass:whatever
openssl pkcs12 -in server.p12 -out server.pem -passin pass:whatever -passout pass:whatever

emitir_cliente() {   # $1 = nombre de archivo, $2 = correo/CN, $3 = contrasena de la clave
  sed "s|^emailAddress[[:space:]]*=.*@.*|emailAddress = $2|; s|^commonName[[:space:]]*=.*@.*|commonName = $2|; \
       s|^input_password.*|input_password = $3|; s|^output_password.*|output_password = $3|" client.cnf > .cnf_tmp
  openssl req -new -out "$1.csr" -keyout "$1.key" -config ./.cnf_tmp
  openssl ca -batch -keyfile ca.key -cert ca.pem -in "$1.csr" -passin pass:$PW_CA -out "$1.crt" \
    -extensions xpclient_ext -extfile xpextensions -config ./.cnf_tmp -days "$DIAS"
  openssl pkcs12 -export -in "$1.crt" -inkey "$1.key" -out "$1.p12" -passin pass:"$3" -passout pass:"$3"
  openssl pkcs12 -in "$1.p12" -out "$1.pem" -passin pass:"$3" -passout pass:"$3"
}

for par in $USUARIOS; do
  u="${par%%:*}"; pw="${par##*:}"
  echo "[*] Cliente $u@example.org"
  emitir_cliente "$u@example.org" "$u@example.org" "$pw"
  rm -f "$u@example.org.csr" "$u@example.org.key" "$u@example.org.crt"
done

echo "[*] Cliente de prueba user@example.org (client.*)"
emitir_cliente client user@example.org whatever
cp client.p12 user@example.org.p12; cp client.pem user@example.org.pem
rm -f .cnf_tmp

echo "[*] Copiando CA y certificados de los suplicantes a $SUPP"
mkdir -p "$SUPP"
cp ca.pem client.pem client.key user@example.org.pem "$SUPP/"
for par in $USUARIOS; do u="${par%%:*}"; cp "$u@example.org.pem" "$SUPP/"; done

echo; echo "[*] Verificacion"
openssl verify -CAfile ca.pem server.pem
for par in $USUARIOS; do u="${par%%:*}"; openssl verify -CAfile ca.pem "$u@example.org.pem"; done
openssl verify -CAfile ca.pem client.pem
echo; for f in ca.pem server.pem profesor01@example.org.pem; do printf "%-28s " "$f"; openssl x509 -in "$f" -noout -enddate; done
