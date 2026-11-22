# Policy Mapper HA (R1)

Esta carpeta guarda el código y la configuración sin secretos del despliegue HA del Policy Mapper. El mapper mantiene sesiones y operaciones pendientes en etcd con quorum de tres miembros; las réplicas sirven detrás del proxy del gateway.

## Componentes desplegados

- `app/`: fuente desplegada en `mapper-ha-prod` (incluye `ha_store`, `ha_sessions`, `ha_runtime` y transporte ONOS con reintento seguro).
- `gateway/proxy.py`: frontend HTTP del gateway `192.168.201.254:5000`, chequea `/health/ha` y deriva entre `192.168.201.1-3:15002`. Arranque persistente mediante `cron @reboot` y `flock`.
- etcd 3.6.6 en los tres controladores, red host, TLS mutuo, puerto cliente 12379 y peer 12380. Cada nodo conserva datos en el volumen `mapper-etcd-prod-data`; el prefijo compartido es `/sdn/mapper/prod`.
- Las tres réplicas `mapper-ha-prod` usan `HA_MAINTENANCE=1`, alias Docker local `policy_mapper`, puerto de gestión 15002 y reinicio `unless-stopped`.
- RADIUS, DHCP, SADIS y fallback MAB consultan el endpoint estable del gateway. Los overrides de RADIUS están en `deploy/`.

## Secretos y estado

No se versionan `prod-runtime.env`, `prod-secrets.env`, certificados privados, claves TLS, datos etcd ni respaldos. Cada controlador requiere sus propios archivos TLS y un env-file protegido con permisos `0600`; `mapper-runtime.env.example` es solo una plantilla. No copiar tokens de producción al repo.

## Comprobaciones

Desde el gateway, `curl http://192.168.201.254:5000/health/ha` debe alternar nodos saludables. En cada controlador, consultar `http://192.168.201.<n>:15002/health/ha`; `sessions` debe coincidir y el estado debe ser `ready`. Para validar RADIUS, revisar el estado del contenedor y sus logs después de una autenticación controlada.

La prueba realizada detuvo temporalmente el mapper de ctrl-1: el gateway siguió respondiendo desde ctrl-2/ctrl-3 con las mismas dos sesiones; luego ctrl-1 volvió a estar listo. Las pruebas de `tests/test_ha_integration.py` requieren un clúster etcd aislado y un prefijo de prueba; no ejecutarlas contra `/sdn/mapper/prod`.

## Pendientes conocidos

No se apagó un ONOS en el slice durante la validación, para evitar interrumpir el laboratorio. El gateway aún es un único punto de entrada y el servicio R2 MAB para VM sigue en ctrl-1.


Las credenciales de ONOS y LDAP se leen de `ONOS_USER`, `ONOS_PASSWORD`, `LDAP_ADMIN_DN` y `LDAP_ADMIN_PASSWORD`. El ejemplo deja los secretos vacíos intencionalmente: definirlos únicamente en el env-file local con permisos `0600` antes de desplegar.
