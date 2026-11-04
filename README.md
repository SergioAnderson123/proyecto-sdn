# Proyecto SDN - Grupo 4 (TEL354, Redes Definidas por Software, PUCP 2026-2)

Solución SDN de seguridad para una red de campus académico:

- **R1:** control de acceso por rol (802.1X/EAP-TLS + MAB como fallback)
- **R2:** restricción de recursos privilegiados (reglas OpenFlow por tablas y roles)
- **R5:** seguridad perimetral (Suricata fuera de banda + integrador hacia ONOS)

Integrantes: Santiago Gabriel Linares Rivera, Mariana Jimena Huertas Oliva, Sergio Anderson Meneses Ramos, Daniel Andres Villalta Rupailla, Aurelio Andre Lam Kau. JP: Nilton Condori.

- Avance 01 (roles del equipo): [`docs/avance-01-roles.md`](docs/avance-01-roles.md)

## Estructura

```
R1_R2/
  flujos_R1_R2.md        flujos de R1 y R2 (transcripción de los diagramas)
  diagramas/             PNG de cada bloque
R5/
  flujos_R5.md           flujos de R5 (transcripción)
  Flujos_R5.pdf          diagrama original
  codigo/integrador.py   M09: integrador IDS-SDN (eve-alerts.json -> ONOS REST)
docs/
  pendientes-entrega-1.md  incoherencias y checklist de la Primera Entrega
  entrega-1/               enunciado y presentación del parcial
```

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

Los ataques se lanzan con `pruebas-r5/correr_escenarios.ps1`. Las métricas salen de `R5/lab/docker/monitoring/exporter.py`; el tablero se regenera con `gen_dashboard.py`.

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
