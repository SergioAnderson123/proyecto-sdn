# Funciones compartidas por las pruebas bash del Lab 7 de R5 (r5_01.sh ... r5_06.sh). No se ejecuta solo: lo cargan con `source`.
# Cada prueba define main() y termina llamando a:  ejecutar R5-0N
# Variables: PY (interprete, python3 por defecto), SIN_ARCHIVO=1 (no guarda la evidencia en ~/r5_0N_<nodo>_<fecha>.txt).
cd "$(dirname "${BASH_SOURCE[0]}")/../../.." || exit 1          # raiz del repositorio
PY="${PY:-python3}"
L="R5/pruebas/lab7/llamar.py"
SEP="----------------------------------------------------------------------------------------------------"
CASOS=0; BIEN=0; MAL=""; FALLAS=0; SALIDA=""

caso()  { COD="$1"; FALLAS=0; echo "$SEP"; echo "[$1] $2"; }
dato()  { printf '  %-10s: %s\n' "$1" "$2"; }
correr() {                                        # correr "texto del comando" cmd args...   (muestra el comando y guarda la salida en $SALIDA)
  local texto="$1"; shift
  dato "Comando" "\$ $texto"
  SALIDA=$("$@" 2>&1)
  echo "  Obtenido  :"; sed 's/^/      /' <<<"$SALIDA"
}
verif() { local d="$1"; shift; if "$@"; then echo "    [OK] $d"; else echo "    [FALLA] $d"; FALLAS=1; fi; }
contiene() { grep -qF -- "$1" <<<"$SALIDA"; }
es()       { [ "$SALIDA" = "$1" ]; }
no_es()    { [ "$SALIDA" != "$1" ]; }
contiene_todo() { local p; for p in "$@"; do contiene "$p" || return 1; done; }
fin() {
  CASOS=$((CASOS+1))
  if [ "$FALLAS" -eq 0 ]; then BIEN=$((BIEN+1)); echo "  RESULTADO $COD: PASS"; else MAL="$MAL $COD"; echo "  RESULTADO $COD: FAIL"; fi
}

cabecera() {                                      # cabecera "R5-0N  titulo (3.N.x)" "criterio" "esperado"
  echo "################################################################################"
  echo "  $1"
  echo "  Nodo: $(hostname)   Fecha: $(date '+%F %T')   Commit: $(git rev-parse --short HEAD 2>/dev/null)"
  echo "  Criterio que valida: $2"
  echo "  Esperado: $3"
  echo "################################################################################"
}

resumen() {                                       # resumen R5-0N   (devuelve 0 si todos los casos pasan)
  echo "===================================================================================================="
  echo "RESUMEN $1: $BIEN de $CASOS casos PASS${MAL:+; FALLAN:$MAL}"
  [ "$BIEN" -eq "$CASOS" ]
}

ejecutar() {                                      # ejecutar R5-0N   (corre main y guarda la evidencia)
  local cod="$1" OUT RC
  if [ -n "$SIN_ARCHIVO" ]; then
    main 2>&1
    exit $?
  fi
  OUT="${OUT:-$HOME/$(echo "$cod" | tr 'A-Z-' 'a-z_')_$(hostname)_$(date +%Y%m%d_%H%M%S).txt}"
  main 2>&1 | tee "$OUT"
  RC=${PIPESTATUS[0]}
  echo "Evidencia: $OUT"
  exit $RC
}
