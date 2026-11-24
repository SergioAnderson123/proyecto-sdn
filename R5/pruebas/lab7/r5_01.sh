#!/bin/bash
# R5-01  parse_ids_alert(linea): lectura de la alerta del IDS (casos 3.1.x).   Criterio que valida: deteccion del ataque.
# Cada caso muestra la entrada, lo esperado, el COMANDO que se ejecuta, lo obtenido y las verificaciones (OK / FALLA).
# No necesita ONOS ni Suricata. Uso, desde cualquier carpeta:   bash R5/pruebas/lab7/r5_01.sh
# Guarda la salida en ~/r5_01_<nodo>_<fecha>.txt (evidencia); SIN_ARCHIVO=1 para no guardarla. PY=python para otro interprete.
# Pruebas equivalentes con pytest:  python3 -m pytest R5/tests/test_lab7_r5.py -k R5_01_ -v
source "$(dirname "${BASH_SOURCE[0]}")/comun.sh"                    # funciones comunes; ademas entra a la raiz del repositorio
en_s1()    { grep -qxF -- "$1" <<<"$S1"; }                       # linea exacta dentro de la salida de la 1.a pasada (caso 3.1.5)

LINEA_VALIDA='{"timestamp": "2026-11-23T15:22:00.000000+0000", "event_type": "alert", "src_ip": "203.0.113.103", "src_port": 51234, "dest_ip": "10.0.0.10", "dest_port": 80, "proto": "TCP", "direction": "to_server", "ether": {"src_mac": "AA:BB:CC:00:00:01", "dest_mac": "AA:BB:CC:00:00:02"}, "alert": {"severity": 2, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}}'
LINEA_FLOOD='{"timestamp": "2026-11-24T15:22:00.000000+0000", "event_type": "alert", "src_ip": "203.0.113.9", "dest_ip": "10.0.0.10", "proto": "TCP", "alert": {"severity": 1, "signature_id": 9000003, "signature": "LOCAL SYN flood"}}'

main() {
  cabecera "R5-01  parse_ids_alert(linea): lectura de la alerta del IDS (3.1.x)" \
    "deteccion del ataque. El integrador solo puede reaccionar a lo que entiende del IDS." \
    "alerta valida -> IP, puertos, protocolo, firma y severidad. Incompleta, corrupta o que no es alerta -> None sin excepcion."

  # ---- 3.1.1 alerta valida ----
  caso "3.1.1" "Alerta valida: devuelve direcciones, puertos, protocolo, firma y severidad"
  dato "Funcion" "parse_ids_alert(linea)"
  dato "Entrada" "$LINEA_VALIDA"
  dato "Esperado" "campos src_ip=203.0.113.103:51234, dest_ip=10.0.0.10:80, proto=TCP, sid=9000002, severidad=2, MAC en minusculas"
  correr "$PY $L parse '<linea>'" "$PY" "$L" parse "$LINEA_VALIDA"
  echo "  Verificaciones:"
  verif "origen = 203.0.113.103:51234" contiene_todo '"src_ip": "203.0.113.103"' '"src_port": 51234'
  verif "destino = 10.0.0.10:80" contiene_todo '"dest_ip": "10.0.0.10"' '"dest_port": 80'
  verif "protocolo = TCP" contiene '"proto": "TCP"'
  verif "firma = 'LOCAL Port scan SYN' (sid 9000002)" contiene '"sid": 9000002'
  verif "severidad = 2" contiene '"severidad": 2'
  verif "hora del paquete conservada" contiene '"timestamp": "2026-11-23T15:22:00.000000+0000"'
  verif "MAC origen en minusculas" contiene '"mac_src": "aa:bb:cc:00:00:01"'
  verif "MAC destino en minusculas" contiene '"mac_dst": "aa:bb:cc:00:00:02"'
  fin

  # ---- 3.1.1b alerta sin campos opcionales ----
  LINEA_ICMP='{"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "proto": "ICMP", "alert": {"severity": 1, "signature_id": 9000021, "signature": "LOCAL R4 Inundacion ICMP interna"}}'
  caso "3.1.1b" "Alerta ICMP sin puertos ni MAC: sigue siendo valida (campos opcionales = None)"
  dato "Funcion" "parse_ids_alert(linea)"
  dato "Entrada" "$LINEA_ICMP"
  dato "Esperado" "alerta valida con src_port, dest_port, mac_src y mac_dst en null"
  correr "$PY $L parse '<linea>'" "$PY" "$L" parse "$LINEA_ICMP"
  echo "  Verificaciones:"
  verif "no es None" no_es "None"
  verif "puertos en null" contiene '"src_port": null'
  verif "MAC en null" contiene '"mac_src": null'
  fin

  # ---- 3.1.2 alertas incompletas o mal tipadas ----
  D2=("sin bloque 'alert'" "sin src_ip ni dest_ip" "IP invalidas ('x', 'y')" "sin destino" "sin severidad (no hay bloque alert)" "bloque alert vacio" "severidad no numerica ('alta')" "'alert' no es un objeto")
  E2=('{"event_type": "alert"}'
      '{"event_type": "alert", "alert": {"severity": 1}}'
      '{"event_type": "alert", "src_ip": "x", "dest_ip": "y", "alert": {"severity": 1}}'
      '{"event_type": "alert", "src_ip": "203.0.113.5", "alert": {"severity": 1}}'
      '{"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10"}'
      '{"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {}}'
      '{"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {"severity": "alta"}}'
      '{"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": "texto"}')
  for i in "${!E2[@]}"; do
    caso "3.1.2 [$((i+1))/8]" "Alerta incompleta o mal tipada: ${D2[$i]}"
    dato "Funcion" "parse_ids_alert(linea) y luego procesar(evento)"
    dato "Entrada" "${E2[$i]}"
    dato "Esperado" "None, sin excepcion, y ninguna regla de bloqueo hacia ONOS"
    correr "$PY $L procesar '<linea>'" "$PY" "$L" procesar "${E2[$i]}"
    echo "  Verificaciones:"
    verif "parse_ids_alert devuelve None" contiene "parse_ids_alert -> None"
    verif "procesar() no lanza excepcion" contiene "sin excepcion"
    verif "no se bloquea ninguna IP" contiene "bloqueadas=[]"
    verif "no se envia ningun POST a ONOS" contiene "POST a ONOS=0"
    fin
  done

  # ---- 3.1.3 lineas corruptas o que no son un objeto ----
  D3=("JSON truncado" "texto cualquiera" "linea vacia" "solo espacios" "JSON valido pero lista" "JSON valido pero numero" "JSON null" "JSON valido pero cadena")
  E3=('{"event_type": "alert", ' 'no es json' '' '   ' '[]' '5' 'null' '"texto"')
  for i in "${!E3[@]}"; do
    caso "3.1.3 [$((i+1))/9]" "Linea corrupta o que no es un objeto: ${D3[$i]}"
    dato "Funcion" "parse_ids_alert(linea)"
    dato "Entrada" "'${E3[$i]}'"
    dato "Esperado" "None, sin excepcion"
    correr "$PY $L parse '${E3[$i]}'" "$PY" "$L" parse "${E3[$i]}"
    echo "  Verificaciones:"
    verif "devuelve None" es "None"
    fin
  done
  caso "3.1.3 [9/9]" "Linea corrupta o que no es un objeto: no es texto (None)"
  dato "Funcion" "parse_ids_alert(None)"
  dato "Entrada" "None"
  dato "Esperado" "None, sin excepcion"
  correr "$PY $L parse-sin-texto" "$PY" "$L" parse-sin-texto
  echo "  Verificaciones:"
  verif "devuelve None" es "None"
  fin

  # ---- 3.1.4 eventos que no son alerta ----
  n=0
  for tipo in stats flow dns anomaly ""; do
    n=$((n+1))
    EV=$(printf '{"event_type": "%s", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {"severity": 1}}' "$tipo")
    caso "3.1.4 [$n/5]" "Evento que no es una alerta (event_type = '$tipo')"
    dato "Funcion" "parse_ids_alert(linea)"
    dato "Entrada" "$EV"
    dato "Esperado" "None (solo se procesan eventos 'alert')"
    correr "$PY $L parse '<linea>'" "$PY" "$L" parse "$EV"
    echo "  Verificaciones:"
    verif "devuelve None" es "None"
    fin
  done

  # ---- 3.1.5 linea a medio escribir ----
  EVE=$(mktemp); EST=$(mktemp); rm -f "$EST"
  printf '%s\n%s' "$LINEA_VALIDA" "${LINEA_FLOOD:0:30}" > "$EVE"                 # la 2.a linea sin terminar y sin salto de linea
  TAM1=$(printf '%s\n' "$LINEA_VALIDA" | wc -c | tr -d ' ')
  caso "3.1.5" "Linea a medio escribir: no se procesa ni avanza el offset hasta que Suricata la termine"
  dato "Funcion" "consumir_eve(estado) sobre un archivo eve-alerts.json"
  dato "Entrada" "archivo con 2 lineas: la 1.a completa ($TAM1 bytes) y la 2.a a medias: ${LINEA_FLOOD:0:30}"
  dato "Esperado" "1.a pasada: solo se bloquea 203.0.113.103 y offset=$TAM1. 2.a pasada (linea ya completa): tambien 203.0.113.9"
  echo "  --- 1.a pasada ---"
  correr "$PY $L consumir <archivo> <estado>" "$PY" "$L" consumir "$EVE" "$EST"
  S1="$SALIDA"
  printf '%s\n%s\n' "$LINEA_VALIDA" "$LINEA_FLOOD" > "$EVE"                      # Suricata termina de escribir la linea
  echo "  --- Suricata termina de escribir la 2.a linea; 2.a pasada ---"
  correr "$PY $L consumir <archivo> <estado>" "$PY" "$L" consumir "$EVE" "$EST"
  echo "  Verificaciones:"
  verif "offset tras la 1.a pasada = $TAM1 (solo la linea completa)" en_s1 "offset=$TAM1"
  verif "1.a pasada: solo 203.0.113.103 bloqueada" en_s1 "bloqueadas=203.0.113.103"
  verif "2.a pasada: se procesa la linea ya completa" contiene "bloqueadas=203.0.113.103,203.0.113.9"
  verif "ninguna pasada reporta fallo de ONOS" contiene "fallo_onos=False"
  fin
  rm -f "$EVE" "$EST"

  # ---- 3.1.6 linea corrupta dentro del archivo ----
  EVE=$(mktemp); EST=$(mktemp); rm -f "$EST"
  printf 'basura que no es json\n%s\n' "$LINEA_VALIDA" > "$EVE"
  TAM=$(wc -c < "$EVE" | tr -d ' ')
  caso "3.1.6" "Linea corrupta dentro del archivo: se salta y las siguientes se procesan"
  dato "Funcion" "consumir_eve(estado) sobre un archivo eve-alerts.json"
  dato "Entrada" "archivo con 2 lineas: 'basura que no es json' y una alerta valida de 203.0.113.103 ($TAM bytes en total)"
  dato "Esperado" "se salta la 1.a linea, se bloquea 203.0.113.103 y el offset llega al final del archivo (offset=$TAM)"
  correr "$PY $L consumir <archivo> <estado>" "$PY" "$L" consumir "$EVE" "$EST"
  echo "  Verificaciones:"
  verif "la alerta valida se procesa" contiene "bloqueadas=203.0.113.103"
  verif "el offset avanza hasta el final ($TAM)" contiene "offset=$TAM"
  fin
  rm -f "$EVE" "$EST"

  resumen R5-01
}

ejecutar R5-01
