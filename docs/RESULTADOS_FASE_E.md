# Resultados de la fase E (16-nov-2026): el tráfico IP de R2 por la tabla 60, sin tormentas

Resumen: en la topología de 14 VM (5 switches OVS con lazos físicos, ONOS en clúster de 3 nodos) el reenvío de **todo** el tráfico de R2 (ARP, IP de usuarios con sesión,
cuarentena, portal) pasó de `OUTPUT NORMAL` (que inunda) a reglas proactivas en la tabla 60. Resultado medido: **0 flujos con NORMAL/FLOOD/ALL en los 5 switches**, dos usuarios
reales autenticados por 802.1X con roles distintos, y una prueba de estrés de difusión a 200–1000 pps **sin multiplicación y con la red vacía 3 s después**. Diseño:
`DISENO_FASE_E.md`; procedimiento: `PROTOCOLO_PRUEBA_AUTENTICACION.md`; reenvío por árbol: `DISENO_FLUJOS_PROACTIVOS.md`.

## 1. Entorno y qué se desplegó
- **Switches** (OpenFlow 1.3, fail-mode secure): `switch-borde`, `sw-core-1` (raíz del árbol de difusión), `sw-core-2`, `sw-acc-1` (VM 192.168.201.12), `sw-acc-2` (192.168.201.11).
  7 cables; 14 enlaces dirigidos en ONOS. Sin STP/RSTP (las BPDU no cruzan VNRT).
- **Controlador:** ONOS 2.7 en 3 nodos (maestro: node-ctrl-1), con LDAP, FreeRADIUS y Policy Mapper en node-ctrl-1. Vigilante de recálculo: principal en node-ctrl-3, respaldo en node-ctrl-2.
- **Server** (VM `server`): IP única de servicios `10.0.0.10/24` en `ens4` (cable a sw-core-1); `ens5` sin IP. Hosts de prueba: `h1-profesor` (acc-1/1, 10.0.0.201), `h2-alumno` (acc-2/1, 10.0.0.202).
- **Código nuevo** (rama `topologia14-deploy`): `policy_tables.py` (bandera `reenvio: normal|proactivo|lista`), `generar_politica_local.py`, `limpiar_r2.py`, `hosts_fijos.py`, alta rápida en el vigilante,
  simulador de la tubería, `consultar_hosts.py`; playbooks 70–75 y 96. **188 pruebas sin red** (R2/policy + plantillas de topologia14 + prioridades).

## 2. Cronología y resultados medidos
| Paso | Qué se hizo | Resultado medido |
|---|---|---|
| A | Línea base de hosts (71) | ONOS conocía solo 2 hosts de IP fija; 0 sesiones 802.1X |
| B | IP `10.0.0.10`, hosts fijos del server, vigilante con alta rápida | ALTA RÁPIDA +2 reglas en cada uno de los 5 switches; `arping` al server responde |
| C | Cores y borde a proactivo (70, con limpieza de flujos antiguos) | 0 NORMAL en los 3; cores con 7 reglas de R2 (5 de confianza + LLDP/BDDP) |
| D | `acc-1` a proactivo | 0 NORMAL en `acc-1`; `acc-1` con 8 reglas en la tabla 0 |
| E | Login de `profesor01` (802.1X) | Sesión Docente VLAN 10; flujos 40001 y 50000; `nc` al 8101 y 8001 = *refused* (llega y vuelve), al 22 = *timed out* |
| — | Logout y re-login | `[<<< LOGOUT]` del agente borra sesión y flujo 40001; vuelve a cuarentena; el re-login funciona |
| F | `acc-2` a proactivo + `alumno01` | **0 NORMAL en los 5 switches**; sesión Alumno VLAN 20; 8001 *refused*, 8101 y 22 *timed out* |
| G | Estrés de difusión (96) | Ver sección 3 |
| — | IP del server persistente (75, netplan) | `ens4` 10.0.0.10/24 por `60-datos-ens4.yaml`; falta probar con un reinicio |

El diagnóstico 95 salió "Sin tormenta" en cada paso (máximo 1–2 pps por puerto, `ovs-vswitchd` al 0,3–0,4 % de CPU).

**La política por rol sigue intacta:** el mismo servidor y el mismo puerto (`10.0.0.10:8101`, sistema de notas) responden *refused* para un docente y *timed out* para un alumno.

## 3. Prueba de estrés de difusión (`96_estres_difusion.yml`)
Un host (`h2-alumno`) genera tramas a ritmo controlado durante 25 s; se mide cada puerto de los 5 switches durante la inyección y tras parar.
| Modo | Inyectado | Máximo por puerto | 3 s después de parar | Qué demuestra |
|---|---|---|---|---|
| `arp` | 200 pps (5 000) | 201 pps | ≤1 pps | La difusión se reparte por el árbol, una copia por puerto |
| `arp` | 1000 pps (25 000) | 1003 pps | ≤1 pps | Igual con 5× el ritmo: factor de multiplicación 1 |
| `eth` (tipo 0x88b5) | 500 pps (12 500) | 500 solo en `sw-acc-2`; el resto ≤1 | ≤1 pps | Una trama que ninguna regla reconoce muere en el switch de entrada |
| `desconocido` (TCP permitido a una MAC inexistente) | 500 pps (12 500) | 501 solo en `sw-acc-2`; el resto ≤1 | ≤1 pps | No sale del switch de entrada |

**El árbol se ve en los puertos.** Con `arp` entrando por `sw-acc-2/1`: `acc-2` saca por el puerto 3 → `core-1` (raíz) recibe por el 3 y saca por 2 (`acc-1`), 4 (`core-2`), 5 (server `ens4`) y 6 (borde) →
`acc-1` entrega a `h1-profesor`, el borde entrega a su puerto 1 y `core-2` entrega al server `ens5`. Son 8 entregas, una por host y por tarjeta; los cables de respaldo y los sensores quedan en ~0,6 pps.

**Contadores de descarte** (`sw-acc-2`): tabla 60 `priority=1 actions=drop` = **12 500** paquetes y 750 000 bytes (exactamente los del modo `desconocido`); tabla 20 `priority=0 actions=drop` = 12 512 (los 12 500 de `eth` más 12 sueltos de pruebas anteriores).
No se midió la CPU de `ovs-vswitchd` durante el estrés.

## 4. Hallazgos y correcciones (lo aprendido en vivo)
1. **La cuarentena (tabla 99) saltaba a la 60.** En OpenFlow `goto_table` solo va hacia una tabla de mayor número: OVS lo rechazó (`BAD_TABLE_ID`), los flujos quedaron en `PENDING_ADD` y el Policy Mapper los reenviaba cada 15 s.
   Corregido: en proactivo la tabla 99 solo descarta y el ARP/portal de un host sin sesión se resuelven en la tabla 0. Prueba nueva: ningún `goto_table` generado va hacia atrás. *El simulador no lo veía porque esos flujos nunca se ejecutaban en sus escenarios.*
2. **Había 35 flujos antiguos de R2 en los 5 switches** (cuarentena a 1000 incluida: hasta entonces la red solo pasaba ARP). El Policy Mapper solo añade flujos, no borra los que su política nueva ya no genera. Solución: `limpiar_r2.py` borra solo el appId `org.campus.r2` de los switches indicados antes de reiniciarlo.
3. **Con `reenvio` en lista, los switches fuera de la lista no reciben nada de R2.** Las reglas antiguas de confianza (`in_port → NORMAL`, 40001) le ganan a la de ONOS que sube el LLDP (40000) y habrían inundado el descubrimiento por los lazos.
4. **El AAA de ONOS no podía hablar con RADIUS en node-ctrl-1:** abre un socket UDP local con el mismo número que el puerto del servidor (1812) y el `docker-proxy` de RADIUS lo ocupaba (`BindException: Address already in use`). `radiusServerUdpPort` no es ese puerto (se probó 18120 sin efecto).
   Solución: RADIUS sin puertos publicados y con IP fija `10.50.0.250`; el AAA apunta a esa IP (`73_radius_red_docker.yml`). *Lección:* reiniciar la app AAA por REST en el clúster la dejó `ACTIVE` pero con la característica `Uninstalled`; se recuperó con `feature:install aaa-app` por la consola Karaf.
5. **El logout no cerraba la sesión:** ONOS AAA 2.7.0 no manda accounting; el único cierre es el `POST /sessions/logout` del agente con `AGENTE_TOKEN`, y el `.env` de topologia14 no lo tenía (403). Solución: `74_token_agente.yml` y `72_suplicante.yml` (copia la clave al agente). Siempre usar `--limit node-ctrl-1,<host>` con el 72.
6. **`ens5` del server no contesta el ARP** (una interfaz sin IPv4 con `rp_filter` activo descarta lo cuyo origen se alcanza por otra interfaz; causa probable, no verificada). El server solo es alcanzable por `sw-core-1`. Redundancia real = *bond* activo-respaldo (trabajo de capa 3).
7. **LDAP con valores de Mininet:** `alumno01` tenía MAC/switch/puerto viejos; hay que `ldap-ubicacion.sh liberar` antes del primer login o el Policy Mapper rechaza y deja la MAC 300 s en lista negra.

## 5. Limitaciones y pendientes (honestos)
- **No probado:** host-jhon sin DHCP (sin acceso a esa VM); portal e invitados (el servicio del portal no está desplegado en el server); caída de un cable con usuarios y tráfico activos (el recálculo solo se probó con ARP); reinicio del server para confirmar la IP persistente; `--copiar-arp` (¿ONOS aprende por EAPOL/DHCP a un host nuevo? h1 y h2 ya eran conocidos); R5 (el espejo aún usa NORMAL; hoy no hay flujos de R5).
- **Escalabilidad:** hoy hay S×H reglas unicast (una por switch y por host); ver `DISENO_CAPA3.md` para el plan de subredes.
- **Sin redundancia del server** (ver 6) y **portal atado a `ens4`**.
- **Pruebas con Ansible:** los playbooks no pueden ejecutarse en el PC de desarrollo; su validación fue en vivo desde `switch-borde`.

## 6. Cómo reproducirlo (desde `switch-borde`, en `R1/deploy/topologia14`)
`PLAYBOOK=<archivo> ./lanzar.sh`, con el freno (`99_freno_emergencia.yml -e confirmar=true`) abierto en otra terminal y el 95 antes y después de cada cambio:
`71` (hosts de ONOS) → `70 -e preparar_ip=true` / `75 -e aplicar=true` (IP del server) → `70 -e hosts_fijos=true` + `65 -e usar_hosts_fijos=true -e aplicar=true` →
`70 -e limpiar=<sw> -e limpiar_aplicar=true -e escribir=true -e reenvio=<lista> -e reiniciar=true` → `73` (RADIUS), `74` (clave) y `72 --limit node-ctrl-1,<host>` (suplicantes) → `96 -e modo=arp|eth|desconocido`.
