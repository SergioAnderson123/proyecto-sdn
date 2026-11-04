#!/bin/bash
# Ejecuta un comando dentro de un host de Mininet sin usar su CLI:  mnx.sh <host> <comando...>
# Ejemplo (dentro del contenedor mininet):  /root/shared-host/mnx.sh h1 curl -s http://10.0.0.40:8001/
host="$1"; shift
pid=$(pgrep -f "mininet:${host}\$" | head -n 1)
[ -n "$pid" ] || { echo "host $host no encontrado (¿esta corriendo la topologia?)" >&2; exit 1; }
exec mnexec -a "$pid" "$@"
