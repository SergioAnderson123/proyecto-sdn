#!/bin/bash
# Muestra las interfaces de este sensor con su MAC y su estado, para llenar R5/deploy/topologia.yaml (campo `mac` de cada core).
# Se ejecuta ANTES de ./up.sh y no necesita Docker. Uso:  bash macs.sh
# Las NIC de espejo son las que no son la de gestion (ens3). Para saber a que core llega cada una, ver "Identificar los enlaces" en R5/deploy/README.md.
printf '%-10s %-19s %-8s %s\n' INTERFAZ MAC ESTADO IPv4
for d in /sys/class/net/*; do
  i=$(basename "$d")
  [ "$i" = lo ] && continue
  printf '%-10s %-19s %-8s %s\n' "$i" "$(cat "$d/address")" "$(cat "$d/operstate")" "$(ip -4 -o addr show "$i" | awk '{print $4}' | paste -sd, -)"
done
