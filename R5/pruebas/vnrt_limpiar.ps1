# Retira lo que R5 dejo en VNRT al terminar: sudoers acotado, llave del controller en h1/h3/h4 y la llave vnrt_r5 en las 8 VM.
# Pide la contrasena de ubuntu (la escribes tu). Ejecutalo al terminar las pruebas. No toca ONOS ni los switches.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"
foreach ($p in 5800, 5811, 5813, 5814) {
    Write-Host "=== sudoers r5-lab en puerto $p ===" -ForegroundColor Cyan
    ssh -tt -i $k -p $p "ubuntu@$ip" 'sudo rm -f /etc/sudoers.d/r5-lab; echo sudoers_retirado'
}
Write-Host "=== llave del controller en h1, h3 y h4 ===" -ForegroundColor Cyan
foreach ($p in 5811, 5813, 5814) {
    ssh -tt -i $k -p $p "ubuntu@$ip" "sed -i '/ubuntu@controller/d' ~/.ssh/authorized_keys; echo llave_controller_retirada"
}
Write-Host "=== llave vnrt_r5 en las 8 VM (al final: ya no podre entrar) ===" -ForegroundColor Cyan
foreach ($p in 5800, 5801, 5802, 5803, 5811, 5812, 5813, 5814) {
    ssh -tt -i $k -p $p "ubuntu@$ip" "sed -i '/r5-vnrt/d' ~/.ssh/authorized_keys; echo llave_retirada_$p"
}
