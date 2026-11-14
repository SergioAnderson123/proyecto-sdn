#!/bin/bash
# Lanza el despliegue de la topologia de 14 VM desde switch-borde.
# Uso:  ./lanzar.sh                       # todo (fase 1)
#       ./lanzar.sh --tags comprobar      # solo las comprobaciones (no instala nada)
#       ./lanzar.sh --tags controller     # solo ONOS y compañia
#       ./lanzar.sh --tags switches,estado
#       ./lanzar.sh -e liberar_firmware=true    # purga linux-firmware si falta disco
# La contraseña de ubuntu se pide una vez y no se guarda: va a un archivo temporal en RAM que se borra al terminar.
set -e
cd "$(dirname "$0")"

command -v ansible-playbook >/dev/null || {
  echo "[*] Instalando Ansible (una vez)..."
  sudo apt-get update -qq && sudo apt-get install -y ansible sshpass
}
command -v sshpass >/dev/null || sudo apt-get install -y sshpass

read -r -s -p "Contraseña de ubuntu (SSH y sudo de todas las VM): " PW; echo
VARS="$(mktemp -p /dev/shm 2>/dev/null || mktemp)"
trap 'shred -u "$VARS" 2>/dev/null || rm -f "$VARS"' EXIT
chmod 600 "$VARS"
printf 'ansible_password: "%s"\nansible_become_password: "%s"\n' "$PW" "$PW" > "$VARS"
unset PW

ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook -i inventario.ini site.yml -e "@$VARS" "$@"
