# Diagnostico de SOLO LECTURA de VNRT con sudo. Cada VM pide la contrasena de sudo (la escribes tu).
# Deja los resultados en ~/diag_<vm>.txt de cada VM para que Claude los lea; no modifica ninguna configuracion.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"

$ctrl = 'ls -la /root; find / -maxdepth 4 -iname "*onos*" -not -path "/proc/*" 2>/dev/null | head -20; docker ps -a; systemctl list-units --type=service --all | grep -i -E "onos|docker"; ss -ltnp | grep -E "8181|6653|8101"; ip -br a'
$sw = 'ovs-vsctl show; ovs-vsctl get-controller $(ovs-vsctl list-br); ovs-ofctl -O OpenFlow13 dump-flows $(ovs-vsctl list-br) | head -20; ip -br a; which suricata nmap hping3 python3'

foreach ($vm in @(@('controller', 5800, $ctrl), @('sw1', 5801, $sw), @('sw2', 5802, $sw), @('sw3', 5803, $sw))) {
    Write-Host "=== $($vm[0]) ===" -ForegroundColor Cyan
    $cmd = "sudo bash -c '" + $vm[2].Replace("'", "'\''") + "' > ~/diag_$($vm[0]).txt 2>&1; echo listo"
    ssh -tt -i $k -p $vm[1] "ubuntu@$ip" $cmd
}
