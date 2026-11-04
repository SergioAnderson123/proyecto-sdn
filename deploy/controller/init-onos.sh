#!/bin/bash
# Inicializa ONOS en la VM Controller: activa OpenFlow, instala SADIS y AAA y los apunta a policy_mapper y radius.
# Derivado de SDN_ONOS_Setup/mininet/init-onos.sh: la unica diferencia es que se ejecuta en la VM (no dentro de Mininet),
# por eso ONOS se alcanza por ONOS_URL (por defecto el puerto publicado 8181). Las URL que se inyectan en ONOS
# (policy_mapper, radius) son nombres del compose y se resuelven dentro de la red `sdn`.
set -e
cd "$(dirname "$0")"
ONOS_URL="${ONOS_URL:-http://127.0.0.1:8181/onos/v1}"
AUTH="${ONOS_USER:-onos}:${ONOS_PASS:-rocks}"
SADIS_VER=5.6.0
AAA_VER=2.7.0
mkdir -p oar

echo "[*] Esperando la API REST de ONOS ($ONOS_URL)..."
until curl -s -u "$AUTH" "$ONOS_URL/applications" >/dev/null; do sleep 3; done
echo "[+] ONOS API REST disponible."

echo "[*] Descargando SADIS $SADIS_VER y AAA $AAA_VER (solo si faltan)..."
wget -q -N -P oar "https://repo1.maven.org/maven2/org/opencord/sadis-app/$SADIS_VER/sadis-app-$SADIS_VER.oar"
wget -q -N -P oar "https://repo1.maven.org/maven2/org/opencord/aaa-app/$AAA_VER/aaa-app-$AAA_VER.oar"

activar() { curl -s -u "$AUTH" -X POST "$ONOS_URL/applications/$1/active" >/dev/null; }
instalar() {
  curl -s -u "$AUTH" -X POST -H "Content-Type: application/octet-stream" --data-binary "@$1" "$ONOS_URL/applications?activate=true" >/dev/null
}

echo "[*] Activando OpenFlow y las aplicaciones del equipo..."
activar org.onosproject.openflow
instalar "oar/sadis-app-$SADIS_VER.oar"
instalar "oar/aaa-app-$AAA_VER.oar"
for app in flowanalyzer dhcp acl vpls; do activar "org.onosproject.$app"; done

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

# La app AAA puede haber arrancado ANTES de recibir esta configuración y fallar con "Remote RADIUS Server: null" (NPE en activate):
# se reinicia para que lea la configuración recién inyectada.
curl -s -u "$AUTH" -X DELETE "$ONOS_URL/applications/org.opencord.aaa/active" >/dev/null
sleep 3
curl -s -u "$AUTH" -X POST "$ONOS_URL/applications/org.opencord.aaa/active" >/dev/null

echo "[+] ONOS configurado."
