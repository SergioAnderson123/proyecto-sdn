# Despliegue de la VM IDS (R5)

Un repositorio, **un despliegue por VM**. La VM IDS levanta Suricata 7 (sensor), el integrador M09 y `r5ctl`. La VM Controller (R1/R2) se despliega aparte: ver [`R1/deploy/`](../../R1/deploy/README.md). Las VM se hablan por la API REST de ONOS y por prioridades de flujo distintas (no comparten contenedores). `ids/` construye la imagen del integrador desde `R5/`.

| VM | Carpeta | Servicios |
|---|---|---|
| **IDS** | `R5/deploy/ids/` | Suricata 7 (sensor) + integrador M09 + `r5ctl` |
| Controller | `R1/deploy/controller/` | ONOS 2.7, OpenLDAP, Policy Mapper, FreeRADIUS |
| sw1..sw3 | (OVS de VNRT, sin contenedores) | OpenFlow 1.3 hacia `CONTROLLER_IP:6653` |

## Requisitos
- Docker y el plugin Compose. Si `docker` pide `sudo`, agregar `ubuntu` al grupo `docker`.
- `cp R5/deploy/.env.example R5/deploy/.env` y ajustar IP del controller, interfaz, `CORE_DEVICE`, puertos y redes (el archivo no se versiona).
- El Controller ya está arriba y los switches conectados.

## Arranque
`cd R5/deploy/ids && ./up.sh` (Suricata, integrador e instalación del espejo en el Core; muestra `r5ctl estado`).

Operar R5 después: `docker compose -f R5/deploy/ids/compose.yml exec integrador r5ctl estado|bloqueos|alertas|bloquear|desbloquear`.
Apagar: `docker compose down` en `R5/deploy/ids`.

## Lo que hay que saber de R5 en VNRT
- **MAC del IDS:** la red virtual solo entrega a una VM las tramas dirigidas a su MAC. El espejo reescribe `eth_dst` de la copia con `IDS_MAC` (la calcula `ids/up.sh`); sin eso Suricata ve una fracción mínima del tráfico.
- **Hace falta un "exterior":** R5 necesita un puerto del Core hacia un host externo o gateway (`PUERTO_UPLINK`). En la topología base del curso el gateway no está en el plano de datos.
- **Recursos:** ONOS y el IDS en una sola VM de 2 vCPU y 2 GB no caben (medido). Por eso son VM distintas.
- **R2 y R5 conviven por prioridades y por switches** (mapa único en `prioridades.yaml`, verificado por `tests/`): el espejo de R5 va a **41000**, sobre el flujo de sesión y los puertos de confianza de R2 (40001); el DROP de R5 a 42000 y el DROP temporal de R1 a 45000. El espejo solo se instala en el Core (sin sesiones de usuario): en un switch de acceso reenvía con NORMAL y se saltaría las tablas de rol de R2. Cuando R5 bloquea a un host interno, el integrador pide al Policy Mapper `POST /sessions/revoke` (`MAPPER_URL`) para revocar también su sesión (I11).
- **ET Open:** `SURICATA_ET=1` descarga las ~50 mil reglas; necesita varios GB de RAM. Por defecto solo las reglas locales (`R5/suricata/local.rules`).

## Estado de la validación
- `docker compose config` valida el stack del IDS.
- Probado en Docker local: Suricata arranca con el `entrypoint.sh` (6 reglas locales, solo `eve-alerts.json`, reinicio idempotente) y el integrador lee una alerta del volumen compartido e intenta bloquear en ONOS.
- **No probado todavía:** el IDS contra los switches reales de VNRT con este despliegue.
