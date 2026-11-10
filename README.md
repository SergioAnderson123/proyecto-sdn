# Proyecto SDN - Grupo 4 (TEL354, Redes Definidas por Software, PUCP 2026-2)

Solución SDN de seguridad para una red de campus académico:

- **R1:** control de acceso por rol (802.1X/EAP-TLS + MAB como fallback)
- **R2:** restricción de recursos privilegiados (reglas OpenFlow por tablas y roles)
- **R5:** seguridad perimetral (Suricata fuera de banda + integrador hacia ONOS)

Integrantes: Santiago Gabriel Linares Rivera, Mariana Jimena Huertas Oliva, Sergio Anderson Meneses Ramos, Daniel Andres Villalta Rupailla, Aurelio Andre Lam Kau. JP: Nilton Condori.

- Avance 01 (roles del equipo): [`docs/avance-01-roles.md`](docs/avance-01-roles.md)

## Estructura

```
R1/   Control de acceso: 802.1X/EAP-TLS, OpenLDAP, FreeRADIUS, Policy Mapper (base) y stack Docker
  compose.yml            stack de laboratorio (ONOS, Mininet, OpenLDAP, Policy Mapper, FreeRADIUS)
  flujos_R1_R2.md        flujos de R1 y R2 (transcripción de los diagramas)  ·  diagramas/
  shared/                LDAP, Policy Mapper, topología Mininet base y supplicants
  radius/  mininet/  policy_mapper/   imágenes y configuración de cada servicio
  deploy/controller/     despliegue en la VM Controller de VNRT
R2/   Restricción de recursos: tablas de rol, fallback MAB, portal cautivo y panel
  policy/                policy.yaml, policy_tables.py y portal_auth.py (se montan en el Policy Mapper)
  mininet/               topología R2, admisión y servicios de prueba
  mab_vm/  fallback/     fallback MAB de VM y overrides de compose
  portal_cautivo/  panel_accesos/  tests/  diagramas/
R5/   Seguridad perimetral: Suricata + integrador hacia ONOS
  codigo/  cli/  lab/  suricata/  tests/
  pruebas/               scripts, evidencia (JSON, capturas, pcap) y resultados de Docker y VNRT
  deploy/ids/            despliegue de los dos sensores IDS (slice de 14 VM); topologia.example.yaml describe cores, puertos y NIC; prioridades.yaml es el mapa único de prioridades R1/R2/R5
docs/   entregables y documentos del curso (no son código de un requerimiento)
```

R1 y R2 comparten el Policy Mapper: el código base está en `R1/shared/policy_mapper/` y los módulos de R2 en `R2/policy/`. `R1/compose.yml` monta ambas carpetas (`PYTHONPATH=/app:/r2_policy`).

Cada requerimiento tiene su propio README con los comandos de arranque y de prueba: [R1](R1/README.md), [R2](R2/README.md) y [R5](R5/flujos_R5.md).

## Pila

ONOS (REST 8181, OpenFlow 1.3 en 6653), Open vSwitch, FreeRADIUS, OpenLDAP, Policy Mapper (FastAPI), Suricata (ET Open + `local.rules`), Python 3 + `requests`.

## Ejecutar el integrador (R5)

```
export ONOS_URL=http://localhost:8181/onos/v1 ONOS_USER=<user> ONOS_PASS=<pass>
export EVE_PATH=<ruta>/eve-alerts.json STATE_PATH=./state.json
DRY_RUN=1 python3 R5/codigo/integrador.py   # simulación sin tocar ONOS
```

El resto de parámetros (`HOME_NET`, `LISTA_BLANCA`, `CORE_DEVICE`, `P_R5`, `POLL_SEC`...) también se leen del entorno; ver [`R5/r5.env.example`](R5/r5.env.example).

## Laboratorio en vivo con vistas profesionales

```
docker compose -f R5/lab/docker/compose.yml up -d --build
```

| Vista | URL | Qué muestra |
|---|---|---|
| Grafana (tablero "R5 · Seguridad perimetral") | http://localhost:3000 | Bloqueos, alertas por firma, tiempos alerta → bloqueo, espejo, captura de Suricata, TCAM |
| EveBox ([jasonish/evebox](https://github.com/jasonish/evebox), MIT) | http://localhost:5636 | Consola de alertas de Suricata |
| Panel de escenarios | http://localhost:8080 | Narración de cada ataque |

Los ataques se lanzan con `R5/pruebas/correr_escenarios.ps1`. Las métricas salen de `R5/lab/docker/monitoring/exporter.py`; el tablero se regenera con `gen_dashboard.py`.

## Administrar R5 desde la terminal (CLI)

```
python3 R5/cli/r5ctl.py estado               # bloqueos, espejo y últimas alertas
python3 R5/cli/r5ctl.py bloqueos
python3 R5/cli/r5ctl.py alertas -n 20
python3 R5/cli/r5ctl.py bloquear 10.0.1.100 --seg 600
python3 R5/cli/r5ctl.py desbloquear 10.0.1.100   # falso positivo
```

Usa las mismas variables de entorno que el integrador. Tras `desbloquear`, el integrador olvida la IP en su próxima reconciliación (`RECONCILE_SEC`, 10 s por defecto) y vuelve a bloquearla si reincide.

No subir credenciales al repo: usar variables de entorno.
