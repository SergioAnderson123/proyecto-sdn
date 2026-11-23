#!/bin/bash
# Corre las pruebas unitarias de R5 en el nodo y guarda la salida con fecha (evidencia del Lab 7).
cd ~/proyecto-sdn || exit 1
OUT=~/r5_unit_$(hostname)_$(date +%Y%m%d_%H%M%S).txt
{
  echo "== $(hostname) $(date -Is) rama $(git branch --show-current) commit $(git rev-parse --short HEAD)"
  python3 -m pytest --version >/dev/null 2>&1 || sudo -n apt-get install -y -q python3-pytest python3-requests python3-yaml
  python3 -m pytest R5/tests R5/deploy/tests -v -p no:cacheprovider
  echo "== exit: $?"
} 2>&1 | tee "$OUT"
echo "Evidencia guardada en $OUT"
