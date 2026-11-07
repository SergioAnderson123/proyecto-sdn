# Paso 3c: copia la llave del controller a h2 (192.168.0.22). El escenario C2 necesita un host interno que cruce el Core
# (h3 y h4 cuelgan del mismo switch sw3). Te pide la contrasena de ubuntu UNA vez.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"
ssh -tt -i $k -p 5800 "ubuntu@$ip" 'ssh-copy-id -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ubuntu@192.168.0.22; echo PASO3C_TERMINADO'
