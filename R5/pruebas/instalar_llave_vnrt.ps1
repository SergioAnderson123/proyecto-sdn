# Instala la llave publica vnrt_r5 en las VM del curso (VNRT) para que Claude pueda operar por SSH sin contrasena.
# Cada VM pide su contrasena una sola vez (la escribes tu en este terminal). Puertos: controller 5800, sw1-3 5801-5803, h1-h4 5811-5814.
$ip = '10.20.12.170'
$pub = Get-Content "$env:USERPROFILE\.ssh\vnrt_r5.pub" -Raw
foreach ($p in 5800, 5801, 5802, 5803, 5811, 5812, 5813, 5814) {
    Write-Host "=== VM puerto $p ===" -ForegroundColor Cyan
    $pub | ssh -p $p -o StrictHostKeyChecking=accept-new "ubuntu@$ip" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo llave instalada en `$(hostname)"
}
