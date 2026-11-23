# Abre Windows Terminal con 4 paneles (2x2) que muestran en vivo ~/r5_live.log de cada nodo:
#   arriba-izq: vm-atacante   arriba-der: node-sec-1
#   abajo-izq : switch-borde  abajo-der : node-sec-2
# Requiere las entradas r5-atacante, r5-borde, r5-sec1 y r5-sec2 en ~/.ssh/config.
function Panel($h) { "ssh $h tail -n 30 -F r5_live.log" }
$a = "new-tab --title R5-en-vivo " + (Panel "r5-atacante") +
     " ; split-pane -V " + (Panel "r5-sec1") +
     " ; split-pane -H " + (Panel "r5-sec2") +
     " ; move-focus left ; split-pane -H " + (Panel "r5-borde")
Start-Process wt -ArgumentList $a
