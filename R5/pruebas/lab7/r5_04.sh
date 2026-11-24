#!/bin/bash
# R5-04  build_perimeter_rule(ip, device) y registrar_incidente: la regla DROP del perimetro y su registro (casos 3.4.x).
# Criterio que valida: mitigacion. La regla corta solo al atacante, con prioridad y expiracion, en el switch correcto, y el incidente queda registrado.
# Cada caso muestra la entrada, lo esperado, el COMANDO que se ejecuta, lo obtenido y las verificaciones (OK / FALLA).
# No necesita ONOS ni Suricata. Uso, desde cualquier carpeta:   bash R5/pruebas/lab7/r5_04.sh
# Guarda la salida en ~/r5_04_<nodo>_<fecha>.txt (evidencia); SIN_ARCHIVO=1 para no guardarla. PY=python para otro interprete.
# Pruebas equivalentes con pytest:  python3 -m pytest R5/tests/test_lab7_r5.py -k R5_04_ -v
source "$(dirname "${BASH_SOURCE[0]}")/comun.sh"                    # funciones comunes; ademas entra a la raiz del repositorio

main() {
  cabecera "R5-04  build_perimeter_rule e incidente: regla DROP del perimetro (3.4.x)" \
    "mitigacion. La regla corta solo al atacante, con prioridad y expiracion, en el switch correcto, y el incidente queda registrado." \
    "DROP de prioridad 42000, timeout 300 s, solo ipv4_src del atacante; cores para un externo, borde para un interno; incidente con origen, destino, firma, accion y tiempos."

  # ---- 3.4.1 contenido de la regla ----
  caso "3.4.1" "La regla descarta solo el origen, con prioridad y timeout"
  dato "Funcion" "build_perimeter_rule(ip, device)   con P_R5=42000 y BLOCK_SEC=300"
  dato "Entrada" "ip='203.0.113.7', device='of:0000000000000001'"
  dato "Esperado" "prioridad 42000, timeout 300 s por inactividad, sin instrucciones (DROP), selector solo IPv4 + IPV4_SRC 203.0.113.7/32"
  correr "$PY $L regla 203.0.113.7 of:0000000000000001" "$PY" "$L" regla 203.0.113.7 of:0000000000000001
  echo "  Verificaciones:"
  verif "prioridad 42000 (banda de R5: sobre la sesion de R2, bajo la lista negra de R1)" contiene '"priority": 42000'
  verif "timeout 300 s y no permanente: expira cuando el atacante calla" contiene_todo '"timeout": 300' '"isPermanent": false'
  verif "sin instrucciones = DROP" contiene '"instructions": []'
  verif "selector exacto: solo IPv4 y solo el origen 203.0.113.7/32, ningun otro criterio" contiene_todo '"ip": "203.0.113.7/32"' "criterios=ETH_TYPE,IPV4_SRC"
  verif "la regla va dirigida al switch indicado" contiene '"deviceId": "of:0000000000000001"'
  fin

  # ---- 3.4.1b IP invalida o IPv6 ----
  I1=("no es ip" "203.0.113.999" "2001:db8::1" "")
  D1=("texto" "octeto fuera de rango" "IPv6 (la regla es solo de IPv4)" "vacio")
  for i in "${!I1[@]}"; do
    caso "3.4.1b [$((i+1))/4]" "IP invalida no genera regla: ${D1[$i]}"
    dato "Funcion" "build_perimeter_rule(ip, device)"
    dato "Entrada" "ip='${I1[$i]}'"
    dato "Esperado" "ValueError: un dato invalido no debe llegar al switch como selector basura"
    correr "$PY $L regla '${I1[$i]}'" "$PY" "$L" regla "${I1[$i]}"
    echo "  Verificaciones:"
    verif "se lanza ValueError" contiene "ValueError"
    verif "no se genera ninguna regla" no_contiene "priority"
    fin
  done

  # ---- 3.4.1c configurable ----
  caso "3.4.1c" "Prioridad y timeout son configurables"
  dato "Funcion" "build_perimeter_rule(ip, device)   con P_R5=43000 y BLOCK_SEC=60"
  dato "Entrada" "ip='203.0.113.7'"
  dato "Esperado" "prioridad 43000 y timeout 60"
  correr "P_R5=43000 BLOCK_SEC=60 $PY $L regla 203.0.113.7" env P_R5=43000 BLOCK_SEC=60 "$PY" "$L" regla 203.0.113.7
  echo "  Verificaciones:"
  verif "prioridad 43000" contiene '"priority": 43000'
  verif "timeout 60" contiene '"timeout": 60'
  fin

  # ---- 3.4.2 un externo se bloquea en todos los cores, en una sola peticion ----
  caso "3.4.2" "Un atacante externo se bloquea en TODOS los cores con una sola peticion a ONOS"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso   con 3 cores: of:c1, of:c2, of:c3"
  dato "Entrada" "alerta grave de 203.0.113.7 (externo) hacia 10.0.0.50"
  dato "Esperado" "1 POST con 3 flujos, uno por core, y un flow_id por cada uno"
  correr "CORE_DEVICES=of:c1,of:c2,of:c3 $PY $L bloqueo 203.0.113.7 10.0.0.50 1" env CORE_DEVICES=of:c1,of:c2,of:c3 "$PY" "$L" bloqueo 203.0.113.7 10.0.0.50 1
  echo "  Verificaciones:"
  verif "una sola peticion POST" contiene "POST a ONOS=1"
  verif "tres flujos, uno por core" contiene "flows_en_el_POST=3 dispositivos=of:c1,of:c2,of:c3"
  verif "se guardan los tres flow_id (para poder borrarlos)" contiene "flow_id=777,778,779"
  fin

  caso "3.4.2b" "Un host interno se bloquea solo en su switch de borde"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso (ONOS ve a 10.0.0.21 en of:00000000000000a1)"
  dato "Entrada" "alerta grave de 10.0.0.21 (interno) hacia 198.51.100.9"
  dato "Esperado" "1 flujo, solo en of:00000000000000a1"
  correr "$PY $L bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1" "$PY" "$L" bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1
  echo "  Verificaciones:"
  verif "un solo flujo, en el borde del host" contiene "flows_en_el_POST=1 dispositivos=of:00000000000000a1"
  verif "el registro marca 'borde'" contiene "donde=borde"
  fin

  # ---- 3.4.3 registro del incidente ----
  caso "3.4.3" "Se registra el incidente: origen, destino, tipo de ataque, accion y tiempos"
  dato "Funcion" "procesar(alerta) y registro guardado en el estado"
  dato "Entrada" "alerta de severidad 2 de 203.0.113.7 hacia 10.0.0.50 (firma 'LOCAL Port scan SYN', sid 9000002)"
  dato "Esperado" "registro con destino 10.0.0.50, accion DROP, sid/firma, switch, flow_id, hora de la alerta y tiempos lectura <= POST <= 201"
  correr "$PY $L incidente 203.0.113.7 10.0.0.50 2" "$PY" "$L" incidente 203.0.113.7 10.0.0.50 2
  echo "  Verificaciones:"
  verif "origen = 203.0.113.7" contiene "ip_bloqueada=203.0.113.7"
  verif "destino = 10.0.0.50" contiene '"destino": "10.0.0.50"'
  verif "tipo de ataque: sid 9000002 'LOCAL Port scan SYN'" contiene_todo '"sid": 9000002' '"firma": "LOCAL Port scan SYN"'
  verif "accion aplicada = DROP en el core, con su flow_id" contiene_todo '"accion": "DROP"' '"donde": "core"' '"flow_id": "777"'
  verif "hora del paquete en el sensor guardada" contiene '"t_alerta": "2026-11-24T15:22:00.000000+0000"'
  verif "sensor que lo detecto guardado" contiene '"sensor":'
  verif "tiempos lectura <= POST <= 201" contiene "tiempos_ordenados=True"
  verif "el bloqueo vence despues del 201" contiene "vence_despues_de_t_201=True"
  fin

  # ---- 3.4.4 simulacion ----
  caso "3.4.4" "En modo simulacion (DRY_RUN) no se contacta con ONOS, pero el incidente se registra"
  dato "Funcion" "procesar(alerta)   con DRY_RUN=1"
  dato "Entrada" "alerta grave de 203.0.113.7 hacia 10.0.0.50"
  dato "Esperado" "0 POST a ONOS, IP registrada, flow_id=None"
  correr "DRY_RUN=1 $PY $L bloqueo 203.0.113.7 10.0.0.50 1" env DRY_RUN=1 "$PY" "$L" bloqueo 203.0.113.7 10.0.0.50 1
  echo "  Verificaciones:"
  verif "no se envia ningun POST a ONOS" contiene "POST a ONOS=0"
  verif "el incidente queda registrado" contiene "bloqueadas=203.0.113.7"
  verif "sin flow_id (no hubo flujo real)" contiene "flow_id=None"
  fin

  # ---- 3.4.5 ONOS sin flowId ----
  caso "3.4.5" "Si ONOS no devuelve flowId, el incidente se registra igual"
  dato "Funcion" "procesar(alerta) con un ONOS falso que responde 201 sin flowId"
  dato "Entrada" "alerta grave de 203.0.113.7 hacia 10.0.0.50"
  dato "Esperado" "IP bloqueada, flow_id=None (no se podra borrar por id, pero el bloqueo existe y se reconcilia)"
  correr "$PY $L bloqueo 203.0.113.7 10.0.0.50 1 sin-flowid" "$PY" "$L" bloqueo 203.0.113.7 10.0.0.50 1 sin-flowid
  echo "  Verificaciones:"
  verif "la IP queda bloqueada" contiene "bloqueadas=203.0.113.7"
  verif "flow_id = None" contiene "flow_id=None"
  fin

  resumen R5-04
}

ejecutar R5-04
