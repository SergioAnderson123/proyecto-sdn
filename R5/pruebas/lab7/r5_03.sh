#!/bin/bash
# R5-03  en_lista_blanca(ip) y la lista blanca en decidir/procesar: una IP legitima nunca se bloquea (casos 3.3.x).
# Criterio que valida: falsos positivos. Un servidor protegido no puede quedar aislado aunque una firma salte sobre su trafico.
# Cada caso muestra la entrada, lo esperado, el COMANDO que se ejecuta, lo obtenido y las verificaciones (OK / FALLA).
# No necesita ONOS ni Suricata. Uso, desde cualquier carpeta:   bash R5/pruebas/lab7/r5_03.sh
# Guarda la salida en ~/r5_03_<nodo>_<fecha>.txt (evidencia); SIN_ARCHIVO=1 para no guardarla. PY=python para otro interprete.
# Pruebas equivalentes con pytest:  python3 -m pytest R5/tests/test_lab7_r5.py -k R5_03_ -v
source "$(dirname "${BASH_SOURCE[0]}")/comun.sh"                    # funciones comunes; ademas entra a la raiz del repositorio
en_s1()    { grep -qxF -- "$1" <<<"$S1"; }                       # linea exacta dentro de la salida del primer comando (caso 3.3.3)

main() {
  cabecera "R5-03  en_lista_blanca y lista blanca en decidir/procesar (3.3.x)" \
    "falsos positivos. Un servidor protegido no puede quedar aislado aunque una firma salte sobre su trafico." \
    "una IP de la lista blanca nunca se bloquea; su vecina no listada si; el destino protegido no libera al atacante; los prefijos invalidos se detectan."
  echo "  Lista blanca del laboratorio: 10.0.0.99/32 y 10.0.0.10/32   (HOME_NET = 10.0.0.0/24)"

  # ---- 3.3.1 en_lista_blanca segun la IP ----
  IP1=(10.0.0.99 10.0.0.10 10.0.0.98 10.0.0.100 203.0.113.7 "no es una ip" "")
  ES1=(True True False False False False False)
  DE1=("en la lista" "en la lista" "vecina anterior de 10.0.0.99" "vecina siguiente de 10.0.0.99" "externa que no esta" "dato invalido (texto)" "dato invalido (vacio)")
  for i in "${!IP1[@]}"; do
    caso "3.3.1 [$((i+1))/7]" "en_lista_blanca('${IP1[$i]}'): ${DE1[$i]}"
    dato "Funcion" "en_lista_blanca(ip)"
    dato "Entrada" "'${IP1[$i]}'"
    dato "Esperado" "en_lista_blanca=${ES1[$i]}"
    correr "$PY $L lista-blanca '${IP1[$i]}'" "$PY" "$L" lista-blanca "${IP1[$i]}"
    echo "  Verificaciones:"
    verif "en_lista_blanca = ${ES1[$i]}" contiene "en_lista_blanca=${ES1[$i]}"
    fin
  done

  # ---- 3.3.1b rangos y IPs externas ----
  LB="10.0.0.0/30,192.0.2.5/32"
  IP2=(10.0.0.0 10.0.0.3 10.0.0.4 192.0.2.5 192.0.2.6)
  ES2=(True True False True False)
  DE2=("primer extremo del /30" "ultimo extremo del /30" "justo fuera del /30" "IP externa conocida (p. ej. un NAT de salida)" "vecina de la IP externa")
  for i in "${!IP2[@]}"; do
    caso "3.3.1b [$((i+1))/5]" "La lista acepta rangos y IPs externas: ${IP2[$i]} (${DE2[$i]})"
    dato "Funcion" "en_lista_blanca(ip)   con LISTA_BLANCA=$LB"
    dato "Entrada" "'${IP2[$i]}'"
    dato "Esperado" "en_lista_blanca=${ES2[$i]}"
    correr "LISTA_BLANCA=$LB $PY $L lista-blanca ${IP2[$i]}" env LISTA_BLANCA="$LB" "$PY" "$L" lista-blanca "${IP2[$i]}"
    echo "  Verificaciones:"
    verif "en_lista_blanca = ${ES2[$i]}" contiene "en_lista_blanca=${ES2[$i]}"
    fin
  done

  # ---- 3.3.2 una IP en la lista blanca no se bloquea, aunque la alerta sea grave ----
  S3=(10.0.0.99 10.0.0.10); D3=(203.0.113.7 198.51.100.9)
  for i in "${!S3[@]}"; do
    caso "3.3.2 [$((i+1))/2]" "Host interno en la lista blanca (${S3[$i]}): alerta de severidad 1, no se bloquea"
    dato "Funcion" "procesar(alerta) completo con un ONOS falso"
    dato "Entrada" "alerta grave de ${S3[$i]} hacia ${D3[$i]} (severidad 1)"
    dato "Esperado" "ninguna IP bloqueada y 0 POST a ONOS"
    correr "$PY $L bloqueo ${S3[$i]} ${D3[$i]} 1" "$PY" "$L" bloqueo "${S3[$i]}" "${D3[$i]}" 1
    echo "  Verificaciones:"
    verif "no se bloquea ninguna IP" contiene "bloqueadas=(ninguna)"
    verif "no se envia ningun POST a ONOS" contiene "POST a ONOS=0"
    fin
  done

  caso "3.3.2b" "IP externa en la lista blanca (192.0.2.5): alerta de severidad 1, no se bloquea en el core"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso   con LISTA_BLANCA=192.0.2.5/32"
  dato "Entrada" "alerta grave de 192.0.2.5 hacia 10.0.0.50 (sin la lista blanca seria un bloqueo en los cores)"
  dato "Esperado" "ninguna IP bloqueada y 0 POST a ONOS"
  correr "LISTA_BLANCA=192.0.2.5/32 $PY $L bloqueo 192.0.2.5 10.0.0.50 1" env LISTA_BLANCA=192.0.2.5/32 "$PY" "$L" bloqueo 192.0.2.5 10.0.0.50 1
  echo "  Verificaciones:"
  verif "no se bloquea ninguna IP" contiene "bloqueadas=(ninguna)"
  verif "no se envia ningun POST a ONOS" contiene "POST a ONOS=0"
  fin

  # ---- 3.3.3 la vecina no listada si se bloquea ----
  caso "3.3.3" "IP no listada se bloquea y su vecina de la lista blanca no"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso"
  dato "Entrada" "1) alerta grave de 10.0.0.98 (no listada)    2) alerta grave de 10.0.0.99 (en la lista blanca)"
  dato "Esperado" "1) 10.0.0.98 se bloquea (1 POST)    2) 10.0.0.99 no se bloquea (0 POST)"
  correr "$PY $L bloqueo 10.0.0.98 203.0.113.7 1" "$PY" "$L" bloqueo 10.0.0.98 203.0.113.7 1
  S1="$SALIDA"
  correr "$PY $L bloqueo 10.0.0.99 203.0.113.7 1" "$PY" "$L" bloqueo 10.0.0.99 203.0.113.7 1
  echo "  Verificaciones:"
  verif "10.0.0.98 queda bloqueada" en_s1 "bloqueadas=10.0.0.98"
  verif "10.0.0.99 no se bloquea" contiene "bloqueadas=(ninguna)"
  fin

  # ---- 3.3.4 solo se protege a la IP que se iba a bloquear ----
  caso "3.3.4" "Destino en la lista blanca: el atacante externo SI se bloquea"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso"
  dato "Entrada" "alerta grave de 203.0.113.7 (externo) hacia 10.0.0.10 (servidor en la lista blanca)"
  dato "Esperado" "203.0.113.7 bloqueada: la lista blanca protege a la IP que se bloquearia, no a quien recibe el ataque"
  correr "$PY $L bloqueo 203.0.113.7 10.0.0.10 1" "$PY" "$L" bloqueo 203.0.113.7 10.0.0.10 1
  echo "  Verificaciones:"
  verif "203.0.113.7 queda bloqueada" contiene "bloqueadas=203.0.113.7"
  verif "se envia 1 POST a ONOS" contiene "POST a ONOS=1"
  fin

  caso "3.3.4b" "Firma sobre la respuesta del servidor protegido: se bloquea al atacante, no al servidor"
  dato "Funcion" "procesar(alerta) completo con un ONOS falso"
  dato "Entrada" "alerta grave con origen 10.0.0.10 (servidor en la lista blanca) y destino 203.0.113.7, direccion to_client"
  dato "Esperado" "203.0.113.7 bloqueada (en to_client el atacante es el destino); 10.0.0.10 intacto"
  correr "$PY $L bloqueo 10.0.0.10 203.0.113.7 1 to_client" "$PY" "$L" bloqueo 10.0.0.10 203.0.113.7 1 to_client
  echo "  Verificaciones:"
  verif "solo 203.0.113.7 queda bloqueada" contiene "bloqueadas=203.0.113.7"
  fin

  # ---- 3.3.5 como se lee la lista desde la configuracion ----
  caso "3.3.5" "La lista ignora entradas vacias (espacios y comas sobrantes)"
  dato "Funcion" "redes(variable)"
  dato "Entrada" "'10.0.0.40/32, ,10.0.0.50/32,'"
  dato "Esperado" "redes=10.0.0.40/32,10.0.0.50/32"
  correr "$PY $L redes '10.0.0.40/32, ,10.0.0.50/32,'" "$PY" "$L" redes "10.0.0.40/32, ,10.0.0.50/32,"
  echo "  Verificaciones:"
  verif "quedan exactamente dos prefijos" es "redes=10.0.0.40/32,10.0.0.50/32"
  fin

  V5=("10.0.0.0/33" "no-es-red" "10.0.0.5/24")
  W5=("mascara fuera de rango" "texto que no es una red" "prefijo con bits de host (10.0.0.5/24)")
  for i in "${!V5[@]}"; do
    caso "3.3.5b [$((i+1))/3]" "Prefijo invalido se detecta al arrancar: ${W5[$i]}"
    dato "Funcion" "redes(variable)"
    dato "Entrada" "'${V5[$i]}'"
    dato "Esperado" "ValueError (el integrador no arranca con una lista blanca mal escrita)"
    correr "$PY $L redes '${V5[$i]}'" "$PY" "$L" redes "${V5[$i]}"
    echo "  Verificaciones:"
    verif "se lanza ValueError" contiene "ValueError"
    fin
  done

  resumen R5-03
}

ejecutar R5-03
