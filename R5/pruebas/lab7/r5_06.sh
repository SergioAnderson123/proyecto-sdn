#!/bin/bash
# R5-06  Resiliencia ante fallas de ONOS y del Policy Mapper: failover, reintento y errores permanentes (casos 3.6.x).
# Criterio que valida: disponibilidad. Un nodo caido no frena un bloqueo, y ninguna alerta se pierde ni atasca a las demas.
# Cada caso muestra la entrada, lo esperado, el COMANDO que se ejecuta, lo obtenido y las verificaciones (OK / FALLA).
# No necesita ONOS ni Suricata. Uso, desde cualquier carpeta:   bash R5/pruebas/lab7/r5_06.sh
# Guarda la salida en ~/r5_06_<nodo>_<fecha>.txt (evidencia); SIN_ARCHIVO=1 para no guardarla. PY=python para otro interprete.
# Pruebas equivalentes con pytest:  python3 -m pytest R5/tests/test_lab7_r5.py -k R5_06_ -v
source "$(dirname "${BASH_SOURCE[0]}")/comun.sh"                    # funciones comunes; ademas entra a la raiz del repositorio
en_s1()    { grep -qxF -- "$1" <<<"$S1"; }                       # linea exacta dentro de la salida de la 1.a pasada

# Una linea de alerta del eve-alerts.json para el atacante $1
linea() { printf '{"timestamp": "2026-11-24T15:22:00.000000+0000", "event_type": "alert", "src_ip": "%s", "dest_ip": "10.0.0.50", "proto": "TCP", "alert": {"severity": 2, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}}' "$1"; }

main() {
  cabecera "R5-06  Resiliencia ante fallas de ONOS y del Policy Mapper (3.6.x)" \
    "disponibilidad. Un nodo caido no frena un bloqueo, y ninguna alerta se pierde ni atasca a las demas." \
    "nodo caido o 5xx -> siguiente nodo; 401/403/408/429 -> reintento; 4xx permanente -> se descarta; ONOS caido -> la alerta se conserva y se procesa al volver."
  echo "  Cluster simulado: 3 nodos ONOS (n1, n2, n3). Cada caso indica lo que contesta cada INTENTO de conexion."

  # ---- 3.6.1 / 3.6.2 failover ----
  caso "3.6.1" "Si un nodo cae, se pasa al siguiente y se recuerda cual funciona"
  dato "Funcion" "onos(metodo, ruta) contra un cluster de 3 nodos; se hacen 2 llamadas seguidas"
  dato "Entrada" "intentos: n1 caido, luego n2 responde 200, luego (2.a llamada) n2 responde 200"
  dato "Esperado" "las dos llamadas dan HTTP 200 y la 2.a va directa a n2, sin volver a probar n1"
  correr "$PY $L onos caido,200,200 2" "$PY" "$L" onos caido,200,200 2
  echo "  Verificaciones:"
  verif "1.a llamada: HTTP 200 (tras saltar el nodo caido)" contiene "llamada 1: HTTP 200"
  verif "2.a llamada: HTTP 200" contiene "llamada 2: HTTP 200"
  verif "nodos probados: n1 (caido), n2, y la 2.a llamada va directa a n2" contiene "nodos_probados=n1 n2 n2"
  fin

  caso "3.6.2" "Un nodo que responde 5xx cede ante el siguiente"
  dato "Funcion" "onos(metodo, ruta)"
  dato "Entrada" "intentos: n1 responde 503, n2 responde 200"
  dato "Esperado" "HTTP 200 tras probar n1 y n2"
  correr "$PY $L onos 503,200" "$PY" "$L" onos 503,200
  echo "  Verificaciones:"
  verif "HTTP 200" contiene "llamada 1: HTTP 200"
  verif "se probaron n1 y n2" contiene "nodos_probados=n1 n2"
  fin

  for c in 500 502 503; do
    caso "3.6.2b [$c]" "Si TODOS los nodos responden $c, es un fallo reintentable (despues de probarlos todos)"
    dato "Funcion" "onos(metodo, ruta)"
    dato "Entrada" "intentos: n1, n2 y n3 responden $c"
    dato "Esperado" "Reintentar, habiendo probado los tres nodos"
    correr "$PY $L onos $c,$c,$c" "$PY" "$L" onos "$c,$c,$c"
    echo "  Verificaciones:"
    verif "se lanza Reintentar (HTTP $c)" contiene "Reintentar: HTTP $c"
    verif "se probaron los tres nodos antes de rendirse" contiene "nodos_probados=n1 n2 n3"
    fin
  done

  caso "3.6.2c" "Un timeout se trata como nodo caido"
  dato "Funcion" "onos(metodo, ruta)"
  dato "Entrada" "intentos: n1 no contesta a tiempo (timeout), n2 responde 200"
  dato "Esperado" "HTTP 200 tras pasar a n2"
  correr "$PY $L onos timeout,200" "$PY" "$L" onos timeout,200
  echo "  Verificaciones:"
  verif "HTTP 200" contiene "llamada 1: HTTP 200"
  verif "se probaron n1 y n2" contiene "nodos_probados=n1 n2"
  fin

  # ---- 3.6.3 errores transitorios ----
  for c in 401 403 408 429; do
    caso "3.6.3 [$c]" "HTTP $c es un fallo transitorio: se reintenta, no se descarta"
    dato "Funcion" "onos(metodo, ruta)"
    dato "Entrada" "n1 responde $c (credenciales, permiso, tiempo agotado o limite de peticiones)"
    dato "Esperado" "Reintentar (el bucle espera y vuelve a intentar la misma alerta)"
    correr "$PY $L onos $c 1" "$PY" "$L" onos "$c" 1
    echo "  Verificaciones:"
    verif "se lanza Reintentar" contiene "Reintentar: HTTP $c"
    verif "no se prueba otro nodo (no es un fallo del nodo)" contiene "nodos_probados=n1"
    fin
  done

  # ---- 3.6.4 errores permanentes ----
  for c in 400 404 409; do
    caso "3.6.4 [$c]" "HTTP $c es un error permanente: se descarta, reintentar no ayudaria"
    dato "Funcion" "onos(metodo, ruta)"
    dato "Entrada" "n1 responde $c"
    dato "Esperado" "Descartar"
    correr "$PY $L onos $c 1" "$PY" "$L" onos "$c" 1
    echo "  Verificaciones:"
    verif "se lanza Descartar" contiene "Descartar: HTTP $c"
    fin
  done

  # ---- 3.6.5 todos caidos / sin nodos ----
  caso "3.6.5" "Todos los nodos caidos es reintentable y no pierde la alerta"
  dato "Funcion" "onos(metodo, ruta)"
  dato "Entrada" "intentos: n1, n2 y n3 caidos"
  dato "Esperado" "Reintentar, habiendo probado los tres nodos"
  correr "$PY $L onos caido,caido,caido" "$PY" "$L" onos caido,caido,caido
  echo "  Verificaciones:"
  verif "se lanza Reintentar" contiene "Reintentar:"
  verif "se probaron los tres nodos" contiene "nodos_probados=n1 n2 n3"
  fin

  caso "3.6.5b" "Sin nodos configurados (ONOS_URL vacio) tambien es reintentable"
  dato "Funcion" "onos(metodo, ruta)   con ONOS_URL=''"
  dato "Entrada" "ningun nodo configurado"
  dato "Esperado" "Reintentar: ONOS_URL sin nodos"
  correr "$PY $L onos - 1 sin-nodos" "$PY" "$L" onos - 1 sin-nodos
  echo "  Verificaciones:"
  verif "se lanza Reintentar" contiene "Reintentar: ONOS_URL sin nodos"
  verif "no se prueba ningun nodo" contiene "nodos_probados=(ninguno)"
  fin

  # ---- 3.6.6 espera exponencial acotada ----
  EA=(1 4 16 30 30); EF=(si si si si no); ES=(2 8 30 30 1)
  ED=("se duplica" "se duplica" "se duplicaria a 32 pero el tope es 30 s" "ya en el tope: se queda en 30 s" "sin fallo vuelve a la espera minima (1 s)")
  for i in "${!EA[@]}"; do
    caso "3.6.6 [$((i+1))/5]" "Espera tras fallar: ${EA[$i]} s, fallo=${EF[$i]} -> ${ES[$i]} s (${ED[$i]})"
    dato "Funcion" "siguiente_espera(espera, fallo)   con BACKOFF_MIN = 1 s y BACKOFF_MAX = 30 s"
    dato "Entrada" "espera actual ${EA[$i]} s, ¿acaba de fallar ONOS? ${EF[$i]}"
    dato "Esperado" "siguiente_espera=${ES[$i]}s"
    correr "$PY $L backoff ${EA[$i]} ${EF[$i]}" "$PY" "$L" backoff "${EA[$i]}" "${EF[$i]}"
    echo "  Verificaciones:"
    verif "la siguiente espera es ${ES[$i]} s" contiene "siguiente_espera=${ES[$i]}s"
    fin
  done

  # ---- 3.6.7 no se pierde ninguna alerta ----
  L7="$(linea 203.0.113.7)"; L8="$(linea 203.0.113.8)"
  EVE=$(mktemp); EST=$(mktemp); rm -f "$EST"
  printf '%s\n%s\n' "$L7" "$L8" > "$EVE"
  TAM=$(wc -c < "$EVE" | tr -d ' ')
  caso "3.6.7" "Si ONOS cae, no se pierde ninguna alerta: se procesan cuando ONOS vuelve"
  dato "Funcion" "consumir_eve(estado) sobre un archivo eve-alerts.json con 2 alertas ($TAM bytes)"
  dato "Entrada" "1.a pasada: ONOS caido. 2.a pasada: ONOS vuelve"
  dato "Esperado" "1.a: offset=0 y nada bloqueado (las alertas siguen pendientes). 2.a: se bloquean 203.0.113.7 y .8 y el offset llega al final ($TAM)"
  echo "  --- 1.a pasada: ONOS caido ---"
  correr "$PY $L consumir <archivo> <estado> caido" "$PY" "$L" consumir "$EVE" "$EST" caido
  S1="$SALIDA"
  echo "  --- ONOS vuelve; 2.a pasada ---"
  correr "$PY $L consumir <archivo> <estado>" "$PY" "$L" consumir "$EVE" "$EST"
  echo "  Verificaciones:"
  verif "ONOS caido: el offset no avanza (offset=0)" en_s1 "offset=0"
  verif "ONOS caido: no se da ninguna alerta por procesada" en_s1 "bloqueadas=(ninguna)"
  verif "ONOS caido: el bucle sabe que debe esperar y reintentar" en_s1 "fallo_onos=True"
  verif "ONOS vuelve: se bloquean las dos alertas pendientes" contiene "bloqueadas=203.0.113.7,203.0.113.8"
  verif "ONOS vuelve: el offset llega al final del archivo ($TAM)" contiene "offset=$TAM"
  fin
  rm -f "$EVE" "$EST"

  EVE=$(mktemp); EST=$(mktemp); rm -f "$EST"
  printf '%s\n%s\n' "$L7" "$L8" > "$EVE"
  TAM1=$(printf '%s\n' "$L7" | wc -c | tr -d ' ')
  caso "3.6.7b" "Si ONOS cae a mitad, no se repite lo ya procesado"
  dato "Funcion" "consumir_eve(estado) sobre un archivo con 2 alertas"
  dato "Entrada" "1.a pasada: ONOS atiende el 1.er POST y cae en el 2.o. 2.a pasada: ONOS vuelve"
  dato "Esperado" "1.a: solo 203.0.113.7 bloqueada, offset=$TAM1 (tras la 1.a linea). 2.a: se bloquea 203.0.113.8 con 1 solo POST (la 1.a no se reenvia)"
  echo "  --- 1.a pasada: ONOS cae en el 2.o POST ---"
  correr "$PY $L consumir <archivo> <estado> a-medias" "$PY" "$L" consumir "$EVE" "$EST" a-medias
  S1="$SALIDA"
  echo "  --- ONOS vuelve; 2.a pasada ---"
  correr "$PY $L consumir <archivo> <estado>" "$PY" "$L" consumir "$EVE" "$EST"
  echo "  Verificaciones:"
  verif "1.a pasada: solo la 1.a alerta queda bloqueada" en_s1 "bloqueadas=203.0.113.7"
  verif "1.a pasada: el offset queda tras la 1.a linea ($TAM1)" en_s1 "offset=$TAM1"
  verif "2.a pasada: las dos quedan bloqueadas" contiene "bloqueadas=203.0.113.7,203.0.113.8"
  verif "2.a pasada: un solo POST (la 1.a alerta no se reenvio)" contiene "POST a ONOS=1"
  fin
  rm -f "$EVE" "$EST"

  # ---- 3.6.8 un error permanente no atasca la cola ----
  L9="$(linea 203.0.113.9)"; L10="$(linea 203.0.113.10)"
  EVE=$(mktemp); EST=$(mktemp); rm -f "$EST"
  printf '%s\n%s\n' "$L9" "$L10" > "$EVE"
  TAM=$(wc -c < "$EVE" | tr -d ' ')
  caso "3.6.8" "Un error permanente en una alerta no atasca a las siguientes"
  dato "Funcion" "consumir_eve(estado) sobre un archivo con 2 alertas ($TAM bytes)"
  dato "Entrada" "ONOS rechaza con HTTP 400 la regla de 203.0.113.9 y acepta la de 203.0.113.10"
  dato "Esperado" "203.0.113.9 se descarta, 203.0.113.10 se bloquea y el offset llega al final ($TAM): no hay reintento infinito"
  correr "$PY $L consumir <archivo> <estado> rechaza:203.0.113.9" "$PY" "$L" consumir "$EVE" "$EST" rechaza:203.0.113.9
  echo "  Verificaciones:"
  verif "solo 203.0.113.10 queda bloqueada" contiene "bloqueadas=203.0.113.10"
  verif "el offset llega al final del archivo ($TAM)" contiene "offset=$TAM"
  verif "no se pide esperar ni reintentar (fallo_onos=False)" contiene "fallo_onos=False"
  fin
  rm -f "$EVE" "$EST"

  # ---- 3.6.9 Policy Mapper caido ----
  caso "3.6.9" "Un Policy Mapper caido no impide el bloqueo"
  dato "Funcion" "procesar(alerta) con revocacion de sesion (I11)"
  dato "Entrada" "alerta grave de 10.0.0.21 (host interno, en el borde of:00000000000000a1); el unico mapper (m1) esta caido"
  dato "Esperado" "10.0.0.21 se bloquea igual; la sesion no se pudo revocar (None)"
  correr "$PY $L bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1 mapper-caido" "$PY" "$L" bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1 mapper-caido
  echo "  Verificaciones:"
  verif "el host interno queda bloqueado" contiene "bloqueadas=10.0.0.21"
  verif "la sesion no se revoca (None), pero no se rompe nada" contiene "sesion de 10.0.0.21: None"
  fin

  caso "3.6.9b" "Si el mapper primario cae, se usa la replica"
  dato "Funcion" "procesar(alerta) con revocacion de sesion (I11)"
  dato "Entrada" "alerta grave de 10.0.0.21; el mapper primario (m1) esta caido y la replica (m2) responde"
  dato "Esperado" "se prueba m1, luego m2, y la sesion queda revocada"
  correr "$PY $L bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1 mapper-replica" "$PY" "$L" bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1 mapper-replica
  echo "  Verificaciones:"
  verif "se probaron primero m1 y luego m2" contiene "mapper_probados=m1:5000 m2:5000"
  verif "la sesion queda revocada por la replica" contiene "sesion de 10.0.0.21: revocada"
  fin

  resumen R5-06
}

ejecutar R5-06
