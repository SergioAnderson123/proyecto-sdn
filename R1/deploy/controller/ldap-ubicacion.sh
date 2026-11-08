#!/bin/bash
# Ajusta en LDAP la MAC, el switch y el puerto de un usuario a partir de lo que ONOS AAA ve (VNRT).
# Se ejecuta en la VM Controller (usa `docker exec ldap` y la REST de ONOS).
#
#   ./ldap-ubicacion.sh ver     <uid>   muestra lo que hay en LDAP y lo que ve ONOS
#   ./ldap-ubicacion.sh liberar <uid>   borra MAC/switch/puerto del usuario (la primera autenticacion pasa sin comprobarlos)
#   ./ldap-ubicacion.sh fijar   <uid>   copia a LDAP la MAC, el switch y el puerto que ONOS vio en su ultima autenticacion
#
# Flujo para dar de alta un host nuevo:  liberar -> autenticar el host (wpa_supplicant) -> fijar -> reiniciar policy_mapper -> autenticar otra vez.
# Un usuario con valores que no coinciden es rechazado Y su MAC queda 300 s en lista negra: por eso se "libera" antes.
set -e
ACCION="${1:-}"; UID_="${2:-}"
[ -n "$ACCION" ] && [ -n "$UID_" ] || { sed -n '2,12p' "$0"; exit 1; }
ONOS_URL="${ONOS_URL:-http://127.0.0.1:8181/onos}"
AUTH="${ONOS_USER:-onos}:${ONOS_PASS:-rocks}"
DN="uid=$UID_,ou=users,dc=example,dc=org"
LDAPARGS=(-x -H ldap://localhost -D cn=admin,dc=example,dc=org -w "${LDAP_ADMIN_PASSWORD:-admin}")

ver_ldap() {
  docker exec ldap ldapsearch "${LDAPARGS[@]}" -LLL -b "$DN" description physicalDeliveryOfficeName roomNumber
}

lo_que_ve_onos() {   # imprime "<mac> <dpid> <puerto>" de la ultima entrada AAA de ese usuario
  curl -s -u "$AUTH" "$ONOS_URL/aaa/app/users" | python3 -c '
import sys, json
uid = sys.argv[1] + "@example.org"
d = json.load(sys.stdin)
filas = d.get("entries", d.get("users", [])) if isinstance(d, dict) else d
mias = [f for f in filas if f.get("username") == uid and "/" in str(f.get("connectPoint", ""))]
if not mias:
    sys.exit(1)
f = mias[-1]
dpid, puerto = f["connectPoint"].rsplit("/", 1)
print(f["macAddress"].lower(), dpid, puerto)
' "$UID_"
}

case "$ACCION" in
  ver)
    echo "== LDAP"; ver_ldap
    echo "== ONOS AAA"; lo_que_ve_onos || echo "(ONOS no tiene una autenticacion de $UID_)"
    ;;
  liberar)
    docker exec -i ldap ldapmodify -c "${LDAPARGS[@]}" >/dev/null 2>&1 <<EOF || true
dn: $DN
changetype: modify
delete: description
-
delete: physicalDeliveryOfficeName
-
delete: roomNumber
EOF
    echo "Liberado $UID_ (sin MAC, switch ni puerto):"; ver_ldap
    ;;
  fijar)
    datos="$(lo_que_ve_onos)" || { echo "ONOS no tiene una autenticacion de $UID_: autentica el host primero (tras 'liberar')."; exit 1; }
    read -r MAC DPID PUERTO <<<"$datos"
    echo "ONOS ve a $UID_ en $DPID puerto $PUERTO con MAC $MAC"
    docker exec -i ldap ldapmodify "${LDAPARGS[@]}" >/dev/null <<EOF
dn: $DN
changetype: modify
replace: description
description: $MAC
-
replace: physicalDeliveryOfficeName
physicalDeliveryOfficeName: $DPID
-
replace: roomNumber
roomNumber: $PUERTO
EOF
    echo "Fijado en LDAP:"; ver_ldap
    echo "Siguiente: docker restart policy_mapper y volver a autenticar el host."
    ;;
  *) echo "Accion desconocida: $ACCION"; exit 1 ;;
esac
