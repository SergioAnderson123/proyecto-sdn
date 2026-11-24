#!/bin/bash
# Corre UNA prueba del Lab 7 (R5-01 .. R5-06) con encabezado legible. Uso: bash r5_lab7.sh 1
# Guarda la salida en ~/lab7_R5-0N_<nodo>_<fecha>.txt (evidencia) y la copia a ~/r5_live.log (visor de paneles).
N="$1"
cd ~/proyecto-sdn || exit 1
case "$N" in
1) T="R5-01  parse_ids_alert: lectura de la alerta del IDS (3.1.x)"; C="Deteccion del ataque"
   E="Alerta valida -> devuelve IP, puertos, protocolo, firma y severidad. Incompleta, corrupta o que no es alerta -> None sin excepcion. Linea a medio escribir -> no avanza el offset." ;;
2) T="R5-02  Decision segun severidad y direccion (1.2.x)"; C="Mitigacion"
   E="Severidad 1-2 bloquea, 3-4 no (limite). Externo->interno bloquea en cores; interno->externo en el borde del host." ;;
3) T="R5-03  Lista blanca (1.3.x)"; C="Falsos positivos"
   E="Una IP de la lista blanca nunca se bloquea; su vecina no listada si; el destino protegido no libera al atacante." ;;
4) T="R5-04  Regla DROP del perimetro (1.4.x)"; C="Mitigacion"
   E="Regla con prioridad 42000, timeout 300 s, solo ipv4_src de esa IP, en todos los cores; incidente con tiempos y flow_id." ;;
5) T="R5-05  Sin duplicar y liberacion (1.5.x)"; C="Mitigacion sin saturar la TCAM"
   E="Misma IP 3 veces -> 1 sola regla. Flujo borrado -> la IP se olvida. Periodo de gracia. Reincidente -> se vuelve a bloquear." ;;
6) T="R5-06  Resiliencia ante fallas de ONOS (1.6.x)"; C="Disponibilidad"
   E="Nodo caido o 5xx -> pasa al siguiente y lo recuerda. 401/403/408/429 -> reintento. 4xx -> descarta. Todos caidos -> reintento." ;;
*) echo "uso: bash r5_lab7.sh <1..6>"; exit 2 ;;
esac
OUT=~/lab7_R5-0${N}_$(hostname)_$(date +%Y%m%d_%H%M%S).txt
{
  printf '\n\n'
  echo "################################################################################"
  echo "  $T"
  echo "  Nodo: $(hostname)   Fecha: $(date '+%F %T')   Commit: $(git rev-parse --short HEAD)"
  echo "  Criterio que valida: $C"
  echo "  Esperado: $E"
  echo "################################################################################"
  python3 -m pytest R5/tests/test_lab7_r5.py -v -p no:cacheprovider -k "R5_0${N}_" 2>&1 \
    | sed -E 's#R5/tests/test_lab7_r5.py::test_##'
  echo "RESULTADO R5-0${N}: exit=${PIPESTATUS[0]}"
} | tee "$OUT" | tee -a ~/r5_live.log
echo "Evidencia: $OUT"
