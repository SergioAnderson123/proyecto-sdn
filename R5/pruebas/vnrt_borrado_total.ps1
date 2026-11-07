# Borrado total de lo que R5 dejo en VNRT (lo que no se pudo hacer sin sudo). Pide la contrasena de ubuntu 8 veces
# (controller, h1, h3, h4, sw1, sw2, sw3 y una mas si sudo expira); al final retira las llaves SSH y ya no habra acceso.
# Ya borrado antes por Claude: flujos R5 y espejo en ONOS, app fwd desactivada, procesos de R5, ~/r5 y archivos de diagnostico.
# Se purgan solo los paquetes principales que instalamos (suricata, nmap, hping3); las bibliotecas compartidas
# (libblas3, liblua5.4-0, libssh2-1t64 y las de suricata) se dejan porque otros programas, como curl, pueden usarlas.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"
function Ejecutar($nombre, $puerto, $cmd) {
    Write-Host "=== $nombre ===" -ForegroundColor Cyan
    ssh -tt -i $k -p $puerto "ubuntu@$ip" $cmd
}
$quitarSudoers = 'sudo rm -f /etc/sudoers.d/r5-lab'
Ejecutar 'controller' 5800 "sudo DEBIAN_FRONTEND=noninteractive apt-get purge -y suricata libhtp2; sudo rm -rf /etc/suricata /var/lib/suricata /var/log/suricata; sudo docker rm -f onos; $quitarSudoers; echo CONTROLLER_LIMPIO"
Ejecutar 'h1' 5811 "sudo pkill -f '[h]ttp.server 80'; sudo rm -rf /tmp/www /tmp/http.log; $quitarSudoers; echo H1_LIMPIO"
$alias = 'for n in 101 102 103 104 110 120; do sudo ip addr del 10.0.0.$n/24 dev ens4 2>/dev/null; done'
Ejecutar 'h3' 5813 "sudo DEBIAN_FRONTEND=noninteractive apt-get purge -y nmap nmap-common hping3 liblinear4; $quitarSudoers; echo H3_LIMPIO"
Ejecutar 'h4' 5814 "sudo DEBIAN_FRONTEND=noninteractive apt-get purge -y nmap nmap-common hping3 liblinear4; $alias; $quitarSudoers; echo H4_LIMPIO"
foreach ($sw in @(@('sw1', 5801), @('sw2', 5802), @('sw3', 5803))) {
    Ejecutar $sw[0] $sw[1] "sudo ovs-vsctl del-controller $($sw[0]); sudo ovs-ofctl -O OpenFlow13 del-flows $($sw[0]); sudo ovs-vsctl show | head -8; echo $($sw[0])_REINICIADO"
}
Write-Host "=== retirando llaves SSH (ultimo paso) ===" -ForegroundColor Cyan
foreach ($p in 5811, 5812, 5813, 5814) {
    ssh -tt -i $k -p $p "ubuntu@$ip" "sed -i '/ubuntu@controller/d' ~/.ssh/authorized_keys; echo llave_controller_retirada_$p"
}
foreach ($p in 5800, 5801, 5802, 5803, 5811, 5812, 5813, 5814) {
    ssh -tt -i $k -p $p "ubuntu@$ip" "sed -i '/r5-vnrt/d' ~/.ssh/authorized_keys; echo llave_vnrt_r5_retirada_$p"
}
