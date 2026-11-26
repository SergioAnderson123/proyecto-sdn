#!/bin/bash
# Chequeo de 10-15 s antes de cualquier demo de R5: contenedores arriba, espejo instalado en TODOS los cores,
# sin bloqueos de pruebas anteriores. No instala ni borra nada.
#
# Se ejecuta en node-sec-1 (necesita docker; r5ctl estado lee el espejo de todos los cores por ONOS, sin SSH a cada switch):
#   bash chequeo_rapido.sh
#
# Nacio porque el 26/11 el espejo se habia caido (probablemente al reiniciarse la VNRT) y no hubo ningun aviso
# directo: solo se noto porque el tablero no mostraba nada. Este chequeo lo deja en evidencia en un solo comando.
set -uo pipefail
SEP="----------------------------------------------------------------------------------------------------"
FALLAS=0

echo "$SEP"
echo "[1] Contenedores de este sensor (r5-suricata, r5-integrador)"
if command -v docker >/dev/null; then
  ESTADO=$(sudo docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -E '^r5-(suricata|integrador)\b')
  echo "$ESTADO"
  for n in r5-suricata r5-integrador; do
    if grep -q "^$n Up" <<<"$ESTADO"; then echo "  [OK] $n esta arriba"; else echo "  [FALLA] $n NO esta arriba"; FALLAS=1; fi
  done
else
  echo "  [FALLA] no se encontro el comando docker en esta maquina (corre esto en node-sec-1 o node-sec-2)"; FALLAS=1
fi

echo "$SEP"
echo "[2] Espejo instalado en los cores (via ONOS, r5ctl estado)"
SALIDA=$(sudo docker exec r5-integrador r5ctl estado 2>&1)
echo "$SALIDA"
if grep -qi "NO instalado" <<<"$SALIDA"; then
  echo "  [FALLA] al menos un core no tiene el espejo instalado (reinstalar: ver R5/deploy/ids/up.sh, paso 'docker compose run --rm espejo')"
  FALLAS=1
elif grep -qi "Espejo al IDS" <<<"$SALIDA"; then
  echo "  [OK] el espejo aparece instalado en todos los cores"
else
  echo "  [FALLA] no se pudo leer el estado del espejo (revisar ONOS_URL/credenciales o si r5-integrador respondio)"
  FALLAS=1
fi

echo "$SEP"
echo "[3] Sin bloqueos de pruebas anteriores"
BLOQ=$(sudo docker exec r5-integrador r5ctl bloqueos 2>&1)
echo "$BLOQ"
if grep -qi "Sin bloqueos" <<<"$BLOQ"; then
  echo "  [OK] no hay bloqueos activos"
else
  echo "  [AVISO] hay bloqueos activos; si no son de la demo que vas a empezar, desbloquealos con:"
  echo "          sudo docker exec r5-integrador r5ctl desbloquear <ip>"
fi

echo "$SEP"
if [ "$FALLAS" -eq 0 ]; then
  echo "RESUMEN: listo para la demo (espejo instalado, sensor arriba)."
else
  echo "RESUMEN: FALLAS encontradas, revisar arriba antes de seguir."
fi
exit $FALLAS
