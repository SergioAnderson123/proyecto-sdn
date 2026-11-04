#!/bin/bash
# Levanta la VM Controller (R1 + R2): ONOS, OpenLDAP, Policy Mapper y FreeRADIUS, y configura ONOS.
# Uso:  cd deploy/controller && ./up.sh        Apagar:  docker compose down
set -e
cd "$(dirname "$0")"
[ -f ../.env ] && { set -a; . ../.env; set +a; }
docker compose up -d --build
./init-onos.sh
echo
echo "Contenedores:"; docker compose ps --format 'table {{.Name}}\t{{.Status}}'
echo
echo "Siguiente paso: en cada switch (sw1..sw3)  ovs-vsctl set-controller <bridge> tcp:${CONTROLLER_IP:-192.168.0.10}:6653"
