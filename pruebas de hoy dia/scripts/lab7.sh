#!/bin/bash
# Corre UNA prueba comun del Lab 7 con encabezado legible. Uso: bash lab7.sh R1-01 | R2-03 | R5-02 ...
# Guarda la salida en ~/lab7_<codigo>_<nodo>_<fecha>.txt (evidencia) y la copia a ~/r5_live.log (visor de paneles).
COD="$1"; N="${COD#R?-}"; R="${COD%-*}"
cd "${REPO:-$HOME/proyecto-sdn}" || exit 1
PY="${PY:-python3}"
case "$COD" in
R1-01) F=R1/shared/policy_mapper/tests/test_lab7_r1.py; T="R1-01  Credenciales validas -> autentica y devuelve el rol (1.1.x)"; C="Control de acceso por rol (R1)"
   E="Usuario registrado con su clave: se autentica, obtiene su rol por grupo LDAP y se registra su sesion." ;;
R1-02) F=R1/shared/policy_mapper/tests/test_lab7_r1.py; T="R1-02  Credenciales invalidas (1.2.x)"; C="Rechazo seguro (R1)"
   E="Clave mala, usuario inexistente, vacios e inyecciones: se rechazan sin excepcion, con el MISMO mensaje y sin rol ni sesion." ;;
R1-03) F=R1/shared/policy_mapper/tests/test_lab7_r1.py; T="R1-03  Bloqueo por intentos fallidos (1.3.x)"; C="Proteccion de cuentas (R1)"
   E="Con N-1 fallos la cuenta sigue habilitada; al llegar a N se bloquea durante el tiempo definido y luego se levanta." ;;
R2-01) F=R2/policy/tests/test_lab7_r2.py; T="R2-01  Decision de acceso rol -> recurso (2.1.x)"; C="Restriccion de recursos privilegiados (R2)"
   E="Administrador: PERMIT a la consola SDN. Docente: DENY con motivo y sin ninguna regla que permita el acceso." ;;
R2-02) F=R2/policy/tests/test_lab7_r2.py; T="R2-02  Rol o recurso inexistente (2.2.x)"; C="Denegacion por defecto (R2)"
   E="Un rol o recurso no registrado se deniega siempre (DENY), y cada tabla de rol termina en un DROP explicito." ;;
R2-03) F=R2/policy/tests/test_lab7_r2.py; T="R2-03  Traduccion de la politica a OpenFlow (2.3.x)"; C="Reglas OpenFlow por rol (R2)"
   E="Reglas con match, accion y prioridad por servicio permitido; si una politica permite y otra deniega, prevalece DENY." ;;
R5-0[1-6]) F=R5/tests/test_lab7_r5.py; E=""; C="Seguridad perimetral (R5)"; T="$COD (3.${N#0}.x)"
   case "$COD" in
   R5-01) T="R5-01  parse_ids_alert: lectura de la alerta del IDS (3.1.x)"; E="Alerta valida -> devuelve IP, puertos, protocolo, firma y severidad. Incompleta, corrupta o que no es alerta -> None sin excepcion. Linea a medio escribir -> no avanza el offset." ;;
   R5-02) T="R5-02  Decision segun severidad y direccion (3.2.x)"; E="Severidad 1-2 bloquea, 3-4 no. Externo->interno bloquea en cores; interno->externo en el borde." ;;
   R5-03) T="R5-03  Lista blanca (3.3.x)"; E="Una IP de la lista blanca nunca se bloquea; su vecina no listada si." ;;
   R5-04) T="R5-04  Regla DROP del perimetro (3.4.x)"; E="Prioridad 42000, timeout 300 s, solo ipv4_src de esa IP, en todos los cores; incidente con tiempos y flow_id." ;;
   R5-05) T="R5-05  Sin duplicar y liberacion (3.5.x)"; E="Misma IP 3 veces -> 1 regla. Flujo borrado -> se olvida la IP. Reincidente -> se vuelve a bloquear." ;;
   R5-06) T="R5-06  Resiliencia ante fallas de ONOS (3.6.x)"; E="Nodo caido o 5xx -> siguiente nodo. 401/403/408/429 -> reintento. 4xx -> descarta." ;;
   esac ;;
*) echo "uso: bash lab7.sh R1-01..03 | R2-01..03 | R5-01..06"; exit 2 ;;
esac
OUT=~/lab7_${COD}_$(hostname)_$(date +%Y%m%d_%H%M%S).txt
{
  printf '\n\n'
  echo "################################################################################"
  echo "  $T"
  echo "  Nodo: $(hostname)   Fecha: $(date '+%F %T')   Commit: $(git rev-parse --short HEAD)"
  echo "  Criterio que valida: $C"
  echo "  Esperado: $E"
  echo "################################################################################"
  $PY -m pytest "$F" -v -rs -p no:cacheprovider -k "${R}_${N}_" 2>&1 | sed -E 's#^[A-Za-z0-9_/]+test_lab7_r[0-9]\.py::test_##'
  echo "RESULTADO $COD: exit=${PIPESTATUS[0]}"
  # Salida caso por caso (entrada, comando, obtenido y verificaciones) del script propio de la prueba, si existe: R5/pruebas/lab7/r5_01.sh ...
  if [ "$R" = "R5" ] && [ -f "R5/pruebas/lab7/r5_${N}.sh" ]; then
    echo; echo "=================== CASOS DE $COD ==================="
    SIN_ARCHIVO=1 PY="$PY" bash "R5/pruebas/lab7/r5_${N}.sh" 2>&1
    echo "RESULTADO CASOS $COD: exit=$?"
  fi
} | tee "$OUT" | tee -a ~/r5_live.log
echo "Evidencia: $OUT"
