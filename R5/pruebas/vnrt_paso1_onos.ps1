# Paso 1 en VNRT: levanta ONOS en el controller y conecta sw1-sw3 con el bootstrap oficial del curso (repo TEL354_LAB5_2026_2).
# Es el mismo procedimiento del Lab 5: imagen ONOS 2.7 del curso + ansible/bootstrap.yml. No toca nada de R1/R2.
# Pide la contrasena de sudo (y quiza "BECOME password") una vez: la escribes tu.
$ip = '10.20.12.170'
$k = "$env:USERPROFILE\.ssh\vnrt_r5"
$cmd = "sudo docker start onos 2>/dev/null || sudo docker run -d --name onos --network host --restart unless-stopped ghcr.io/nohafeynman/tel354-onos:2026-2; " +
       "sudo bash -c 'cd /root/TEL354_LAB5_2026_2/ansible && ansible-playbook bootstrap.yml'; " +
       "curl -s -u onos:rocks http://localhost:8181/onos/v1/devices | head -c 300; echo; echo PASO1 TERMINADO"
ssh -tt -i $k -p 5800 "ubuntu@$ip" $cmd
