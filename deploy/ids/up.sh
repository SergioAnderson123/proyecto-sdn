#!/bin/bash
# Levanta la VM IDS (R5). Uso:  cd deploy/ids && ./up.sh        Apagar:  docker compose down
# Requisito: el Controller ya esta arriba y los switches conectados (CORE_DEVICE y puertos en ../.env).
set -e
cd "$(dirname "$0")"
[ -f ../.env ] || { echo "Falta deploy/.env (copiar de .env.example)"; exit 1; }
set -a; . ../.env; set +a
# La red virtual de VNRT solo entrega al IDS las tramas dirigidas a su MAC: el espejo reescribe eth_dst con esta MAC.
export IDS_MAC="${IDS_MAC:-$(cat /sys/class/net/$IDS_IFACE/address)}"
echo "[r5] interfaz $IDS_IFACE, MAC $IDS_MAC"
docker compose up -d --build suricata integrador
docker compose run --rm espejo
sleep 3
docker compose exec integrador r5ctl estado
