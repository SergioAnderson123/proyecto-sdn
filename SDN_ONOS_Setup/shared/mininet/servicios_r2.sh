#!/bin/bash
# Levanta los servicios de prueba de R2 (policy.yaml, seccion `services`) como servidores HTTP en h4 y h5.
# Uso (dentro del contenedor mininet):  /root/shared-host/servicios_r2.sh
aqui="$(dirname "$0")"
mkdir -p /tmp/srv
pkill -f "http.server 8[01][0-9][0-9]" 2>/dev/null
declare -A catalogo=(
  [h4]="virtual_library:8001 learning_platform:8002 university_portal:8003"
  [h5]="grade_system:8101 student_records:8102 research_database:8103 print_server:8104 network_management:8105"
)
for h in h4 h5; do
  for par in ${catalogo[$h]}; do
    nombre="${par%%:*}"; puerto="${par##*:}"
    mkdir -p "/tmp/srv/$puerto"; echo "$nombre" > "/tmp/srv/$puerto/index.html"
    nohup "$aqui/mnx.sh" "$h" python3 -m http.server "$puerto" --directory "/tmp/srv/$puerto" >/dev/null 2>&1 &
  done
done
sleep 2
echo "servidores: $(pgrep -fc 'http.server 8[01][0-9][0-9]')"
