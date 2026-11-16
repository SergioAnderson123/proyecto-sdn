# Plan de direccionamiento para la capa 3 (subredes)

Dos planes: **A** para nuestro slice de VNRT (los 5 switches de hoy) y **B** para un campus más real, con pabellones (V, CIA, A, O…). El plan A es una propuesta concreta y comprobable; el plan B es una **plantilla con supuestos de ejemplo**
(los pisos y switches por pabellón NO son datos del campus: se reemplazan con los reales). Contexto técnico: `DISENO_CAPA3.md`. Los dos planes cargan con `capa3.cargar_plan` (que valida solapes, gateways y puertos).

## 0. Reglas comunes
| Regla | Valor |
|---|---|
| Una subred por… | **switch de acceso** (o grupo de puertos de un switch). Un /24 aunque el switch solo tenga 48 puertos: las direcciones no escasean y la regularidad ayuda a operar |
| Gateway virtual | `.254` de cada subred; **una MAC común** `02:00:5E:00:00:FE` (no existe en ningún equipo: la contesta el switch de acceso) |
| `.1–.9` | reservadas (equipos de red) |
| `.10–.99` | estáticas: servidores, impresoras, equipos fijos |
| `.100–.199` | DHCP dinámico (hoy es el rango de la app DHCP de ONOS: 100–199) |
| `.200–.249` | estáticas de prueba o reserva |
| `.250` | servidor DHCP virtual de la subred |
| Rol (docente, alumno, soporte, invitado) | **no** define la subred: lo define la sesión (VLAN/tablas de R2). Una subred mezcla roles |
| No solapar con | gestión `192.168.201.0/24` (ens3 de las VM), red interna de Docker `10.50.0.0/24` (RADIUS en `10.50.0.250`) y la red de monitoreo (sensores: no se enruta) |
| IDS (R5) | un solo prefijo agregado como `HOME_NET` (ver la sección 3) |

## 1. Plan A: nuestro slice de VNRT (topologia14)
Propuesta mínima: **el server y el portal no cambian de IP** (`10.0.0.10`), y los hosts de prueba conservan su sufijo.

| Subred | Switch (ONOS) | Puertos de usuario | Prefijo | Gateway | DHCP | Estáticas | Hosts hoy → plan |
|---|---|---|---|---|---|---|---|
| servicios | `sw-core-1` | 5 (server `ens4`) | `10.0.0.0/24` | `10.0.0.254` | — | `.10` server y portal | `10.0.0.10` → **`10.0.0.10`** (no cambia) |
| acc-1 | `sw-acc-1` | 1 | `10.0.1.0/24` | `10.0.1.254` | `.100–.199`, servidor `.250` | `.201` | h1-profesor `10.0.0.201` → **`10.0.1.201`** |
| acc-2 | `sw-acc-2` | 1 | `10.0.2.0/24` | `10.0.2.254` | `.100–.199`, servidor `.250` | `.202` | h2-alumno `10.0.0.202` → **`10.0.2.202`** |
| externa | `switch-borde` | 1 | `203.0.113.0/24` | `203.0.113.254` | **sin DHCP** (los externos no obtienen IP) | `.103` (propuesta) | host-jhon (atacante simulado; sin IP hoy) |

- **Agregado del campus:** `10.0.0.0/16` (servicios y accesos) → ese es el `HOME_NET`. La subred `externa` queda **fuera** (es "el exterior"); `203.0.113.0/24` es el rango de documentación que ya proponía `group_vars/all.yml` (`red_externa`).
- **Reservado para crecer:** `10.0.3.0/24 – 10.0.9.0/24` (más accesos), sin asignar.
- **Fuera del plan (no se enrutan):** gestión `192.168.201.0/24`, Docker `10.50.0.0/24` y los puertos de monitoreo (`core-1/1`, `core-2/1`, `borde/4` y `borde/5`: sensores).
- El archivo `R2/policy/planes/vnrt_topologia14.json` es este plan en el formato de `capa3.cargar_plan(datos, nombres=NOMBRES)`; `tests/test_planes.py` lo carga, lo prueba sobre la topología real (puertos y cables de ONOS) y comprueba que ninguna dirección choca con la gestión ni con Docker.

**Qué habría que tocar para aplicarlo** (nada de esto está hecho): `policy.local.yaml` (los servicios siguen en `10.0.0.10`, no cambia), DHCP por subred (la app de ONOS tiene un solo rango), `HOME_NET` de R5, las IP de h1 y h2, el vigilante en modo capa 3 y el ARP del gateway (ver el riesgo de viabilidad en `DISENO_CAPA3.md`, sección 6).

## 2. Plan B: campus con pabellones (plantilla)
Jerarquía: `10.<id del pabellón>.<n del switch de acceso>.0/24`. **Cada pabellón agrega a un /16** (`10.10.0.0/16` para V, `10.20.0.0/16` para CIA…), y los servicios y el IoT tienen su bloque. Ventajas: (a) el IDS se configura con un prefijo,
(b) con un switch de distribución por pabellón el núcleo solo necesitaría **una ruta por pabellón** en vez de una por subred (rutas agregadas: extensión pendiente de `capa3.py`), (c) leer una IP dice dónde está el host.

**Datos del equipo y supuestos.** Dato: **V tiene 3 pisos** (con varios equipos en el 3.º), **CIA 4**, **A 8** (con pocos equipos) y **O 5**. Supuestos míos (a confirmar): cuántos switches de acceso hay por piso (V: 1, 1 y 3, por los "varios equipos" del 3.º; CIA y O: 2 por piso;
A: 1 por piso porque tiene pocos equipos) y 48 puertos de usuario por switch. Los demás pabellones se añaden con su id (50, 60…). Con los datos reales de switches por piso se regenera todo:
`python3 R2/policy/planes/generar_plan_pabellones.py --config campus.json --doc docs/PLAN_DIRECCIONAMIENTO.md`.
Un piso con más de 48 equipos tiene varios switches y por tanto varias subredes; un switch con más de 254 equipos necesitaría un /23. A, con pocos equipos por piso, podría usar /26 en vez de /24 (62 hosts) si se quiere ahorrar direcciones; no hace falta, hay de sobra en `10.0.0.0/8`.

<!-- BEGIN-GENERADO -->
### Resumen por pabellón (pisos: dato; switches por piso: supuesto)

| Pabellón | Id | Bloque agregado | Pisos | Switches de acceso por piso | Subredes de acceso | Hosts máx. (48 por switch) |
|---|---|---|---|---|---|---|
| V | 10 | `10.10.0.0/16` | 3 | 1, 1, 3 | 5 | 240 |
| CIA | 20 | `10.20.0.0/16` | 4 | 2, 2, 2, 2 | 8 | 384 |
| A | 30 | `10.30.0.0/16` | 8 | 1, 1, 1, 1, 1, 1, 1, 1 | 8 | 384 |
| O | 40 | `10.40.0.0/16` | 5 | 2, 2, 2, 2, 2 | 10 | 480 |
| **Total** | | `10.0.0.0/8` | | | **31** | **1,488** |

### Subredes especiales

| Subred | Switch | Prefijo | Gateway | Uso |
|---|---|---|---|---|
| servicios | DC-SW1 | `10.1.0.0/24` | `10.1.0.254` | Centro de datos: portal, LDAP, RADIUS, servicios académicos |
| iot | IOT-SW1 | `10.250.0.0/24` | `10.250.0.254` | IoT y equipos sin 802.1X (MAB; tabla de rol 30 reservada en R2) |

### Todas las subredes de acceso

| Pabellón | Piso | Switch de acceso | Subred | Gateway | DHCP | Estáticas |
|---|---|---|---|---|---|---|
| V | 1 | V-P1-SW1 | `10.10.1.0/24` | `10.10.1.254` | `10.10.1.100–.199` | `10.10.1.10–.99`, `10.10.1.200–.249` |
| V | 2 | V-P2-SW1 | `10.10.2.0/24` | `10.10.2.254` | `10.10.2.100–.199` | `10.10.2.10–.99`, `10.10.2.200–.249` |
| V | 3 | V-P3-SW1 | `10.10.3.0/24` | `10.10.3.254` | `10.10.3.100–.199` | `10.10.3.10–.99`, `10.10.3.200–.249` |
| V | 3 | V-P3-SW2 | `10.10.4.0/24` | `10.10.4.254` | `10.10.4.100–.199` | `10.10.4.10–.99`, `10.10.4.200–.249` |
| V | 3 | V-P3-SW3 | `10.10.5.0/24` | `10.10.5.254` | `10.10.5.100–.199` | `10.10.5.10–.99`, `10.10.5.200–.249` |
| CIA | 1 | CIA-P1-SW1 | `10.20.1.0/24` | `10.20.1.254` | `10.20.1.100–.199` | `10.20.1.10–.99`, `10.20.1.200–.249` |
| CIA | 1 | CIA-P1-SW2 | `10.20.2.0/24` | `10.20.2.254` | `10.20.2.100–.199` | `10.20.2.10–.99`, `10.20.2.200–.249` |
| CIA | 2 | CIA-P2-SW1 | `10.20.3.0/24` | `10.20.3.254` | `10.20.3.100–.199` | `10.20.3.10–.99`, `10.20.3.200–.249` |
| CIA | 2 | CIA-P2-SW2 | `10.20.4.0/24` | `10.20.4.254` | `10.20.4.100–.199` | `10.20.4.10–.99`, `10.20.4.200–.249` |
| CIA | 3 | CIA-P3-SW1 | `10.20.5.0/24` | `10.20.5.254` | `10.20.5.100–.199` | `10.20.5.10–.99`, `10.20.5.200–.249` |
| CIA | 3 | CIA-P3-SW2 | `10.20.6.0/24` | `10.20.6.254` | `10.20.6.100–.199` | `10.20.6.10–.99`, `10.20.6.200–.249` |
| CIA | 4 | CIA-P4-SW1 | `10.20.7.0/24` | `10.20.7.254` | `10.20.7.100–.199` | `10.20.7.10–.99`, `10.20.7.200–.249` |
| CIA | 4 | CIA-P4-SW2 | `10.20.8.0/24` | `10.20.8.254` | `10.20.8.100–.199` | `10.20.8.10–.99`, `10.20.8.200–.249` |
| A | 1 | A-P1-SW1 | `10.30.1.0/24` | `10.30.1.254` | `10.30.1.100–.199` | `10.30.1.10–.99`, `10.30.1.200–.249` |
| A | 2 | A-P2-SW1 | `10.30.2.0/24` | `10.30.2.254` | `10.30.2.100–.199` | `10.30.2.10–.99`, `10.30.2.200–.249` |
| A | 3 | A-P3-SW1 | `10.30.3.0/24` | `10.30.3.254` | `10.30.3.100–.199` | `10.30.3.10–.99`, `10.30.3.200–.249` |
| A | 4 | A-P4-SW1 | `10.30.4.0/24` | `10.30.4.254` | `10.30.4.100–.199` | `10.30.4.10–.99`, `10.30.4.200–.249` |
| A | 5 | A-P5-SW1 | `10.30.5.0/24` | `10.30.5.254` | `10.30.5.100–.199` | `10.30.5.10–.99`, `10.30.5.200–.249` |
| A | 6 | A-P6-SW1 | `10.30.6.0/24` | `10.30.6.254` | `10.30.6.100–.199` | `10.30.6.10–.99`, `10.30.6.200–.249` |
| A | 7 | A-P7-SW1 | `10.30.7.0/24` | `10.30.7.254` | `10.30.7.100–.199` | `10.30.7.10–.99`, `10.30.7.200–.249` |
| A | 8 | A-P8-SW1 | `10.30.8.0/24` | `10.30.8.254` | `10.30.8.100–.199` | `10.30.8.10–.99`, `10.30.8.200–.249` |
| O | 1 | O-P1-SW1 | `10.40.1.0/24` | `10.40.1.254` | `10.40.1.100–.199` | `10.40.1.10–.99`, `10.40.1.200–.249` |
| O | 1 | O-P1-SW2 | `10.40.2.0/24` | `10.40.2.254` | `10.40.2.100–.199` | `10.40.2.10–.99`, `10.40.2.200–.249` |
| O | 2 | O-P2-SW1 | `10.40.3.0/24` | `10.40.3.254` | `10.40.3.100–.199` | `10.40.3.10–.99`, `10.40.3.200–.249` |
| O | 2 | O-P2-SW2 | `10.40.4.0/24` | `10.40.4.254` | `10.40.4.100–.199` | `10.40.4.10–.99`, `10.40.4.200–.249` |
| O | 3 | O-P3-SW1 | `10.40.5.0/24` | `10.40.5.254` | `10.40.5.100–.199` | `10.40.5.10–.99`, `10.40.5.200–.249` |
| O | 3 | O-P3-SW2 | `10.40.6.0/24` | `10.40.6.254` | `10.40.6.100–.199` | `10.40.6.10–.99`, `10.40.6.200–.249` |
| O | 4 | O-P4-SW1 | `10.40.7.0/24` | `10.40.7.254` | `10.40.7.100–.199` | `10.40.7.10–.99`, `10.40.7.200–.249` |
| O | 4 | O-P4-SW2 | `10.40.8.0/24` | `10.40.8.254` | `10.40.8.100–.199` | `10.40.8.10–.99`, `10.40.8.200–.249` |
| O | 5 | O-P5-SW1 | `10.40.9.0/24` | `10.40.9.254` | `10.40.9.100–.199` | `10.40.9.10–.99`, `10.40.9.200–.249` |
| O | 5 | O-P5-SW2 | `10.40.10.0/24` | `10.40.10.254` | `10.40.10.100–.199` | `10.40.10.10–.99`, `10.40.10.200–.249` |
<!-- END-GENERADO -->

**Tamaño en reglas con estos supuestos** (31 subredes de acceso × 48 hosts = 1 488 hosts; modelo plano de 2 cores; `python3 R2/policy/benchmark_capa3.py --accesos 31 --hosts 48`):

| Modo | Reglas totales | Máx. en un switch | En un core |
|---|---|---|---|
| Capa 2 (hoy: un unicast por host en cada switch) | 50 689 | 1 539 | 1 520 |
| Capa 3, rutas completas | 6 977 | 223 | 32 |
| Capa 3, ruta por defecto en los accesos | 7 535 | 241 | 32 |

Con 31 accesos todavía conviene el modo de **rutas completas** (la ruta por defecto compensa a partir de ~50 subredes). Con rutas agregadas por pabellón y un switch de distribución, el núcleo bajaría de 32 a unas 6 reglas (4 pabellones, servicios e IoT).

## 3. ¿Qué pasa cuando un host cambia de zona (y de IP)? Efectos en R5 y en el resto
**Es una consecuencia inevitable de una subred por switch** (como en cualquier red enrutada): al moverse a otra zona el host recibe una IP de esa subred. No impide el diseño, pero hay efectos que conviene tener claros. Lo que sigue sale de leer el código de R5
(`R5/codigo/integrador.py`) y del Policy Mapper; **no está probado en vivo**.

**Afecta a R5 en tres puntos:**
1. **`HOME_NET` (Suricata e integrador).** Hoy vale `10.0.0.0/24`. Con subredes, los hosts de `10.0.1.x` y `10.0.2.x` caerían *fuera* de HOME_NET y el integrador los trataría como **atacantes externos**: pondría el DROP en los **cores** en vez de en el switch del host,
   y **no revocaría su sesión** (eso solo lo hace para los internos, `donde == "borde"`). Se arregla con el prefijo agregado (`10.0.0.0/16` en el slice, `10.0.0.0/8` menos lo externo en el campus) en `topologia.yaml` (`home:`), en `HOME_NET` del integrador y en el `suricata.yaml`. Es el cambio **obligatorio**; con el plan jerárquico es una línea.
2. **El bloqueo es por IP.** El DROP de R5 casa `IPV4_SRC` (prioridad 42000) y vence por inactividad. Un host bloqueado que cambia de zona **recibe otra IP y el DROP ya no lo alcanza**. La revocación de sesión que dispara R5 (I11) pone una lista negra por **(switch, puerto, MAC)**:
   tampoco cubre el puerto nuevo, así que lo único que lo frenaría es la reautenticación 802.1X (y la ubicación fija de LDAP, si el usuario la tiene). Mitigación recomendada: que el integrador, además de la IP, **recuerde la MAC** y reaplique el bloqueo cuando ONOS vea esa MAC con una IP nueva,
   o que el Policy Mapper rechace por MAC sin importar el puerto. No existe hoy. El problema inverso (una IP bloqueada que se reasigna a otro host mientras dura el bloqueo) ya existe en la red plana y no cambia.
3. **Atribución y visibilidad.**
   - Saber "quién tenía la IP X cuando saltó la alerta" depende de ONOS (`/hosts`) y del DHCP; con IP que cambia por zona, ONOS conserva un tiempo la IP anterior del host y `ubicar_host(ip)` puede dar un resultado viejo o "no encontrado" (el integrador entonces **bloquea en el Core**, que es seguro pero más amplio). Conviene registrar IP↔MAC↔hora (DHCP u ONOS) para análisis posterior.
   - `mac_del_evento` toma la **MAC de origen de la trama espejada**. En capa 3 el switch del destino reescribe `eth_src` al gateway: el espejo debe seguir tomándose en los puertos de entrada y tránsito (borde y cores, como hoy), no después del último salto, o la MAC del atacante sería la del gateway. **A verificar.**
   - El tráfico **entre subredes siempre cruza los cores** (mejor visibilidad que ahora para `INTRANET_BLOCK`); el tráfico **dentro de la misma subred** no sale del switch de acceso y no lo ve el espejo de los cores (hoy en capa 2 tampoco, si los dos hosts cuelgan del mismo switch).
   - Los espejos de R5 aún usan `NORMAL`: en capa 3 deben pasar a la tabla de reenvío, como ya está pendiente para la capa 2.
   - La lista blanca de R5 (por IP o prefijo) hay que ajustarla a los prefijos nuevos (gestión, servicios).

**Fuera de R5:**
- **La política de R2 no se afecta:** va por rol (VLAN) y por la IP del *destino* (el servicio), no por la IP de origen.
- **Las conexiones TCP abiertas se cortan** al cambiar de IP (alcance: solo red cableada). Es el precio de agregar por subred: el host que se pasa a otro puerto de **otro switch o pabellón** cambia de IP; dentro del mismo switch de acceso no cambia. Si hiciera falta conservar la IP al moverse, habría que ir a rutas /32 por host (justo lo que la capa 3 evita).
- **LDAP con ubicación fija** (`description`, switch y puerto de la ficha) ya rechaza hoy a un usuario que se autentica desde otro puerto: para usuarios móviles esa ficha no debe fijarse.
- **El DHCP** entrega un contrato nuevo en la subred nueva; el anterior se libera por vencimiento.

## 4. Decisiones abiertas (tuyas)
1. Aprobar el **plan A** (`10.0.0.0/24` servicios, `10.0.1.0/24` acc-1, `10.0.2.0/24` acc-2, `203.0.113.0/24` externa) o pedir cambios.
2. Datos reales para el **plan B**: pabellones, pisos, switches por piso, equipos por zona.
3. **DHCP por subred** (`dhcprelay` + servidor con un ámbito por subred, o un DHCP propio): es independiente del reenvío.
4. Cómo tratar el **bloqueo de R5 ante movilidad** (sección 3, punto 2).
