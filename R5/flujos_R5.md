# Flujos R5: seguridad perimetral

Fuente: [`Flujos_R5.pdf`](Flujos_R5.pdf). Código: [`codigo/integrador.py`](codigo/integrador.py), [`lab/`](lab/), [`suricata/`](suricata/). Resultados: [`resultados_R5.md`](resultados_R5.md).

**Idea:** el Core espeja selectivamente el tráfico Norte-Sur hacia Suricata (IDS fuera de banda). Cuando Suricata alerta, el integrador M09 lee `eve.json` y pide a ONOS un DROP en el plano de datos. `HOME_NET = 10.0.0.0/24`.

## Bloque 1: espejado selectivo (regla permanente en el Core)

- Estado inicial: el Core reenvía sin copiar nada a Suricata (`hids`, puerto espejo).
- Un script de arranque instala la regla permanente: `POST /onos/v1/flows/of:0000000000000001` (HTTP 8181), Tabla 0, `priority = P_ESPEJO` (TBD), `isPermanent = true`, `match(eth_type=0x0800, ipv4_dst=10.0.0.0/24, in_port=uplink)`, `instr(output NORMAL + output puerto_hids)`.
- ONOS -> OVS: `OFPT_FLOW_MOD` (TCP 6653); confirmación en TCAM; 201 Created con `flow_id`.
- El `flow_id` del espejo se guarda: es permanente y se distingue de los DROP temporales. El tráfico entre hosts internos no se copia.

## Bloque 2: detección y alerta

1. Atacante externo (`hext` 10.0.1.10) lanza `nmap -sS` o `curl` contra `hsrv` 10.0.0.10.
2. El Core reenvía (NORMAL) y copia el paquete a `puerto_hids` (I08).
3. Suricata evalúa unas 53 mil reglas ET Open más `local.rules`. Regla propia: `LOCAL Port scan SYN` (sid 9000002), `detection_filter` de 20 SYN en 5 s por origen. Escribe la alerta en `eve.json` (I09).
4. M09 lee solo líneas nuevas (offset + inode, tolera rotación) y filtra `event_type = alert` y severidad <= 2. Ejemplo: `Ethertype unknown` (sev 3, LLDP de ONOS) se descarta.

## Bloque 3: decisión (whitelist y reincidencia)

M09 consulta el registro (M11, propuesta): `{en_whitelist, incidentes_previos, ultimo_timeout}`.

| Caso | Acción |
|---|---|
| A: IP en whitelist (DNS, gateways, servidores propios) | No se bloquea; se registra y se avisa al operador |
| B: IP nueva | DROP con timeout corto (hoy 300 s) |
| C: IP reincidente | DROP con timeout largo o permanente (escala sugerida: corto, medio, permanente o revisión manual) |

Deduplicación: si ya hay un DROP activo para esa IP, no se reenvía.

## Bloque 4: enforcement del DROP

1. `GET /onos/v1/hosts` (Basic Auth) para ubicar el switch de borde y el puerto del host interno.
2. `POST /onos/v1/flows/{deviceId}?appId=APP_ID_R5`, `priority = P_R5` (TBD, mayor que las del Policy Mapper), `timeout = T s`, `isPermanent = false`, `match(eth_type=0x0800, ipv4_src=IP_maliciosa)`, treatment vacío (DROP).
3. ONOS -> OVS: `OFPT_FLOW_MOD`; 201 Created con `flow_id`.
4. Bloqueo en el Core (IP externa) y en el borde (host interno afectado).
5. `registrar(ip, sid, flow_id, timeout, reincidencias+1, estado=bloqueada)`. Se guardan `t_deteccion` y `t_mitigacion` para las métricas.

## Bloque 5: flujo proactivo (feed de reputación)

1. Descarga periódica (HTTPS, cada N horas, TBD) de una lista de IPs/CIDR maliciosas. Se normaliza, se agrega a CIDR, se excluye la whitelist y se prioriza por riesgo para no saturar la TCAM.
2. Conciliación con los bloqueos proactivos activos: añadir las nuevas, renovar las vigentes, retirar las que salieron del feed.
3. `POST /onos/v1/flows/{deviceId}` con DROP preventivo (`ipv4_src` = IP o CIDR), timeout renovable; `registrar(ip, origen=feed, ...)`. Retiro con `DELETE /onos/v1/flows/{deviceId}/{flowId}`.

## Bloque 6: expiración, reincidencia y retiro manual

1. Expiración natural: `OFPT_FLOW_REMOVED`, más reconciliación periódica con `GET /onos/v1/flows`; `marcar_expirada(ip, flow_id)`. Se conservan historial y contador de reincidencias.
2. Si la misma IP vuelve a atacar, regresa al Bloque 3 como reincidente con un timeout mayor.
3. Retiro manual (falso positivo): `DELETE /onos/v1/flows/{deviceId}/{flowId}` -> `OFPFC_DELETE_STRICT` -> 204; `registrar(ip, estado=liberada_manual)`.

## Estado real de implementación

| Elemento | Estado |
|---|---|
| Lectura de `eve-alerts.json`, filtro de severidad, whitelist, POST de DROP, reintentos con backoff, estado en disco | Implementado en `integrador.py` (probado con ONOS real) |
| Escalado por reincidencia (Bloque 3) | Diseño; el código usa timeout fijo de 300 s |
| Registro de `t_deteccion` / `t_mitigacion` | Diseño; los tiempos se midieron comparando `eve.json`, el log del integrador y `dump-flows` |
| Feed de reputación (Bloque 5) | Diseño |
| Reconciliación y retiro manual (Bloque 6) | Diseño |
| Script del espejo (Bloque 1) | Implementado: [`lab/espejo.py`](lab/espejo.py), reglas entrante y saliente, verificado con contadores |
| Mediciones de tiempos, fracción que pasa y TCAM | Medido: ver [`resultados_R5.md`](resultados_R5.md) |
