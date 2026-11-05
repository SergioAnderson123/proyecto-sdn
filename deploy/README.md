# Despliegue por VM (R1 + R2 + R5)

Un repositorio, **un despliegue por VM**. Cada VM levanta solo lo suyo con un `docker compose`; las VM se hablan por la API REST de ONOS y por prioridades de flujo distintas (no comparten contenedores).

| VM | Carpeta | Servicios | Requerimientos |
|---|---|---|---|
| **Controller** | `deploy/controller/` | ONOS 2.7, OpenLDAP, Policy Mapper, FreeRADIUS | R1, R2 |
| **IDS** | `deploy/ids/` | Suricata 7 (sensor) + integrador M09 + `r5ctl` | R5 |
| sw1..sw3 | (OVS de VNRT, sin contenedores) | OpenFlow 1.3 hacia `CONTROLLER_IP:6653` | todos |

`controller/compose.yml` **reutiliza** (`extends`) los servicios de `SDN_ONOS_Setup/compose.yml`, sin duplicarlos: lo que cambie el equipo de R1/R2 ahi se hereda. Solo se omite Mininet. `ids/` construye la imagen del integrador desde `R5/`.

## Requisitos
- Docker y el plugin Compose en cada VM de servicios (controller e IDS). Si `docker` pide `sudo`, agregar `ubuntu` al grupo `docker`.
- `cp deploy/.env.example deploy/.env` y ajustar IP, interfaz, `CORE_DEVICE`, puertos y redes (el archivo no se versiona).

## Orden de arranque
1. **Controller:** `cd deploy/controller && ./up.sh` (levanta los 4 contenedores y configura SADIS y AAA en ONOS).
2. **Switches:** en cada uno, `sudo ovs-vsctl set-fail-mode <br> secure -- set bridge <br> protocols=OpenFlow13` y `sudo ovs-vsctl set-controller <br> tcp:<CONTROLLER_IP>:6653`. Anotar el `deviceId` del Core (`GET /onos/v1/devices`) en `.env` como `CORE_DEVICE`.
3. **IDS:** `cd deploy/ids && ./up.sh` (Suricata, integrador e instalacion del espejo en el Core; muestra `r5ctl estado`).

Operar R5 despues: `docker compose -f deploy/ids/compose.yml exec integrador r5ctl estado|bloqueos|alertas|bloquear|desbloquear`.
Apagar: `docker compose down` en cada carpeta.

## Puertos
| VM | Puerto | Uso |
|---|---|---|
| Controller | 6653/tcp | OpenFlow desde los switches |
| Controller | 8181/tcp | REST de ONOS (lo usa el IDS) |
| Controller | 8101, 1389, 5000/tcp, 1812-1813/udp | CLI de ONOS, LDAP, Policy Mapper, RADIUS |
| IDS | (ninguno) | Suricata captura por `IDS_IFACE` en modo host |

## Lo que hay que saber de R5 en VNRT
- **MAC del IDS:** la red virtual solo entrega a una VM las tramas dirigidas a su MAC. El espejo reescribe `eth_dst` de la copia con `IDS_MAC` (la calcula `ids/up.sh`); sin eso Suricata ve una fraccion minima del trafico.
- **Hace falta un "exterior":** R5 necesita un puerto del Core hacia un host externo o gateway (`PUERTO_UPLINK`). En la topologia base del curso el gateway no esta en el plano de datos.
- **Recursos:** ONOS y el IDS en una sola VM de 2 vCPU y 2 GB no caben (medido). Por eso son VM distintas.
- **R2 y R5 conviven por prioridades y por switches** (mapa único en `deploy/prioridades.yaml`, verificado por `deploy/tests`): el espejo de R5 va a **41000**, sobre el flujo de sesion y los puertos de confianza de R2 (40001); el DROP de R5 a 42000 y el DROP temporal de R1 a 45000. El espejo solo se instala en el Core (sin sesiones de usuario): en un switch de acceso reenvia con NORMAL y se saltaria las tablas de rol de R2. Cuando R5 bloquea a un host interno, el integrador pide al Policy Mapper `POST /sessions/revoke` (`MAPPER_URL`) para revocar tambien su sesion (I11).
- **ET Open:** `SURICATA_ET=1` descarga las ~50 mil reglas; necesita varios GB de RAM. Por defecto solo las reglas locales (`R5/suricata/local.rules`).

## Estado de la validacion
- `docker compose config` valida los dos stacks (incluido `extends` contra `SDN_ONOS_Setup` de `master`).
- Probado en Docker local: Suricata arranca con el `entrypoint.sh` (6 reglas locales, solo `eve-alerts.json`, reinicio idempotente) y el integrador lee una alerta del volumen compartido e intenta bloquear en ONOS.
- **No probado todavia:** el stack del Controller levantado (ONOS, RADIUS y LDAP juntos) y el IDS contra los switches reales de VNRT.
