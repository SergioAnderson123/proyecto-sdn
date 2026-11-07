# Paso 3 en VNRT: permisos MINIMOS para que R5 corra sin pedir la contrasena de sudo en cada ataque.
#  - sudoers acotado (/etc/sudoers.d/r5-lab): solo estos programas, nada mas.
#      controller: suricata, tcpdump, pkill        h1: tcpdump        h3 y h4: nmap, hping3, tcpdump
#    AVISO: nmap y tcpdump permiten ejecutar comandos como root; es aceptable en estas VM del laboratorio, pero
#    conviene retirarlo al terminar:  sudo rm /etc/sudoers.d/r5-lab   (ver vnrt_limpiar.ps1)
#  - una llave SSH propia del controller en h1, h3 y h4 para que los escenarios lancen los ataques desde alli.
# Te pide la contrasena de ubuntu varias veces (la escribes tu).
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"

function Dar-Sudo($nombre, $puerto, $programas) {
    Write-Host "=== sudoers acotado en $nombre ===" -ForegroundColor Cyan
    $linea = "ubuntu ALL=(root) NOPASSWD: $programas"
    $cmd = "echo '$linea' | sudo tee /etc/sudoers.d/r5-lab >/dev/null && sudo chmod 440 /etc/sudoers.d/r5-lab && sudo visudo -cf /etc/sudoers.d/r5-lab"
    ssh -tt -i $k -p $puerto "ubuntu@$ip" $cmd
}
Dar-Sudo 'controller' 5800 '/usr/bin/suricata, /usr/bin/tcpdump, /usr/bin/pkill'
Dar-Sudo 'h1' 5811 '/usr/bin/tcpdump'
Dar-Sudo 'h3' 5813 '/usr/bin/nmap, /usr/sbin/hping3, /usr/bin/tcpdump'
Dar-Sudo 'h4' 5814 '/usr/bin/nmap, /usr/sbin/hping3, /usr/bin/tcpdump'

Write-Host "=== llave SSH del controller hacia h1, h3 y h4 ===" -ForegroundColor Cyan
$cmd = 'test -f ~/.ssh/id_ed25519 || ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/id_ed25519; for h in 192.168.0.21 192.168.0.23 192.168.0.24; do ssh-copy-id -o StrictHostKeyChecking=accept-new ubuntu@$h; done; echo PASO3_TERMINADO'
ssh -tt -i $k -p 5800 "ubuntu@$ip" $cmd
