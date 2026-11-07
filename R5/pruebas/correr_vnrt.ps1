# Corre los escenarios de R5 en VNRT (desde el controller) y captura el panel del controller por un tunel SSH.
# Requiere: pasos 1-3b de VNRT hechos y la llave vnrt_r5. Capturas en capturas-vnrt\, resultados en evidencia-vnrt\.
param([string[]]$Escenarios = @('scan', 'rafaga', 'multi', 'web', 'c2', 'nat', 'flood', 'r3', 'r4udp', 'r4icmp'))
$ip = '10.20.12.170'; $k = "$env:USERPROFILE\.ssh\vnrt_r5"
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$carpeta = Join-Path $PSScriptRoot 'capturas-vnrt'; New-Item -ItemType Directory -Force $carpeta | Out-Null
$tunel = Start-Process ssh -ArgumentList "-N -i `"$k`" -o BatchMode=yes -L 18080:localhost:8080 -p 5800 ubuntu@$ip" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
$orden = @('scan', 'rafaga', 'multi', 'web', 'c2', 'nat', 'flood', 'r3', 'r4udp', 'r4icmp')
foreach ($e in $Escenarios) {
    $n = $orden.IndexOf($e) + 1
    Write-Host "=== VNRT escenario $n : $e ===" -ForegroundColor Cyan
    ssh -i $k -o BatchMode=yes -p 5800 "ubuntu@$ip" "cd ~/r5 && set -a && . ./r5.env && set +a && python3 lab/docker/attacks.py $e 2>&1 | grep -E 'Medici|Traceback|rror'"
    Start-Sleep -Seconds 5
    $png = Join-Path $carpeta ('{0:00}-{1}.png' -f $n, $e)
    & $edge --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 --virtual-time-budget=6000 "--screenshot=$png" http://localhost:18080 2>$null | Out-Null
    Write-Host "captura: $png" -ForegroundColor Green
}
$ev = Join-Path $PSScriptRoot 'evidencia-vnrt'; New-Item -ItemType Directory -Force $ev | Out-Null
scp -i $k -P 5800 "ubuntu@${ip}:r5/evidencia/*.json" $ev
ssh -i $k -o BatchMode=yes -p 5800 "ubuntu@$ip" "cd ~/r5 && set -a && . ./r5.env && set +a && python3 cli/r5ctl.py estado" | Out-File (Join-Path $carpeta 'r5ctl-estado.txt') -Encoding utf8
Stop-Process -Id $tunel.Id -Force
