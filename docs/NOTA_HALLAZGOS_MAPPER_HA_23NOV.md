# Nota: hallazgos del 23-nov sobre el mapper HA y los agentes 802.1X

Para quien mantiene `R1/mapper-ha/` y el agente 802.1X. Surgieron al preparar una prueba de la app DHCP: h1 se quedó sin camino al servidor y al buscar la causa aparecieron estas cosas. Todo lo de abajo son datos de la red en vivo del 23-nov (≈14:20 a 15:20, hora de los controladores); lo que es hipótesis está marcado como tal.

## 1. Una sesión activa en el mapper sin su flujo 40001 en ONOS (causa desconocida)

- **Qué se vio.** `/sessions` daba a `profesor01` activa (`session_id 08ba47c4…`, `flow_id 50665496667556007`), pero en sw-acc-1 (`ovs-ofctl dump-flows br0 table=0`) **no había** el flujo `priority=40001,in_port=1,dl_src=fa:16:3e:b6:96:08`, y ONOS (`GET /flows/of:0000e6925d87354b` por ctrl-2) tampoco: solo los dos 40001 de los troncales. En acc-2 el de h2 sí estaba. Resultado: `nc 10.0.0.10 8101` desde h1 daba `timed out` (el bueno es `refused`).
- **Quién lo borró: no lo sé.** Los logs de `mapper-ha-prod` de los tres nodos (90 min) no tienen ningún borrado, arranque ni migración; solo `[HA] maintenance deferred: Unavailable`.
- **Por qué no se arregla reautenticando.** El login responde «ya tiene esta misma sesión activa… se confirma sin reprocesar» (visto el 22-nov) y no reinstala el flujo. **Propuesta:** al confirmar una sesión existente, comprobar que su flujo 40001 existe en ONOS (el `flow_id` es determinista: mismo selector, mismo id) y reinstalarlo si falta; y/o que el mantenimiento reconcilie sesiones y flujos 40001.

## 2. Los agentes avisan a un puerto que ya no existe

- `[hosts:vars] controller_ip=192.168.201.1` en `R1/deploy/topologia14/inventario.ini` hace que `hosts.yml` ponga `R1_MAPPER=http://192.168.201.1:5000` en el agente. Con el HA, `curl ctrl-1:5000` da **HTTP 000** y `ctrl-1:15002` da 200; el mapper se sirve por el gateway (`192.168.201.254:5000`) y el 15002 de cada nodo.
- **Efecto:** el `POST /sessions/logout` del agente (apagado del host, enlace caído, «Desconectar», `72 -e parar=true`) **se pierde en silencio**: la sesión sigue viva con `session_id` igual. La presencia de la app DHCP sí funciona porque ya apunta al gateway.
- **Corregido** en el repo (`controller_ip=192.168.201.254`, solo en `[hosts:vars]`, que usan `hosts.yml` y `portal.yml`) y probado en h1 y h2 con `-e controller_ip=192.168.201.254`. Falta que lo confirmes y, si hay un portal, que apunte igual.

## 3. Cosas de playbooks que no sirven con el HA

- `88_prueba_sesion_ausente.yml` llama a `127.0.0.1:5000` de ctrl-1 (y lee `PRESENCIA_TOKEN` de `R1/deploy/.env`): con el HA no hay nada ahí. No lo cambié.
- `72_suplicante.yml` trabaja sobre **h1 y h2** (grupo `[hosts]`) aunque su cabecera hable de h1: `-e parar=true` paró los dos agentes.

## 4. Cómo se cerró la sesión de h1 (por si vuelve a pasar)

`POST /sessions/logout` **por el gateway** con la clave del agente, leída en ctrl-1 sin mostrarla: respondió `{"status":"cerrada"} HTTP 200`, lo que confirma que `AGENTE_TOKEN` de `R1/deploy/.env` coincide con el de las réplicas. Luego `72 -e arrancar=true -e controller_ip=192.168.201.254`: h1 entró con `session_id` nuevo (`922bf7cb`), su flujo reapareció y `nc` dio `refused`.

## 5. Señales de lentitud (etcd) que conviene mirar

- `mapper-etcd-prod` en ctrl-1: `apply request took too long` de 101 a 127 ms (umbral 100 ms) cada pocos segundos, incluso en lecturas de `/sdn/mapper/prod/state`. Parece disco lento de las VM. La compactación está bien (periódica 1 h, DB de 737 kB).
- `[HA] maintenance deferred: Unavailable` varias veces por hora en las tres réplicas (p. ej. 14:20:47 en ctrl-1 y ctrl-2 a la vez). Hipótesis: contención o un corte breve de etcd. ¿Es lo esperado?

## 6. ONOS se atascó y no por red (puede afectar al mapper)

- El 22-nov se «restituyó ctrl-1 como maestro de los cinco switches»; el 23-nov el maestro de **todos** era ctrl-2. Las lecturas `GET /flows/{switch}` por ctrl-1 o ctrl-3 tardaban 11 a 15 s o daban 500 (`flow-store-of:… timed out in 15000 milliseconds`); por ctrl-2, 0,02 s. Red entre controladores sana (0 % pérdida, RTT < 1 ms), carga baja.
- Pausar el vigilante de capa 3 (que leía esos flujos cada 2 s por ctrl-1) lo normalizó un rato, pero **el atasco volvió con el vigilante ya leyendo del nodo maestro** y también apareció con él pausado: es **intermitente** y no lo causa el vigilante (1 de 15 combinaciones switch × nodo en la última medición, p. ej. core-1 leído desde ctrl-3, 9,7 s; el log repite `flow-store-of:…-flows timed out in 15000 ms`). Parece un atasco intermitente de ctrl-2 (que manda en todos los switches) al responder a otros nodos. El vigilante ahora pide los flujos de un switch a su nodo maestro (`R2/policy/onos_multi.py`) y va sano, pero lo de fondo no está resuelto.
- **Pregunta para el mapper:** `ha_onos` recorre `ONOS_ENDPOINTS` en orden fijo (ctrl-1, ctrl-2, ctrl-3) y tu mantenimiento lee `/flows/{dpid}` (`recover_intents`) y borra flujos. Con el mando en otro nodo esas operaciones también serían remotas; un POST/DELETE que se rinde a los 3 s puede dejar el flujo a medias. ¿Lo has contemplado? ¿Sabes qué movió el mando a ctrl-2?

## Qué te pido

1. ¿Puedes comprobar por qué el mapper no detecta (ni repone) un flujo 40001 que falta con la sesión activa? Es lo que dejó a h1 sin servicio.
2. Confirmar el cambio de `controller_ip` y revisar el portal.
3. Mirar la lentitud de etcd y los `maintenance deferred`.
4. Decidir si el mapper debe hablar con el maestro del switch igual que el vigilante.

## Para reproducir

```bash
curl -s -m 15 http://192.168.201.254:5000/sessions | python3 -c "import sys,json; [print(s['username'], s.get('session_id','')[:8], s['flow_id']) for s in json.load(sys.stdin)['sessions'].values()]"
ssh -t ubuntu@192.168.201.12 "sudo ovs-ofctl -O OpenFlow13 dump-flows br0 table=0 | grep 'dl_src=fa:16:3e:b6:96:08'"
curl -s -u onos:rocks http://192.168.201.2:8181/onos/v1/mastership/of:0000e6925d87354b/master
```
