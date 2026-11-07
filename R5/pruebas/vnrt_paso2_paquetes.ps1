# Paso 2 en VNRT: instala lo necesario para R5 (necesita sudo en cada VM; la contrasena la escribes tu).
#  controller : suricata (IDS; se instala pero NO se deja corriendo como servicio)
#  h1         : servidor web de prueba (python http.server en el puerto 80)
#  h3         : nmap, hping3 (atacante interno para R3/R4)
#  h4         : nmap, hping3 y alias IP 10.0.0.101-104, .110, .120 en ens4 (simulan atacantes externos, NAT y C2)
# No modifica ONOS ni los switches.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"
$apt = 'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends'

$pasos = @(
    @('controller', 5800, "$apt suricata; sudo systemctl disable --now suricata; df -h / | tail -1; echo CONTROLLER_OK"),
    @('h1', 5811, "sudo mkdir -p /tmp/www && echo R5-servidor | sudo tee /tmp/www/index.html >/dev/null; sudo setsid nohup python3 -m http.server 80 --directory /tmp/www >/tmp/http.log 2>&1 < /dev/null & sleep 2; echo H1_OK"),
    @('h3', 5813, "$apt nmap hping3 tcpdump; echo H3_OK"),
    @('h4', 5814, "$apt nmap hping3 tcpdump; for n in 101 102 103 104 110 120; do sudo ip addr add 10.0.0.`$n/24 dev ens4 2>/dev/null; done; ip -br a show ens4; echo H4_OK")
)
foreach ($p in $pasos) {
    Write-Host "=== $($p[0]) ===" -ForegroundColor Cyan
    ssh -tt -i $k -p $p[1] "ubuntu@$ip" $p[2]
}
