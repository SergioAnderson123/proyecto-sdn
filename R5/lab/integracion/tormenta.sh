#!/bin/bash
# Un usuario autenticado abre N conexiones seguidas a un servicio que SI tiene permitido (por defecto 40 contra 10.0.0.40:8001).
# El volumen (>20 SYN en 5 s desde una IP) activa la regla 9000002 de Suricata sin salirse de lo que R2 permite.
# Uso (dentro del contenedor mininet):  bash /root/r5/tormenta.sh <host> [N] [ip] [puerto]
host=${1:?host}; n=${2:-40}; ip=${3:-10.0.0.40}; puerto=${4:-8001}
for i in $(seq 1 "$n"); do
  bash /root/shared-host/mnx.sh "$host" curl -s -m 2 -o /dev/null "http://$ip:$puerto/" &
done
wait
echo "$host: $n conexiones a $ip:$puerto enviadas"
