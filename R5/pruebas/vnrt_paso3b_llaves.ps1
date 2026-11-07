# Paso 3b: el paso 3 no pudo instalar la llave del controller en h1, h3 y h4 (las huellas SSH antiguas de 192.168.0.x
# del Lab 5 no coincidian). Aqui se ignoran esas huellas viejas. Te pide la contrasena de ubuntu 3 veces.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"
$cmd = 'for h in 192.168.0.21 192.168.0.23 192.168.0.24; do ssh-copy-id -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ubuntu@$h; done; echo PASO3B_TERMINADO'
ssh -tt -i $k -p 5800 "ubuntu@$ip" $cmd
