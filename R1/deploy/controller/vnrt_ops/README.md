# Comprobaciones VNRT del 17/11/2026

Estado y resultados: `docs/ESTADO_VNRT_2026-11-17.md`. Estos helpers no despliegan toda la topología.

## Consultas sin tráfico de prueba

En ctrl-1, desde la raíz del repositorio:

```bash
docker cp R1/deploy/controller/vnrt_ops/onos_status.py policy_mapper:/tmp/onos_status.py
docker exec -e PYTHONPATH=/app:/r2_policy policy_mapper python /tmp/onos_status.py
docker cp R1/deploy/controller/vnrt_ops/mab_arp_ingress.py policy_mapper:/tmp/mab_arp_ingress.py
docker exec -e PYTHONPATH=/app:/r2_policy policy_mapper python /tmp/mab_arp_ingress.py check
```

En cada switch se puede ejecutar `python3 passive_rates.py`: lee contadores durante cinco segundos, sin enviar paquetes. `tormenta False` es solo el resultado de esa muestra.

## Dos reglas ARP de acceso

Las dos reglas ya están instaladas. Copian ARP al controlador solo en el puerto 1 de los dos accesos y mantienen el salto existente a tabla 60. Las reglas originales de `r2.proactivo` se conservan. El helper comprueba su presencia antes de actuar y aborta ante superposiciones o cambios inesperados.

No ejecutar `apply` otra vez sobre el despliegue actual. El estado está en `/home/ubuntu/sdn-identity/mab-ingress-state.json` y en `/tmp/sdn-mab-ingress-state.json` del mapper. Si fuera necesario revertir esta adaptación, el modo `remove` elimina exclusivamente los IDs guardados cuya propiedad y selector coincidan; no limpia otros flujos. Conservar antes una copia del estado. No usar playbooks de limpieza general para retirar estas reglas.

La variante MAB 1.0.3 usa HTTP/1.1, observa como advisor(2), restringe consultas a esos accesos y limita cola/caché. El OAR original 1.0.0 permanece intacto. `R2/fallback/build-vnrt.py` compila contra el contenedor ONOS local; aborta si ya existe el artefacto de salida y no instala aplicaciones. No ejecutar migraciones LDAP históricas de `/tmp`: ya están aplicadas y los montajes actuales son distintos.

## Límites pendientes

La integración se comprobó mediante una única trama dentro del pipeline OVS. La nueva emisión física de h1 después de corregir el punt no se ejecutó porque requiere sudo interactivo. Jhon conserva su IP DHCP y necesita activar SSH desde su consola. No se debe asignar una IP de la red interna al atacante por suposición.
