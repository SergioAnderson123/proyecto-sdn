#!/bin/sh
# Guion de dhclient para las pruebas de 83_prueba_dhcp.yml: pone SOLO la direccion que da el servidor en la interfaz de prueba. No instala ruta por defecto, ni DNS, ni toca ninguna otra interfaz,
# asi la prueba no puede cortar el acceso del host. Escribe lo recibido en /tmp/dhcp_prueba_<interfaz>.txt.
SALIDA="/tmp/dhcp_prueba_${interface}.txt"
case "$reason" in
  BOUND|RENEW|REBIND|REBOOT)
    ip addr flush dev "$interface" 2>/dev/null
    ip addr add "$new_ip_address/$new_subnet_mask" dev "$interface"
    {
      echo "motivo=$reason"
      echo "ip=$new_ip_address"
      echo "mascara=$new_subnet_mask"
      echo "router=$new_routers"
      echo "servidor=$new_dhcp_server_identifier"
      echo "lease=$new_dhcp_lease_time"
    } > "$SALIDA"
    ;;
  EXPIRE|FAIL|RELEASE|STOP)
    ip addr flush dev "$interface" 2>/dev/null
    echo "motivo=$reason" > "$SALIDA"
    ;;
esac
exit 0
