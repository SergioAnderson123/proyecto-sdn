# Demo de r5ctl + reconciliación (C7): desbloquear una IP y comprobar que el integrador vuelve a bloquearla si reincide.
function Paso($t) { Write-Host ""; Write-Host ">> $t" -ForegroundColor Yellow }
function R5($a) { docker exec r5-lab r5ctl @a }
function Atacar() { docker exec r5-lab hx hext3 curl -s -m 3 -A 'sqlmap/1.7' -o /dev/null -w 'HTTP %{http_code}' http://10.0.0.10/; Write-Host "" }

Paso "1. Ataque web desde hext3 (User-Agent sqlmap): el integrador debe bloquearla"
docker exec r5-lab python3 /opt/r5/lab/docker/attacks.py web | Select-String 'Medición'
Start-Sleep -Seconds 2
R5 @('bloqueos')

Paso "2. El administrador la desbloquea con r5ctl (supongamos que era un falso positivo)"
R5 @('desbloquear', '10.0.1.13')
R5 @('bloqueos')

Paso "3. hext3 vuelve a atacar de inmediato: el integrador aún no ha reconciliado y ignora la alerta (comportamiento de C7)"
Atacar
Start-Sleep -Seconds 3
R5 @('bloqueos')

Paso "4. Espero la reconciliación (10 s): el integrador olvida la IP porque su flujo ya no existe en ONOS"
Start-Sleep -Seconds 12
docker exec r5-lab bash -c 'tail -3 /var/log/r5/integrador.log'

Paso "5. hext3 reincide: ahora sí se vuelve a bloquear"
Atacar
Start-Sleep -Seconds 3
R5 @('bloqueos')

Paso "6. Bloqueo manual y estado general"
R5 @('bloquear', '10.0.1.200', '--seg', '120')
R5 @('estado')
