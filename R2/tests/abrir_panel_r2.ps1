# Abre UNA ventana de Windows Terminal con 3 paneles para seguir R2 en vivo:
#   izquierda        : logs del Policy Mapper (seguir_logs.ps1: se vuelve a enganchar si el contenedor se recrea)
#   arriba derecha   : monitor (TCAM por tabla, sesiones, CPU/RAM y ultimas mediciones)
#   abajo derecha    : shell dentro del contenedor mininet (mnx.sh, ovs-ofctl, ...)
# Uso:  .\tests\abrir_panel_r2.ps1      (desde R2)
# Nota: los comandos de wt no deben llevar ';' (es su separador), por eso la logica va en archivos aparte.
$aqui = Split-Path -Parent $PSScriptRoot
wt -w r2 new-tab --title "R2 en vivo" -d $aqui powershell -NoExit -File tests/seguir_logs.ps1 `; split-pane -V -d $aqui powershell -NoExit -Command "python tests/monitor_r2.py" `; split-pane -H -d $aqui powershell -NoExit -Command "docker exec -it mininet bash"
