#!/bin/bash

echo "[*] Esperando a que la API REST de ONOS esté lista en el puerto 8181..."
until curl -s -u onos:rocks http://localhost:8181/onos/v1/applications > /dev/null; do
    sleep 3
done
echo "[+] ONOS API REST disponible."

# 1. Descargar las aplicaciones OAR requeridas si no existen
echo "[*] Descargando módulos SADIS (5.6.0) y AAA (2.7.0)..."
wget -q -N https://repo1.maven.org/maven2/org/opencord/sadis-app/5.6.0/sadis-app-5.6.0.oar
wget -q -N https://repo1.maven.org/maven2/org/opencord/aaa-app/2.7.0/aaa-app-2.7.0.oar

# 2. Activar OpenFlow Provider Suite en ONOS
echo "[*] Activando OpenFlow Provider Suite..."
curl -s -u onos:rocks -X POST http://localhost:8181/onos/v1/applications/org.onosproject.openflow/active

# 3. Instalar y activar SADIS y AAA
echo "[*] Instalando y activando SADIS y AAA..."
curl -s -u onos:rocks -X POST -H "Content-Type: application/octet-stream" \
  --data-binary @sadis-app-5.6.0.oar "http://localhost:8181/onos/v1/applications?activate=true"

curl -s -u onos:rocks -X POST -H "Content-Type: application/octet-stream" \
  --data-binary @aaa-app-2.7.0.oar "http://localhost:8181/onos/v1/applications?activate=true"

# 4. Inyectar configuración de SADIS dinámico hacia policy_mapper (policy_mapper)
echo "[*] Inyectando configuración SADIS dinámica hacia policy_mapper (policy_mapper:5000)..."
curl -s -u onos:rocks -X POST -H "Content-Type: application/json" \
  http://localhost:8181/onos/v1/network/configuration \
  -d '{
    "apps": {
      "org.opencord.sadis": {
        "sadis": {
          "integration": {
            "url": "http://policy_mapper:5000/sadis/%s",
            "cache": {
              "enabled": true,
              "maxsize": 1000,
              "ttl": "PT1m"
            }
          }
        }
      }
    }
  }'

# 5. Inyectar configuración AAA hacia freeradius_server (radius)
# Se define explícitamente bajo org.opencord.aaa y en modo socket
echo "[*] Inyectando configuración AAA hacia freeradius_server (radius:1812)..."
curl -s -u onos:rocks -X POST -H "Content-Type: application/json" \
  http://localhost:8181/onos/v1/network/configuration \
  -d '{
    "apps": {
      "org.opencord.aaa": {
        "AAA": {
          "radiusIp": "radius",
          "radiusServerPort": "1812",
          "radiusServerUdpPort": "1812",
          "radiusSecret": "testing123",
          "radiusConnectionType": "socket",
          "radiusMac": "00:00:00:00:00:01",
          "vlanId": "-1"
        }
      }
    }
  }'

echo "[+] Configuración de ONOS completada exitosamente."
