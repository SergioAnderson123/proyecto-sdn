#!/bin/bash
# R5-02  classify_alert(alerta) y decidir(evento): decision segun la severidad y la direccion del trafico (casos 3.2.x).
# Criterio que valida: mitigacion. Solo las alertas graves bloquean, y el bloqueo va al switch correcto (cores o borde del host).
# Cada caso muestra la entrada, lo esperado, el COMANDO que se ejecuta, lo obtenido y las verificaciones (OK / FALLA).
# No necesita ONOS ni Suricata. Uso, desde cualquier carpeta:   bash R5/pruebas/lab7/r5_02.sh
# Guarda la salida en ~/r5_02_<nodo>_<fecha>.txt (evidencia); SIN_ARCHIVO=1 para no guardarla. PY=python para otro interprete.
# Pruebas equivalentes con pytest:  python3 -m pytest R5/tests/test_lab7_r5.py -k R5_02_ -v
source "$(dirname "${BASH_SOURCE[0]}")/comun.sh"                    # funciones comunes; ademas entra a la raiz del repositorio

main() {
  cabecera "R5-02  classify_alert y decidir: decision segun severidad y direccion (3.2.x)" \
    "mitigacion. Solo las alertas graves bloquean, y el bloqueo va al switch correcto." \
    "severidad 1-2 -> bloquear; 3 o mas -> registrar; <1 -> ignorar. Externo->interno bloquea en los cores; interno->externo en el borde del host."

  # ---- 3.2.1 classify_alert segun la severidad (limite: MAX_SEVERITY = 2) ----
  SEV=(1 2 3 4 99); ESP=(bloquear bloquear registrar registrar registrar)
  DES=("severidad 1 (la mas grave)" "severidad 2 (limite: todavia bloquea)" "severidad 3 (justo por encima del limite)" "severidad 4" "severidad 99")
  for i in "${!SEV[@]}"; do
    caso "3.2.1 [$((i+1))/5]" "classify_alert: ${DES[$i]}"
    dato "Funcion" "classify_alert(alerta)   con MAX_SEVERITY=2"
    dato "Entrada" "alerta con severidad ${SEV[$i]}"
    dato "Esperado" "accion=${ESP[$i]}"
    correr "$PY $L classify ${SEV[$i]}" "$PY" "$L" classify "${SEV[$i]}"
    echo "  Verificaciones:"
    verif "la accion es ${ESP[$i]}" contiene "accion=${ESP[$i]}"
    fin
  done

  # ---- 3.2.1b el limite es configurable ----
  MAX=(1 1 3 3); SEV=(1 2 3 4); ESP=(bloquear registrar bloquear registrar)
  for i in "${!SEV[@]}"; do
    caso "3.2.1b [$((i+1))/4]" "El limite es configurable: MAX_SEVERITY=${MAX[$i]}, severidad ${SEV[$i]}"
    dato "Funcion" "classify_alert(alerta)   con MAX_SEVERITY=${MAX[$i]}"
    dato "Entrada" "alerta con severidad ${SEV[$i]}"
    dato "Esperado" "accion=${ESP[$i]}"
    correr "MAX_SEVERITY=${MAX[$i]} $PY $L classify ${SEV[$i]}" env MAX_SEVERITY="${MAX[$i]}" "$PY" "$L" classify "${SEV[$i]}"
    echo "  Verificaciones:"
    verif "el limite aplicado es ${MAX[$i]}" contiene "maximo_para_bloquear=${MAX[$i]}"
    verif "la accion es ${ESP[$i]}" contiene "accion=${ESP[$i]}"
    fin
  done

  # ---- 3.2.2 severidad fuera de rango ----
  SEV=(0 -1 -50)
  for i in "${!SEV[@]}"; do
    caso "3.2.2 [$((i+1))/3]" "Severidad fuera de rango (${SEV[$i]}): se ignora, no se toma como la mas grave"
    dato "Funcion" "classify_alert(alerta)"
    dato "Entrada" "alerta con severidad ${SEV[$i]} (Suricata solo usa 1 en adelante)"
    dato "Esperado" "accion=ignorar"
    correr "$PY $L classify ${SEV[$i]}" "$PY" "$L" classify "${SEV[$i]}"
    echo "  Verificaciones:"
    verif "la accion es ignorar" contiene "accion=ignorar"
    fin
  done

  # ---- 3.2.3 procesar solo bloquea si classify_alert lo pide ----
  SEV=(1 2 3 4)
  for i in "${!SEV[@]}"; do
    caso "3.2.3 [$((i+1))/4]" "procesar() con severidad ${SEV[$i]}: ¿se llega a bloquear en ONOS?"
    dato "Funcion" "procesar(alerta) completo con un ONOS falso"
    dato "Entrada" "alerta de 203.0.113.7 hacia 10.0.0.50 con severidad ${SEV[$i]}"
    if [ "${SEV[$i]}" -le 2 ]; then
      dato "Esperado" "accion=bloquear, 203.0.113.7 bloqueada y 1 POST a ONOS"
    else
      dato "Esperado" "accion=registrar, ninguna IP bloqueada y 0 POST a ONOS"
    fi
    correr "$PY $L bloqueo 203.0.113.7 10.0.0.50 ${SEV[$i]}" "$PY" "$L" bloqueo 203.0.113.7 10.0.0.50 "${SEV[$i]}"
    echo "  Verificaciones:"
    if [ "${SEV[$i]}" -le 2 ]; then
      verif "accion = bloquear" contiene "accion=bloquear"
      verif "203.0.113.7 queda bloqueada" contiene "bloqueadas=203.0.113.7"
      verif "se envia 1 POST a ONOS" contiene "POST a ONOS=1"
    else
      verif "accion = registrar" contiene "accion=registrar"
      verif "no se bloquea ninguna IP" contiene "bloqueadas=(ninguna)"
      verif "no se envia ningun POST a ONOS" contiene "POST a ONOS=0"
    fi
    fin
  done

  # ---- 3.2.4 a quien y donde se bloquea segun la direccion del trafico ----
  S4=(203.0.113.7 10.0.0.21 10.0.0.21 10.0.0.50 198.51.100.1)
  D4=(10.0.0.50 198.51.100.9 10.0.0.50 203.0.113.7 203.0.113.7)
  R4=("203.0.113.7,core" "10.0.0.21,borde" "10.0.0.21,borde" "203.0.113.7,core" "None")
  T4=("externo -> interno: se bloquea al atacante externo en los cores" "interno -> externo (posible C2): se bloquea al host interno en su borde"
      "interno -> interno (R3/R4): se bloquea al origen en su borde" "firma sobre la respuesta (to_client): el atacante es el destino"
      "externo -> externo: no es trafico de nuestra red, no se bloquea")
  X4=("" "" "" "to_client" "")
  for i in "${!S4[@]}"; do
    caso "3.2.4 [$((i+1))/5]" "decidir(): ${T4[$i]}"
    dato "Funcion" "decidir(evento)"
    dato "Entrada" "origen ${S4[$i]} -> destino ${D4[$i]}${X4[$i]:+, direccion ${X4[$i]}}   (HOME_NET = 10.0.0.0/24)"
    dato "Esperado" "resultado=${R4[$i]}"
    correr "$PY $L decidir ${S4[$i]} ${D4[$i]} ${X4[$i]}" "$PY" "$L" decidir "${S4[$i]}" "${D4[$i]}" ${X4[$i]}
    echo "  Verificaciones:"
    verif "resultado = ${R4[$i]}" contiene "resultado=${R4[$i]}"
    fin
  done

  # ---- 3.2.5 el externo va a los cores y el interno a su borde ----
  caso "3.2.5" "Un atacante externo se bloquea en el Core y un host interno en su switch de borde"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso (ONOS ve a 10.0.0.21 en el switch of:00000000000000a1)"
  dato "Entrada" "1) 203.0.113.7 -> 10.0.0.50    2) 10.0.0.21 -> 198.51.100.9"
  dato "Esperado" "1) device=of:0000000000000001 (core)    2) device=of:00000000000000a1 (borde del host)"
  correr "$PY $L bloqueo 203.0.113.7 10.0.0.50 1" "$PY" "$L" bloqueo 203.0.113.7 10.0.0.50 1
  S1="$SALIDA"
  correr "$PY $L bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1" "$PY" "$L" bloqueo 10.0.0.21 198.51.100.9 1 10.0.0.21=of:00000000000000a1
  echo "  Verificaciones:"
  verif "el atacante externo se bloquea en el core of:0000000000000001" grep -qF "203.0.113.7 -> device=of:0000000000000001, donde=core" <<<"$S1"
  verif "el host interno se bloquea en su borde of:00000000000000a1" contiene "10.0.0.21 -> device=of:00000000000000a1, donde=borde"
  fin

  resumen R5-02
}

ejecutar R5-02
