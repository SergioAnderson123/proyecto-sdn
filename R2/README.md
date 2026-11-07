# R2: restricción de recursos privilegiados

Tablas OpenFlow por rol (docente VLAN 10, alumno VLAN 20, IoT VLAN 30, invitado/portal y administrador), fallback MAB y portal cautivo. Los flujos están en [`R1/flujos_R1_R2.md`](../R1/flujos_R1_R2.md); R2 depende de las sesiones que crea R1.

| Carpeta | Contenido |
|---|---|
| `policy/` | `policy.yaml` (qué recursos alcanza cada rol), `policy_tables.py` (genera las tablas) y `portal_auth.py`. El Policy Mapper de R1 los importa: `R1/compose.yml` los monta en `/r2_policy` con `PYTHONPATH=/app:/r2_policy` |
| `mininet/` | `topo_baseR2.py`, `topo_baseR2_userspace.py`, `admision_r2.py`, `admision_concurrente_r2.py`, `servicios_r2.sh`. Dentro del contenedor `mininet` están en `/root/shared-r2/` |
| `mab_vm/` | Servicio MAB para VM con cuotas y revocación (contenedor `r2-mab-vm`, puerto 5001) |
| `fallback/` | Overrides de compose (`compose.mab-network.yml`, `compose.version-onos.yml`) y `fallback-mab-1.0.0.oar` |
| `portal_cautivo/`, `panel_accesos/` | Portal web de login y panel de accesos en vivo (cada uno con su README) |
| `tests/` | Aceptación, métricas, barrido de CPU/RAM y monitor de R2 |
| `diagramas/` | `tablas.png` y `seguridadmeters.png` |

## Arranque

```bash
docker compose -f R1/compose.yml up -d --build                                      # desde la raíz del repo
docker compose -f R1/compose.yml -f R2/fallback/compose.mab-network.yml up -d       # con fallback MAB de VM
docker compose -f R2/mab_vm/compose.yml up -d --build                               # servicio MAB de VM (red externa `sdn`)
docker exec -it mininet python3 /root/shared-r2/topo_baseR2_userspace.py            # topología de R2
```

## Pruebas

```bash
python -m pytest R2/policy/tests R2/portal_cautivo/tests R2/mab_vm/test_mab_vm.py -q
python R2/tests/aceptacion_r2.py        # requiere el stack y la topología en marcha
```
