# Nota para R5: el exporter lee `/devices` de un nodo de ONOS con la vista mala (23-nov)

Para quien mantiene `R5/codigo/exporter.py` (Sergio). No he tocado nada de R5; esto es lo que vimos al investigar los timeouts de ONOS el 23-nov. Los datos son de la red en vivo (horas UTC de los controladores); lo que es hipótesis está marcado.

## 1. Qué pasa con `r5_dispositivos_conectados`

- El exporter toma `ONOS = ONOS_URL.split(",")[0]` y de ahí lee `/devices` y `/flows/{switch}`. Con `topologia.slice14.yaml` ese primer nodo es **ctrl-1** (`192.168.201.1`).
- Desde las ~18:15 el ONOS de ctrl-1 (y el de ctrl-3) devuelve los 5 switches con **`available=false`**, mientras que ctrl-2 (que manda en los 5) los devuelve con `available=true`. Visto a las 20:17 en el registrador nuevo: `ctrl1 dev=200/15ms/0de5`, `ctrl2 dev=200/13ms/5de5`, `ctrl3 dev=200/14ms/0de5`.
- Efecto en tu monitoreo: `r5_dispositivos_conectados` (que cuenta los `available`) debe marcar **0** en Grafana, aunque los switches están conectados y reenviando. `r5_flujos_switch` y el espejo no deberían verse afectados porque salen de `/flows`, que no depende de `available`.
- **Causa: no se sabe.** Empezó tras reiniciar el ONOS de ctrl-2 (≈18:14) y luego el de ctrl-1 (≈18:23), que movieron el mando. Reiniciar no la corrigió. Hipótesis sin probar: la réplica no recibió el aviso de «switch disponible» tras el cambio de mando. Detalle en `docs/DISENO_CAPA3.md`, V9 quinquies.

## 2. Qué te pedimos o proponemos (tú decides)

1. Que el exporter no se fíe del primer nodo para `available`: si el nodo en uso trae todos los switches caídos, probar con los demás y quedarse con el que ve alguno (es lo que hace ahora `R2/policy/onos_multi.py` para el vigilante), o leer el estado de cada switch en su nodo maestro (`GET /mastership/{dpid}/master`).
2. Comprobar si `integrador.py` usa `/devices` o `available` del primer nodo para algo que decida (por ejemplo, saber qué switch aplicar). No lo hemos revisado.
3. Si tu dashboard marca 0 dispositivos desde las ~18:15, es esto y no una caída.

## 3. Sobre la carga del exporter (no es el origen del problema, pero suma)

- Por cada consulta de Prometheus (cada 5 s, a los 2 sensores) el exporter hace unas 10 llamadas a ONOS: `/flows/application/org.campus.r5`, `/flows/{core}` de cada core, `/devices` y `/flows/{id}` de los 5 switches. Son unas 4 llamadas por segundo, todas al mismo nodo.
- ONOS tenía rachas de timeouts de Atomix (`onos-pipeconf-table`, `flow-store-...: timed out in 15000 ms`) que **empezaron antes** de que arrancara tu exporter (rachas desde al menos las 06:00 UTC; el exporter lleva en marcha desde ~17:00), así que no lo causa. Pero las lecturas de `/flows/{switch}` de un switch cuyo maestro es otro nodo pasan por Atomix, que es lo que se atascaba. Cuando ONOS se ralentiza, el exporter tarda más de los 5 s de `scrape_timeout` y Prometheus lo marca `down` (`context deadline exceeded`; se vio a las ~18:00).
- Una consulta directa al exporter (en calma) tardó 0,9 a 1,3 s.
- Sugerencia: subir `scrape_interval` de 5 s a 10 o 15 s y `scrape_timeout` por encima, y/o cachear en el exporter lo que cambia poco (la lista de switches) unos segundos. Es una propuesta; la carga no se ha demostrado dañina.
- Hasta ahora un parón de tu Prometheus de 130 s no cambió el número de avisos de ONOS (no había ninguno en ese momento, así que la prueba no concluye).

## 4. Para reproducir

```bash
for n in 1 2 3; do echo "ctrl-$n:"; curl -s -m 15 -u onos:rocks http://192.168.201.$n:8181/onos/v1/devices | python3 -c "import sys,json; d=json.load(sys.stdin)['devices']; print(len(d), 'switches,', sum(1 for x in d if x['available']), 'disponibles')"; done
curl -s -m 10 http://192.168.201.2:9090/api/v1/targets | python3 -c "import sys,json; [print(t['labels'].get('sensor'), t['health'], t.get('lastScrapeDuration'), (t.get('lastError') or '')[:70]) for t in json.load(sys.stdin)['data']['activeTargets']]"
```

Hay un registrador de latencia de ONOS en los controladores (`R1/deploy/topologia14/registrador_onos.yml`; registro en `/var/log/registrador-onos/medidas.log` de cada controlador, `grep LENTO`) por si quieres cruzar tus `down` con lo que tardó ONOS en ese momento.
