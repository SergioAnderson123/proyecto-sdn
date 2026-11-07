# Corre los escenarios de ataque del laboratorio R5 uno por uno y guarda una captura del panel al final de cada uno.
# Requiere el laboratorio levantado (docker compose -f R5/lab/docker/compose.yml up -d) y el panel en http://localhost:8080
param([string[]]$Escenarios = @('scan', 'rafaga', 'multi', 'web', 'c2', 'nat', 'flood'))

$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$carpeta = Join-Path $PSScriptRoot 'capturas-sem10'
New-Item -ItemType Directory -Force $carpeta | Out-Null

$n = 0
foreach ($e in $Escenarios) {
    $n++
    Write-Host ""
    Write-Host "=== Escenario $n : $e ===" -ForegroundColor Cyan
    docker exec r5-lab python3 /opt/r5/lab/docker/attacks.py $e
    Start-Sleep -Seconds 6     # deja que ONOS actualice los contadores del bloqueo
    $png = Join-Path $carpeta ('{0:00}-{1}.png' -f $n, $e)
    & $edge --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 --virtual-time-budget=6000 "--screenshot=$png" http://localhost:8080 2>$null | Out-Null
    Write-Host "captura: $png" -ForegroundColor Green
}

# Evidencia (pcap y JSON de mediciones) del volumen del contenedor
$ev = Join-Path $PSScriptRoot 'evidencia'
New-Item -ItemType Directory -Force $ev | Out-Null
docker cp r5-lab:/evidencia/. $ev
Write-Host "evidencia copiada a $ev" -ForegroundColor Green
