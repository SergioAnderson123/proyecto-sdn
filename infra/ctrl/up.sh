#!/bin/bash
# Levanta un nodo de control (ctrl-1, ctrl-2 o ctrl-3): ONOS, OpenLDAP, Policy Mapper y FreeRADIUS, y configura ONOS.
# Mismo script y mismo compose en los tres nodos. Uso:  cd infra/ctrl && ./up.sh        Apagar:  docker compose down
#
# openflow, flowanalyzer, dhcp, acl y vpls los instala y activa ONOS solo, al arrancar (ver ONOS_APPS en compose.yml).
# Lo que queda por hacer aqui es lo que ONOS_APPS no cubre:
#   - SADIS y AAA son apps de OpenCORD: no vienen con la imagen, hay que bajarlas de Maven e instalarlas por REST.
#   - la app DHCP necesita que le mandemos el rango y los tiempos por REST, aunque ya este activa.
set -e
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }

echo "[*] Levantando los contenedores..."
docker compose up -d --build

ONOS_URL="${ONOS_URL:-http://127.0.0.1:8181/onos/v1}"
AUTH="${ONOS_USER:-onos}:${ONOS_PASS:-rocks}"
SADIS_VER=5.6.0
AAA_VER=2.7.0
MAVEN="https://repo1.maven.org/maven2/org/opencord"
FALLOS=0
mkdir -p oar || { echo "[X] No puedo crear la carpeta oar/ (permisos: sudo chown -R \$USER:\$USER $(pwd))"; exit 1; }

http_code() { curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" "$@"; }
activa()    { curl -s -u "$AUTH" "$ONOS_URL/applications/$1" | grep -q '"state":"ACTIVE"'; }

echo "[*] Esperando la API REST de ONOS ($ONOS_URL)..."
espera=0
until curl -s -u "$AUTH" "$ONOS_URL/applications" >/dev/null; do
  sleep 3; espera=$((espera + 3))
  [ "$espera" -ge 300 ] && { echo "[X] ONOS no respondio en 5 minutos: revisa 'docker logs onos --tail 40' y la RAM (free -h)."; exit 1; }
done
echo "[+] ONOS API REST disponible."

echo "[*] Comprobando las apps de ONOS_APPS (openflow, flowanalyzer, dhcp, acl, vpls)..."
for i in $(seq 1 20); do activa org.onosproject.openflow && break; sleep 3; done   # da tiempo a que ONOS_APPS termine de activarlas
for app in openflow flowanalyzer dhcp acl vpls; do
  if activa "org.onosproject.$app"; then echo "    org.onosproject.$app: ACTIVE"
  else echo "    [!] org.onosproject.$app: no quedo ACTIVE (revisa ONOS_APPS en compose.yml y 'docker logs onos --tail 40')"; FALLOS=1; fi
done
if activa org.onosproject.openflow; then echo "[+] OpenFlow ACTIVE: ONOS escucha a los switches en el 6653."
else echo "[X] OpenFlow no quedo ACTIVE: sin el, ningun switch conecta."; FALLOS=1; fi

descargar() {   # $1 = app, $2 = version
  local f="oar/$1-$2.oar"
  [ -s "$f" ] && { echo "    $1 $2: ya descargada"; return 0; }
  rm -f "$f"
  if curl -fL -sS --retry 3 --retry-delay 3 --connect-timeout 15 -o "$f" "$MAVEN/$1/$2/$1-$2.oar"; then echo "    $1 $2: descargada"
  else rm -f "$f"; echo "    [!] $1 $2: no se pudo descargar de repo1.maven.org (internet, DNS o disco: 'df -h /' y 'curl -I https://repo1.maven.org')"; FALLOS=1; return 1; fi
}
instalar() {
  local codigo; codigo="$(http_code -X POST -H "Content-Type: application/octet-stream" --data-binary "@$1" "$ONOS_URL/applications?activate=true")"
  if [ "$codigo" = "200" ] || [ "$codigo" = "201" ]; then echo "    $1: instalada y activada"
  elif [ "$codigo" = "409" ]; then echo "    $1: ya estaba instalada (se comprueba mas abajo que este ACTIVE)"   # al repetir el script: no es un fallo
  else echo "    [!] $1: no se pudo instalar (HTTP $codigo)"; FALLOS=1; fi
}

echo "[*] SADIS y AAA (OpenCORD) no vienen con la imagen: descargando $SADIS_VER / $AAA_VER (solo si faltan)..."
descargar sadis-app "$SADIS_VER"; descargar aaa-app "$AAA_VER"
echo "[*] Instalando SADIS y AAA..."
[ -s "oar/sadis-app-$SADIS_VER.oar" ] && instalar "oar/sadis-app-$SADIS_VER.oar"
[ -s "oar/aaa-app-$AAA_VER.oar" ] && instalar "oar/aaa-app-$AAA_VER.oar"

echo "[*] Configurando SADIS dinamico hacia policy_mapper:5000..."
curl -s -u "$AUTH" -X POST -H "Content-Type: application/json" "$ONOS_URL/network/configuration" -d '{
  "apps": {"org.opencord.sadis": {"sadis": {"integration": {
    "url": "http://policy_mapper:5000/sadis/%s",
    "cache": {"enabled": true, "maxsize": 1000, "ttl": "PT1m"}}}}}}'

echo "[*] Configurando AAA hacia radius:1812..."
curl -s -u "$AUTH" -X POST -H "Content-Type: application/json" "$ONOS_URL/network/configuration" -d '{
  "apps": {"org.opencord.aaa": {"AAA": {
    "radiusIp": "radius",
    "radiusServerPort": "1812",
    "radiusServerUdpPort": "1812",
    "radiusSecret": "testing123",
    "radiusConnectionType": "socket",
    "radiusMac": "00:00:00:00:00:01",
    "vlanId": "-1"}}}}'

# DHCP: la app org.onosproject.dhcp es un servidor DHCP dentro de ONOS con UN solo rango para todos los hosts (la separacion entre
# alumnos, invitados, etc. la hacen las VLAN/tablas de R2, no la subred). Valores por defecto y su significado: infra/ctrl/.env.example.
DHCP_SERVIDOR_IP="${DHCP_SERVIDOR_IP:-10.0.0.250}"; DHCP_SERVIDOR_MAC="${DHCP_SERVIDOR_MAC:-02:00:00:00:00:02}"
DHCP_MASCARA="${DHCP_MASCARA:-255.255.255.0}"; DHCP_BROADCAST="${DHCP_BROADCAST:-10.0.0.255}"
DHCP_ROUTER="${DHCP_ROUTER:-10.0.0.254}"; DHCP_DNS="${DHCP_DNS:-8.8.8.8}"
DHCP_INICIO="${DHCP_INICIO:-10.0.0.100}"; DHCP_FIN="${DHCP_FIN:-10.0.0.199}"
DHCP_LEASE="${DHCP_LEASE:-3600}"; DHCP_RENEW="${DHCP_RENEW:-1800}"; DHCP_REBIND="${DHCP_REBIND:-3150}"; DHCP_PURGA_MIN="${DHCP_PURGA_MIN:-2}"
DHCP_TIMEOUT="${DHCP_TIMEOUT:-60}"
echo "[*] Configurando la app DHCP de ONOS (rango $DHCP_INICIO - $DHCP_FIN, concesion de $DHCP_LEASE s)..."
curl -s -u "$AUTH" -X POST -H "Content-Type: application/json" "$ONOS_URL/network/configuration" -d "{
  \"apps\": {\"org.onosproject.dhcp\": {\"dhcp\": {
    \"ip\": \"$DHCP_SERVIDOR_IP\", \"mac\": \"$DHCP_SERVIDOR_MAC\", \"subnet\": \"$DHCP_MASCARA\", \"broadcast\": \"$DHCP_BROADCAST\",
    \"router\": \"$DHCP_ROUTER\", \"domain\": \"$DHCP_DNS\", \"startip\": \"$DHCP_INICIO\", \"endip\": \"$DHCP_FIN\",
    \"lease\": \"$DHCP_LEASE\", \"renew\": \"$DHCP_RENEW\", \"rebind\": \"$DHCP_REBIND\", \"delay\": \"$DHCP_PURGA_MIN\", \"timeout\": \"$DHCP_TIMEOUT\"}}}}"

# La app AAA puede haber arrancado ANTES de recibir esta configuracion y fallar con "Remote RADIUS Server: null" (NPE en activate):
# se reinicia para que lea la configuracion recien inyectada.
curl -s -u "$AUTH" -X DELETE "$ONOS_URL/applications/org.opencord.aaa/active" >/dev/null
sleep 3
curl -s -u "$AUTH" -X POST "$ONOS_URL/applications/org.opencord.aaa/active" >/dev/null

echo
echo "[*] Estado final:"
for app in org.onosproject.openflow org.opencord.sadis org.opencord.aaa org.onosproject.dhcp; do
  if activa "$app"; then echo "    $app: ACTIVE"; else echo "    [!] $app: NO ACTIVA"; FALLOS=1; fi
done
echo
echo "Contenedores:"; docker compose ps --format 'table {{.Name}}\t{{.Status}}'
echo
echo "Siguiente paso: en cada switch (acc-1, acc-2, core-1, core-2)  ovs-vsctl set-controller <bridge> tcp:${CONTROLLER_IP:-192.168.0.10}:6653"
echo
if [ "$FALLOS" = "0" ]; then echo "[+] ONOS configurado."
else echo "[X] ONOS quedo incompleto: revisa los avisos [!] de arriba y vuelve a ejecutar este script."; exit 1; fi
