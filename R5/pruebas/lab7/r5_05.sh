#!/bin/bash
# R5-05  Sin duplicar reglas y liberacion: procesar, reconciliar y purgar_vencidas (casos 3.5.x).
# Criterio que valida: mitigacion sin saturar la TCAM. Un atacante insistente no llena el switch de reglas y una IP liberada puede volver a bloquearse.
# Cada caso muestra la entrada, lo esperado, el COMANDO que se ejecuta, lo obtenido y las verificaciones (OK / FALLA).
# No necesita ONOS ni Suricata. Uso, desde cualquier carpeta:   bash R5/pruebas/lab7/r5_05.sh
# Guarda la salida en ~/r5_05_<nodo>_<fecha>.txt (evidencia); SIN_ARCHIVO=1 para no guardarla. PY=python para otro interprete.
# Pruebas equivalentes con pytest:  python3 -m pytest R5/tests/test_lab7_r5.py -k R5_05_ -v
source "$(dirname "${BASH_SOURCE[0]}")/comun.sh"                    # funciones comunes; ademas entra a la raiz del repositorio

main() {
  cabecera "R5-05  Sin duplicar reglas y liberacion (3.5.x)" \
    "mitigacion sin saturar la TCAM. Un atacante insistente no llena el switch de reglas y una IP liberada puede volver a bloquearse." \
    "misma IP varias veces -> 1 sola regla, tambien tras un reinicio; flujo borrado o bloqueo vencido -> la IP se olvida; reincidente -> se vuelve a bloquear."

  A="203.0.113.7>10.0.0.50@2"          # alerta de 203.0.113.7 hacia 10.0.0.50 con severidad 2

  # ---- 3.5.1 la misma IP no genera otra regla ----
  caso "3.5.1" "Una IP ya bloqueada no genera otra regla, ni con otra firma"
  dato "Funcion" "procesar(alerta) repetido sobre el mismo estado, con un ONOS falso"
  dato "Entrada" "4 alertas de 203.0.113.7: tres iguales y una con otra firma (sid 9000003, severidad 1)"
  dato "Esperado" "1 solo POST a ONOS: las demas se descartan porque la IP ya esta en la lista"
  correr "$PY $L secuencia $A $A $A '203.0.113.7>10.0.0.50@1#9000003'" "$PY" "$L" secuencia "$A" "$A" "$A" "203.0.113.7>10.0.0.50@1#9000003"
  echo "  Verificaciones:"
  verif "se procesaron 4 alertas" contiene "alertas=4"
  verif "solo 1 POST a ONOS (1 regla en la TCAM)" contiene "POST a ONOS=1"
  fin

  caso "3.5.1b" "Dos IPs distintas generan una regla cada una"
  dato "Funcion" "procesar(alerta) sobre el mismo estado, con un ONOS falso"
  dato "Entrada" "una alerta de 203.0.113.7 y una de 203.0.113.8"
  dato "Esperado" "2 POST a ONOS y las dos IPs bloqueadas"
  correr "$PY $L secuencia $A 203.0.113.8>10.0.0.50@2" "$PY" "$L" secuencia "$A" "203.0.113.8>10.0.0.50@2"
  echo "  Verificaciones:"
  verif "2 POST a ONOS" contiene "POST a ONOS=2"
  verif "las dos IPs quedan bloqueadas" contiene "bloqueadas=203.0.113.7,203.0.113.8"
  fin

  caso "3.5.1c" "No se duplica la regla tras reiniciar el integrador"
  dato "Funcion" "procesar(alerta) + guardar_estado() + cargar_estado() + procesar(alerta)"
  dato "Entrada" "alerta de 203.0.113.7; el integrador se reinicia (el estado se guarda en disco y se recarga); llega otra alerta de la misma IP"
  dato "Esperado" "1 solo POST: tras el reinicio el integrador recuerda que 203.0.113.7 ya esta bloqueada"
  correr "$PY $L secuencia $A reinicio $A" "$PY" "$L" secuencia "$A" reinicio "$A"
  echo "  Verificaciones:"
  verif "el estado recargado conserva la IP" contiene "estado guardado y recargado; bloqueadas=203.0.113.7"
  verif "solo 1 POST a ONOS" contiene "POST a ONOS=1"
  fin

  # ---- 3.5.2 reconciliar ----
  caso "3.5.2" "reconciliar() olvida la IP cuyo flujo ya no existe en ONOS"
  dato "Funcion" "reconciliar(estado)"
  dato "Entrada" "203.0.113.7 bloqueada hace 60 s; ONOS ya no tiene su flujo (lo borro r5ctl desbloquear o expiro por inactividad)"
  dato "Esperado" "la IP se olvida: sus alertas nuevas ya no se ignoran"
  correr "$PY $L reconciliar 203.0.113.7:60 -" "$PY" "$L" reconciliar 203.0.113.7:60 -
  echo "  Verificaciones:"
  verif "antes estaba bloqueada" contiene "antes=203.0.113.7"
  verif "despues no queda ninguna" contiene "despues=(ninguna)"
  fin

  caso "3.5.3" "reconciliar() respeta el periodo de gracia y los flujos vigentes"
  dato "Funcion" "reconciliar(estado)"
  dato "Entrada" "203.0.113.7 recien creada (0 s, ONOS aun no la lista) y 203.0.113.8 de hace 60 s con su flujo vigente en ONOS"
  dato "Esperado" "las dos se conservan"
  correr "$PY $L reconciliar 203.0.113.7:0,203.0.113.8:60 203.0.113.8" "$PY" "$L" reconciliar 203.0.113.7:0,203.0.113.8:60 203.0.113.8
  echo "  Verificaciones:"
  verif "ambas siguen bloqueadas" contiene "despues=203.0.113.7,203.0.113.8"
  fin

  caso "3.5.3b [1/2]" "reconciliar() sin bloqueos no consulta a ONOS"
  dato "Funcion" "reconciliar(estado)"
  dato "Entrada" "ninguna IP bloqueada"
  dato "Esperado" "0 consultas a ONOS (no se gasta una peticion REST sin necesidad)"
  correr "$PY $L reconciliar - -" "$PY" "$L" reconciliar - -
  echo "  Verificaciones:"
  verif "0 consultas a ONOS" contiene "consultas_a_onos=0"
  fin

  caso "3.5.3b [2/2]" "Si ONOS esta caido al reconciliar, se conserva la lista"
  dato "Funcion" "reconciliar(estado)"
  dato "Entrada" "203.0.113.7 bloqueada hace 60 s; ONOS no responde"
  dato "Esperado" "Reintentar (main() lo captura y lo intenta mas tarde) y la IP se conserva: sin datos de ONOS no se libera a nadie"
  correr "$PY $L reconciliar 203.0.113.7:60 - onos-caido" "$PY" "$L" reconciliar 203.0.113.7:60 - onos-caido
  echo "  Verificaciones:"
  verif "se lanza Reintentar" contiene "Reintentar: ONOS caido"
  verif "la IP se conserva" contiene "despues=203.0.113.7"
  fin

  # ---- 3.5.4 purgar_vencidas ----
  PD=(-1 0 100); PE=("ya vencido (1 s antes)" "vence justo ahora (limite)" "vence dentro de 100 s")
  PO=(203.0.113.7 203.0.113.7 "(ninguna)")
  for i in "${!PD[@]}"; do
    caso "3.5.4 [$((i+1))/3]" "purgar_vencidas(): ${PE[$i]}"
    dato "Funcion" "purgar_vencidas(estado, ahora=1000)"
    dato "Entrada" "203.0.113.7 con vencimiento en 1000 + (${PD[$i]}) = $((1000 + PD[i]))"
    dato "Esperado" "olvidadas=${PO[$i]}"
    correr "$PY $L purgar 203.0.113.7:${PD[$i]}" "$PY" "$L" purgar "203.0.113.7:${PD[$i]}"
    echo "  Verificaciones:"
    verif "olvidadas = ${PO[$i]}" contiene "olvidadas=${PO[$i]}"
    fin
  done

  # ---- 3.5.5 reincidencia ----
  caso "3.5.5" "Tras liberar la IP con r5ctl desbloquear, si reincide se vuelve a bloquear"
  dato "Funcion" "procesar(alerta) + reconciliar(estado) + procesar(alerta)"
  dato "Entrada" "alerta de 203.0.113.7; un administrador la desbloquea (ONOS pierde el flujo); la reconciliacion la olvida; llega otra alerta"
  dato "Esperado" "2 POST a ONOS en total y la IP bloqueada de nuevo"
  correr "$PY $L secuencia $A liberar:203.0.113.7 $A" "$PY" "$L" secuencia "$A" liberar:203.0.113.7 "$A"
  echo "  Verificaciones:"
  verif "la reconciliacion la olvida" contiene "olvidadas=203.0.113.7"
  verif "2 POST a ONOS en total" contiene "POST a ONOS=2"
  verif "queda bloqueada otra vez" contiene "bloqueadas=203.0.113.7"
  fin

  caso "3.5.5b" "Al caducar el bloqueo (300 s sin actividad), si la IP reincide se vuelve a bloquear"
  dato "Funcion" "procesar(alerta) + purgar_vencidas(estado) + procesar(alerta)"
  dato "Entrada" "alerta de 203.0.113.7; pasan mas de BLOCK_SEC = 300 s sin actividad; llega otra alerta"
  dato "Esperado" "2 POST a ONOS en total y la IP bloqueada de nuevo"
  correr "$PY $L secuencia $A vencer $A" "$PY" "$L" secuencia "$A" vencer "$A"
  echo "  Verificaciones:"
  verif "el bloqueo vencido se olvida" contiene "olvidadas=203.0.113.7"
  verif "2 POST a ONOS en total" contiene "POST a ONOS=2"
  fin

  resumen R5-05
}

ejecutar R5-05
