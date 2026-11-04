#!/bin/bash
# Conecta el portal cautivo al plano de datos y lo arranca. Se ejecuta DENTRO del contenedor mininet, con la topología ya levantada.
# Un par veth une el contenedor (donde el portal alcanza a ldap/policy_mapper) con el puerto portal1 de s2 (switch de confianza total).
# Uso:  docker cp portal_cautivo mininet:/opt/portal && docker exec -d mininet bash /opt/portal/portal-up.sh
set -u
IP=${PORTAL_IP:-10.0.0.60}; MAC=${PORTAL_MAC:-02:00:00:00:00:60}; SW=${PORTAL_SWITCH:-s2}
DIR=$(cd "$(dirname "$0")" && pwd)

if ! ip link show portal0 >/dev/null 2>&1; then
  ip link add portal0 type veth peer name portal1
  ip link set portal0 address "$MAC"
  ip addr add "$IP/24" dev portal0
  for i in portal0 portal1; do
    ip link set "$i" up
    ethtool -K "$i" tx off rx off tso off gso off >/dev/null 2>&1   # OVS en modo usuario descarta TCP con checksum "pendiente"
  done
  ovs-vsctl --may-exist add-port "$SW" portal1
fi

pkill -f "[p]ortal_cautivo/server.py|[o]pt/portal/server.py" 2>/dev/null
MAPPER_URL=${MAPPER_URL:-http://policy_mapper:5000} PORTAL_IP="$IP" nohup python3 "$DIR/server.py" >/var/log/portal.log 2>&1 &
echo "portal en http://$IP:8080 (log: /var/log/portal.log)"
