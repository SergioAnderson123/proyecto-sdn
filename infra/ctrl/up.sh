#!/bin/bash
# Levanta un nodo de control (ctrl-1, ctrl-2 o ctrl-3): ONOS, OpenLDAP, Policy Mapper y FreeRADIUS, y configura ONOS.
# Mismo script y mismo compose en los tres nodos. Uso:  cd infra/ctrl && ./up.sh        Apagar:  docker compose down
set -e
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }
docker compose up -d --build
bash init-onos.sh || echo "[!] init-onos.sh termino con avisos: ONOS esta levantado pero incompleto (ver arriba)."
echo
echo "Contenedores:"; docker compose ps --format 'table {{.Name}}\t{{.Status}}'
echo
echo "Siguiente paso: en cada switch (acc-1, acc-2, core-1, core-2)  ovs-vsctl set-controller <bridge> tcp:${CONTROLLER_IP:-192.168.0.10}:6653"
