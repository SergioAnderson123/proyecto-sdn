# Policy Mapper HA (R1)

Esta carpeta guarda el código y la configuración sin secretos del despliegue HA del Policy Mapper. El mapper mantiene sesiones y operaciones pendientes en etcd con quorum de tres miembros; las réplicas sirven detrás del proxy del gateway.

## Componentes desplegados

- `app/`: fuente desplegada en `mapper-ha-prod` (incluye `ha_store`, `ha_sessions`, `ha_runtime` y transporte ONOS con reintento seguro).
- `gateway/proxy.py`: frontend HTTP del gateway `192.168.201.254:5000`, chequea `/health/ha` y deriva entre `192.168.201.1-3:15002`. Arranque persistente mediante `cron @reboot` y `flock`.
- etcd 3.6.6 en los tres controladores, red host, TLS mutuo, puerto cliente 12379 y peer 12380. Cada nodo conserva datos en el volumen `mapper-etcd-prod-data`; el prefijo compartido es `/sdn/mapper/prod`.
- Las tres réplicas `mapper-ha-prod` usan `HA_MAINTENANCE=1`, alias Docker local `policy_mapper`, puerto de gestión 15002 y reinicio `unless-stopped`.
- RADIUS, DHCP, SADIS y fallback MAB consultan el endpoint estable del gateway. Los overrides de RADIUS están en `deploy/`.
- En el slice VNRT, las tres réplicas consultan el LDAP central de ctrl-1 por `192.168.201.1:1389` (`LDAP_HOST`/`LDAP_PORT`). En un despliegue Docker local se conservan los valores por defecto `ldap:389`.

## Secretos y estado

No se versionan `prod-runtime.env`, `prod-secrets.env`, certificados privados, claves TLS, datos etcd ni respaldos. Cada controlador requiere sus propios archivos TLS y un env-file protegido con permisos `0600`; `mapper-runtime.env.example` es solo una plantilla. No copiar tokens de producción al repo.

## Comprobaciones

Desde el gateway, `curl http://192.168.201.254:5000/health/ha` debe alternar nodos saludables. En cada controlador, consultar `http://192.168.201.<n>:15002/health/ha`; `sessions` debe coincidir y el estado debe ser `ready`. Para validar RADIUS, revisar el estado del contenedor y sus logs después de una autenticación controlada.

La prueba realizada detuvo temporalmente el mapper de ctrl-1: el gateway siguió respondiendo desde ctrl-2/ctrl-3 con las mismas dos sesiones; luego ctrl-1 volvió a estar listo. Las pruebas de `tests/test_ha_integration.py` requieren un clúster etcd aislado y un prefijo de prueba; no ejecutarlas contra `/sdn/mapper/prod`.

En la actualización VNRT del 22-nov-2026, los mappers se sustituyeron uno por uno. Antes de cada cambio se guardó el contenedor anterior como `mapper-ha-prod-v2-rollback-20261122`; también se conservaron los contenedores de rollback anteriores. Las tres réplicas quedaron `ready` con las mismas 2 sesiones y 4 eventos. Una solicitud MAB sintética con puerto no autorizado devolvió `403 PORT_NOT_ALLOWED` en cada réplica y no instaló flujos. El servicio R2 MAB sigue siendo una sola instancia en ctrl-1, aunque las tres réplicas pueden alcanzarla.

## Pendientes conocidos

El gateway aún es un único punto de entrada y el servicio R2 MAB para VM sigue en ctrl-1. Se midió una sola reautenticación EAP-TLS real; hacen falta más muestras para estimar percentiles. La pérdida de quórum se ensayó en un clúster aislado, no en producción.

El 22-nov-2026 se detuvo solo ONOS en ctrl-1 durante la prueba de failover. Ctrl-2 y ctrl-3 permanecieron `READY`, conservaron los 5/5 switches y el gateway siguió sirviendo 2 sesiones y 4 eventos. Tras iniciar ctrl-1, los tres ONOS volvieron a `READY`, cada uno vio 5/5 switches y se restauró ctrl-1 como maestro de los cinco.

En la misma fecha, h1-profesor completó una reautenticación EAP-TLS (TLS 1.2) en 2384 ms, medidos desde `logon` hasta `EAP-SUCCESS` y el puerto autorizado. RADIUS registró solicitudes con atributos EAP y ningún `Access-Reject` en los 10 minutos consultados. Las tres réplicas HA conservaron las mismas 2 sesiones y 4 eventos. Es una única muestra de reautenticación, no un percentil ni una medición de primer acceso.

También se probó etcd 3.6.6 en tres contenedores temporales, sobre una red Docker interna sin puertos publicados y con datos aislados. Una réplica detenida permitió lecturas/escrituras con las otras dos; con dos detenidas, la escritura no se confirmó; al volver una segunda réplica, el quórum y las escrituras se recuperaron. Se eliminaron la red, los contenedores y los archivos temporales de esa prueba. No se detuvo ni modificó etcd de producción; los tiempos observados de commit (7–11 ms) son muestras del ensayo, no latencia del mapper.


Las credenciales de ONOS y LDAP se leen de `ONOS_USER`, `ONOS_PASSWORD`, `LDAP_ADMIN_DN` y `LDAP_ADMIN_PASSWORD`. El ejemplo deja los secretos vacíos intencionalmente: definirlos únicamente en el env-file local con permisos `0600` antes de desplegar.

## Fuente oficial y corrección de la revisión (22-nov-2026)

La fuente oficial del mapper y del gestor de sesiones es `R1/shared/policy_mapper/`.
Contiene los modos local y HA; `SESSION_ETCD_ENDPOINTS` activa HA. Los módulos de
políticas y portal siguen siendo oficiales en `R2/policy/`. `app/` contiene enlaces
relativos a esas fuentes, no copias que deban editarse por separado.

Para generar un paquete autocontenido, incluso en Windows con enlaces simbólicos
deshabilitados, ejecutar desde la raíz del repositorio:

```sh
python3 R1/mapper-ha/build_bundle.py /tmp/mapper-ha.tar.gz
```

El paquete incluye una política de ejemplo. En producción se conserva la
`policy.yaml` vigente de cada despliegue; no sustituirla por el ejemplo.
El despliegue anterior puede tener credenciales en el código: antes de reemplazarlo,
configurar `ONOS_USER`, `ONOS_PASSWORD`, `LDAP_ADMIN_DN` y `LDAP_ADMIN_PASSWORD`
en el env-file privado. El compose local también las recibe del entorno;
`ONOS_PASS` se admite como alternativa a `ONOS_PASSWORD`. Nunca versionar esos valores.

### Historial de eventos

En HA, `GET /events` lee la clave `events` del mismo prefijo etcd de las sesiones.
Las escrituras verifican el propietario del cerrojo y usan compare-and-swap;
se conservan los últimos 200 eventos, en orden inverso para el panel.
Una réplica reiniciada lee el mismo historial. Sin quórum se devuelve 503,
sin recurrir a una cola local incompleta. La lectura de eventos no adquiere el
cerrojo de escritura. El modo sin HA conserva la cola local anterior.

La clave se crea con el primer evento; no requiere borrar ni migrar las sesiones.
Los eventos que estaban en memoria antes de actualizar no se recuperan solos:
exportarlos desde cada réplica antes del reinicio si se necesita conservarlos.
El historial acotado no es una auditoría transaccional: sesión y evento se escriben
en claves distintas, y una caída entre ambas operaciones puede dejar una sesión
válida cuyo evento no llegó a guardarse.

### Pruebas y límites

Ejecutar con `SESSION_ETCD_ENDPOINTS` sin definir:

```sh
PYTHONPATH=R1/shared/policy_mapper:R2/policy python3 -m pytest \
  R1/shared/policy_mapper/tests R1/mapper-ha/tests R2/policy/tests -q
```

`test_review.py` comprueba eventos compartidos, retención, pérdida de quórum,
propiedad de escritura, respuestas HTTP y failover ONOS con transporte simulado.
La suite de integración rechaza cualquier prefijo distinto de `/sdn/mapper/test/…`;
requiere inicializar un estado vacío de prueba en un cluster de ensayo.
No usar las pruebas de regresión del modo local con credenciales de producción.

Cada transacción terminada registra `transaction wait_ms=... hold_ms=...`:
la espera incluye el cerrojo local y distribuido; la duración incluye la operación
y liberación. Son medidas del mapper, no la duración completa de EAP/RADIUS.
Para medir un login real, correlacionar esos logs con la petición y registrar
el tiempo total del cliente, incluyendo errores y percentiles bajo concurrencia.
Las transacciones que no consiguen adquirir el cerrojo pueden responder 503.

El gateway de gestión `.254:5000` se conserva como punto único por la decisión
del grupo recogida en `docs/NOTA_VALIDACION_MAPPER_HA.md`; no es el gateway virtual
`10.0.x.254` del plano de datos. R2 MAB sigue siendo un servicio separado en ctrl-1:
la réplica de sesiones del mapper no replica automáticamente ese servicio.
Las pruebas de caída real de ONOS y pérdida de dos miembros etcd requieren una
ventana controlada y recuperación comprobada; las pruebas simuladas no las sustituyen.
La medición de 15 solicitudes MAB sintéticas a través del gateway dio HTTP 403 en
las 15, mediana 189.91 ms y p95 de rango más cercano 310.90 ms. Esto mide el
camino MAB/LDAP y el cerrojo con un puerto deliberadamente inválido; no mide un
login EAP real. La prueba de apagar un mapper sí se repitió: con ctrl-1 detenido,
el gateway respondió desde ctrl-3 y mantuvo las 2 sesiones y 4 eventos. No se
detuvo ningún ONOS ni se quitó quórum etcd en producción para no interrumpir el
slice; esas pruebas siguen requiriendo una ventana controlada.
