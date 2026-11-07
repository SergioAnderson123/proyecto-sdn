# Despliegue de la VM Controller (R1 + R2)

Un repositorio, **un despliegue por VM**. La VM Controller levanta ONOS 2.7, OpenLDAP, Policy Mapper y FreeRADIUS con un solo `docker compose`. La VM del IDS (R5) se despliega aparte: ver [`R5/deploy/`](../../R5/deploy/README.md).

`controller/compose.yml` **reutiliza** (`extends`) los servicios de [`R1/compose.yml`](../compose.yml), sin duplicarlos: lo que cambie el equipo de R1/R2 ahí se hereda. Solo se omite Mininet: en VNRT los switches son las VM sw1 a sw3 (OVS real).

## Requisitos
- Docker y el plugin Compose. Si `docker` pide `sudo`, agregar `ubuntu` al grupo `docker`.
- `cp R1/deploy/.env.example R1/deploy/.env` y ajustar `CONTROLLER_IP` y credenciales de ONOS (el archivo no se versiona).

## Orden de arranque
1. **Controller:** `cd R1/deploy/controller && ./up.sh` (levanta los 4 contenedores y configura SADIS y AAA en ONOS).
2. **Switches:** en cada uno, `sudo ovs-vsctl set-fail-mode <br> secure -- set bridge <br> protocols=OpenFlow13` y `sudo ovs-vsctl set-controller <br> tcp:<CONTROLLER_IP>:6653`. Anotar el `deviceId` del Core (`GET /onos/v1/devices`) para `R5/deploy/.env` como `CORE_DEVICE`.
3. **IDS (R5):** ver [`R5/deploy/README.md`](../../R5/deploy/README.md).

Apagar: `docker compose down` en `R1/deploy/controller`.

## Puertos del Controller
| Puerto | Uso |
|---|---|
| 6653/tcp | OpenFlow desde los switches |
| 8181/tcp | REST de ONOS (lo usa el IDS) |
| 8101, 1389, 5000/tcp, 1812-1813/udp | CLI de ONOS, LDAP, Policy Mapper, RADIUS |

## Convivencia con R5
El mapa único de prioridades OpenFlow de R1, R2 y R5 está en [`R5/deploy/prioridades.yaml`](../../R5/deploy/prioridades.yaml) y lo verifica `R5/deploy/tests`.

## Estado de la validación
- `docker compose config` valida el stack (incluido `extends` contra `R1/compose.yml`).
- **No probado todavía:** el stack del Controller levantado en VNRT (ONOS, RADIUS y LDAP juntos).
