#!/bin/bash
# Levanta un sensor IDS (sec-1 o sec-2). Uso:  cd infra/ids && ./up.sh        Apagar:  docker compose down
# Requisitos: los nodos ONOS arriba, los cuatro switches conectados, y infra/ids/.env e infra/ids/topologia.yaml completos.
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  AUTO=".env.$(hostname)"                 # nodo-sec-1 y nodo-sec-2 ya tienen el suyo propio en el repo
  [ -f "$AUTO" ] && { echo "[*] No hay .env: uso $AUTO (detectado por hostname)"; cp "$AUTO" .env; }
fi
[ -f .env ] || { echo "Falta infra/ids/.env (copiar de .env.example, o de .env.<hostname> si existe uno para este sensor)"; exit 1; }
[ -f topologia.yaml ] || { echo "Falta infra/ids/topologia.yaml (copiar de topologia.example.yaml y completar; ver README)"; exit 1; }
set -a; . ./.env; set +a
export TOPOLOGIA_ARCHIVO="$(pwd)/topologia.yaml"
IDS_IFACES="${IDS_IFACES:-${IDS_IFACE:?falta IDS_IFACES en infra/ids/.env}}"
export IDS_IFACES

python3 -c 'import yaml' 2>/dev/null || sudo apt-get install -y python3-yaml
python3 ../../apps/ids/integrador/topologia.py topologia.yaml          # valida el archivo antes de tocar nada

# NIC de espejo: sin IP, en modo promiscuo, sin IPv6 y sin GRO/LRO (juntan tramas y Suricata vería paquetes que no existieron).
# La red virtual de VNRT solo entrega al sensor las tramas dirigidas a la MAC de cada NIC: el espejo reescribe eth_dst con ella.
echo "[r5] sensor ${SENSOR:?falta SENSOR en infra/ids/.env}: NIC de espejo y MAC (deben coincidir con topologia.yaml)"
for i in ${IDS_IFACES//,/ }; do
  sudo ip link set "$i" up promisc on
  sudo sysctl -qw "net.ipv6.conf.$i.disable_ipv6=1"
  sudo ethtool -K "$i" gro off lro off 2>/dev/null || true
  echo "     $i  $(cat /sys/class/net/$i/address)"
done

docker compose up -d --build suricata integrador
docker compose run --rm espejo
sleep 3
docker compose exec integrador r5ctl estado
