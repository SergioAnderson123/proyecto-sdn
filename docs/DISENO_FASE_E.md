# Fase E: el tráfico IP de R2 por la tabla 60 (sin `OUTPUT NORMAL`)

Estado: **diseño**, sin código todavía. Leído del código real: `R2/policy/policy_tables.py`, `R2/policy/policy.yaml`,
`R1/shared/policy_mapper/policy_mapper.py`, `R1/deploy/slice.py`.

## 1. Cómo funciona R2 hoy, de la autenticación al servicio

### 1.1 Quién hace qué
| Pieza | Qué hace |
|---|---|
| **ONOS AAA** | Recibe el EAPOL del host (regla de ONOS, prioridad 40000) y lo relaya a RADIUS |
| **FreeRADIUS + LDAP** | Valida el certificado (EAP-TLS) y obtiene rol, MAC registrada y ubicación fija del usuario |
| **Policy Mapper** (`/webhook`) | Decide si se admite y qué rol recibe; **instala el flujo de sesión** en el switch del usuario |
| **R2: `policy_tables.py`** | Instala las **tablas de rol** (10 docente, 20 alumno, 40 soporte, 50 invitado) y la de **cuarentena** (99) a partir de `policy.yaml` |
| **ONOS DHCP** | Da IP; el DHCP del cliente va al controlador (prioridad 40500) |
| **Portal cautivo** | Login con usuario y contraseña (LDAP) para invitados y para el **ascenso** de un equipo 802.1X |

### 1.2 Las prioridades de la tabla 0 de un switch de acceso (la más alta gana)
| Prioridad | Regla | Origen |
|---|---|---|
| 50000 | EAPOL del puerto de un usuario con sesión → controlador | Policy Mapper |
| 45000 | lista negra temporal por MAC (descarte) | Policy Mapper |
| 42000 / 41000 | descarte por IP de origen / espejo hacia el IDS | R5 |
| 40500 | DHCP del cliente (UDP 68→67) → controlador | R2 |
| **40003** | **ARP → tabla 60** (difusión por árbol) | **nuestra fase C** |
| 40002 | ARP del portal (NORMAL + controlador) y reescritura de la respuesta del portal | R2 |
| **40001** | **sesión**: `in_port + eth_src` → `push VLAN(rol)` + `goto tabla del rol` | Policy Mapper |
| **40001** | **confianza**: `in_port` de troncales y servidores → `OUTPUT NORMAL` | R2 |
| 40000 | LLDP/BDDP, EAPOL→AAA, ARP→controlador, DHCP→controlador | ONOS |
| 35000 | TCP hacia el portal → `NORMAL`; desvío del HTTP reescrito hacia el portal | R2 |
| 1000 | **cuarentena**: todo lo demás → `push VLAN 99` + `goto tabla 99` | R2 |

### 1.3 Un alumno, de principio a fin
1. **Conecta** el equipo al puerto del switch de acceso. Sin sesión, su tráfico cae en la regla de cuarentena (1000): solo puede hacer DHCP y hablar con el portal.
2. **Autentica por 802.1X.** El EAPOL sube al controlador (40000); ONOS AAA lo relaya a RADIUS, que valida el certificado y consulta LDAP. RADIUS llama al `/webhook` del Policy Mapper con el usuario y los datos de LDAP (rol, MAC registrada, switch y puerto autorizados).
3. **El Policy Mapper decide.** (a) La **MAC física** debe coincidir con la registrada en LDAP; si no, 403 y lista negra de 300 s. (b) Si ya hay una sesión de ese usuario o MAC en otro sitio: 409 y lista negra de 600 s (posible suplantación). (c) La **ubicación** debe ser la autorizada en LDAP. (d) El grupo LDAP se traduce a una tabla: docente 10, alumno 20, soporte 40.
4. **Instala el flujo de sesión** en *su* switch de acceso: `in_port + eth_src → push VLAN(20) + goto tabla 20` (prioridad 40001), más la regla que sube el EAPOL de ese puerto al controlador (50000). Antes se asegura de que las tablas de rol existan en ese switch. Devuelve a RADIUS la VLAN (Access-Accept).
5. **El alumno envía tráfico a `virtual_library` (10.0.0.40:8001).** En la tabla 0 casa con su flujo de sesión, el paquete recibe la etiqueta VLAN 20 y pasa a la **tabla 20**.
6. **La tabla 20 es la política.** Prioridad 30000: *ARP*; por cada servicio que la matriz permite al rol (`virtual_library`, `learning_platform`, `university_portal`): `VLAN 20 + IPv4 + TCP + ip_dst=<servicio>/32 + tcp_dst=<puerto>`; y el portal. **Todo lo permitido termina en `pop VLAN + OUTPUT NORMAL`**; lo demás cae en el descarte (prioridad 0). Por eso un alumno no llega al sistema de notas.
7. **`OUTPUT NORMAL`** convierte al switch en un conmutador L2 clásico: aprende MAC y reenvía por el puerto donde está el destino, o **inunda si no lo conoce**. Aquí vive el problema con lazos.
8. **El servidor responde.** Su respuesta entra al switch de acceso por una *troncal*. No coincide con ninguna sesión (la MAC de origen es la del servidor), así que sin más caería en la cuarentena. Por eso existen las **reglas de confianza**: `in_port` de troncales y servidores → `NORMAL`, que saltan las tablas de rol. La política se aplica solo en el **switch de entrada**; los demás confían.
9. **Fin de sesión.** El agente del host avisa del cierre (`/sessions/logout`) y se borra el flujo 40001; una revocación borra el flujo y mete la MAC en la lista negra (45000).

### 1.4 Un invitado y el ascenso, por el portal
- Un equipo **sin sesión** queda en cuarentena. Las reglas del portal (35000, 40002) le dejan resolver ARP y abrir TCP hacia el portal (en VNRT, HTTPS en el 8443); el HTTP hacia los destinos de `desvios` se reescribe hacia el portal y la respuesta se reescribe de vuelta.
- Al iniciar sesión, el portal valida contra LDAP y llama al Policy Mapper (`/portal/login`), que instala **el mismo flujo de sesión 40001** (invitado: tabla 50, caduca a los 1800 s). La ubicación del equipo la toma de la tabla de hosts de ONOS.
- **Ascenso:** un equipo con sesión 802.1X de rol `alumno` puede subir a `docente` si una persona entra en el portal, desde la misma MAC y la misma ubicación, con su usuario de LDAP. Se sustituye el flujo de sesión; al cerrar sesión o vencer el plazo vuelve a su rol base.

### 1.5 Dónde se usa `NORMAL` hoy (lo que hay que sustituir)
1. `_SALIDA` en las **tablas de rol** y en la **cuarentena** (`pop VLAN + NORMAL`): todo el tráfico permitido.
2. `flujos_confianza`: las reglas de **troncales y servidores** (`in_port → NORMAL`, y `NORMAL` para todo un switch si es de confianza total).
3. `flujos_portal`: ARP, TCP hacia el portal, desvío y reescritura de la respuesta (`NORMAL`).
4. `R5/lab/espejo.py`: la regla de espejo hacia el IDS (41000) reenvía también con `NORMAL`.

## 2. El problema en la topología de 14 VM
- **Con lazos, `NORMAL` inunda y no tiene STP que lo frene**: es lo que causa las tormentas.
- Hoy las tablas de R2 **ya están instaladas** en los switches (se ven los flujos de las tablas 10 a 99), pero **no hay reglas de confianza** para este slice (`slice.py portal` las genera para el slice anterior), así que **no hay camino de datos IP para los usuarios**. El ARP sí funciona, por nuestra tabla 60.
- Si hoy se generaran esas reglas de confianza tal cual (`in_port → NORMAL`), se reintroduciría la tormenta.

## 3. Diseño de la fase E

### 3.1 Principio
La **política no cambia**: sigue decidiendo *quién* puede alcanzar *qué* en el switch de entrada. Solo cambia **cómo se entrega** lo permitido: en vez de `OUTPUT NORMAL`, `goto_table:60`, donde ya existen los flujos unicast por destino calculados por el árbol.

```
tabla 0 ──(sesión 40001)──► tabla de rol ──(permitido: pop VLAN)──► tabla 60 ──► puerto de salida
        └─(confianza)──────────────────────────────────────────►
        └─(40003 ARP)──────────────────────────────────────────► tabla 60 (difusión por árbol)
```

### 3.2 Cambios en `policy_tables.py` (bajo una bandera)
- Nueva clave **`reenvio: normal | proactivo`** en `policy.yaml`, **`normal` por defecto**, para no tocar el laboratorio de Mininet (sin lazos).
- Dos ayudas, `_salida(politica)` y `_normal(politica)`, que devuelven `[VLAN_POP, NORMAL]` / `[NORMAL]` o, en modo proactivo, `[VLAN_POP, TABLE 60]` / `[TABLE 60]`. Se usan en los cuatro sitios de 1.5.
- Las reglas de ARP de las tablas de rol quedan inalcanzables (el 40003 las gana), pero van también a la tabla 60 por coherencia.

### 3.3 Reglas de confianza: por tipo de trama, no por puerto
La regla actual `in_port → NORMAL` a 40001 **gana a la de LLDP/BDDP (40000)** y se tragaría el descubrimiento de enlaces; un "todo el switch" a 40001 haría lo mismo. En modo proactivo, las reglas de confianza casan además **`ETH_TYPE = IPv4`** (el ARP ya va por la 40003; LLDP, BDDP y EAPOL siguen llegando al controlador). En los cores y el borde (confianza total) basta una regla `IPv4 → tabla 60`.

### 3.4 Lo que la tabla 60 necesita para que esto funcione
1. **Hosts fijos.** ONOS solo aprende hosts de los paquetes que le llegan; el ARP ya no le llega (va a la tabla 60) y el DHCP solo enseña a los clientes. El **server y el portal** (IP fija) no aparecerían, y sin su MAC no habría flujo unicast hacia ellos: el tráfico se descartaría. Hay que declararlos en un archivo (`hosts_fijos`: MAC, switch y puerto) que el vigilante una a lo que dice ONOS.
2. **Visibilidad de los hosts de usuario.** Para que ONOS siga aprendiendo ubicaciones (lo necesita el portal, `ubicar_host`, y los flujos unicast del vigilante), la regla de difusión de **entrada por un puerto de acceso** añade además `OUTPUT CONTROLLER`. El controlador recibe la copia pero **no reenvía nada** (no hay `proxyarp`), así que no hay riesgo de inundación.
3. **Camino rápido para hosts nuevos en el vigilante.** Hoy cualquier cambio espera 15 s. Para el tráfico de un host recién autenticado eso es mucho. Si la diferencia son **solo altas de flujos unicast** (ningún borrado ni cambio de difusión), se aplica de inmediato: no puede formar lazos.
4. **Server con dos tarjetas** (una por core). Las dos están en la misma subred; el ARP del host recibirá respuesta de ambas y se quedará con una MAC cualquiera. Funciona si hay flujos hacia **las dos MAC**, pero Linux puede descartar el tráfico que llega por la segunda tarjeta si `rp_filter` es estricto. Hay que fijar `rp_filter=2` (o 0) en el server antes de la prueba.

### 3.5 R5
La regla de espejo (41000) pasa de `NORMAL + sensores` a `goto_table:60 + sensores` (la copia a los sensores es una acción inmediata y no cambia).

## 4. Invariantes y pruebas (sin red, antes de tocar nada)
1. **Ningún flujo generado inunda**: con `reenvio: proactivo`, `flujos_que_inundan(...)` vacío para cada switch y cada rol.
2. **Todo lo que era `NORMAL` es ahora `goto_table:60`** y no cambia ninguna regla de política (mismas coincidencias, mismas prioridades).
3. **Las tramas de control no se pierden**: ninguna regla de confianza casa LLDP, BDDP ni EAPOL.
4. **Simulador de la tubería completa** (tabla 0 → tabla de rol → tabla 60) con paquetes de ejemplo: alumno → servicio permitido (llega), alumno → sistema de notas (se descarta), respuesta del servidor → alumno (llega), invitado → portal (llega), invitado → servicio (se descarta), DHCP (al controlador), broadcast (nunca duplicado). Sobre la topología de 14 VM y con cada fallo simple.
5. Las pruebas existentes de R2 y R5 con la bandera en `normal` siguen pasando sin cambios.

## 5. Despliegue
1. Con la bandera apagada no cambia nada: se sube el código.
2. Se instalan primero los **hosts fijos** y se comprueba (vista previa) que existen los flujos unicast hacia el server y el portal.
3. Se activa **en un solo switch de acceso** (`acc-1`) borrando y reinstalando sus flujos de R2 (borrar y publicar, igual que en la fase B), con el diagnóstico 95 y el freno abierto.
4. Prueba de punta a punta con un usuario real: autenticar, llegar a un servicio permitido, ser rechazado en uno prohibido.
5. Después `acc-2`, los cores y el borde, uno a uno.
6. **Reversible:** apagar la bandera y volver a sincronizar devuelve los flujos a `NORMAL`.

## 6. Decisiones que necesito del equipo
1. **Dónde viven los servicios (10.0.0.40, .50) y el portal (10.0.0.60) en esta topología.** ¿En el `server`? ¿Con sus IP como alias en una tarjeta? Define los hosts fijos.
2. **Orden de despliegue:** ¿primero acceso y luego núcleo, o al revés? (Recomendado: primero los hosts fijos y el núcleo, después los accesos.)
3. **El camino rápido** (3.4.3): aplicar de inmediato las altas unicast. ¿Lo aceptamos?
4. **`rp_filter` en el server** (3.4.4): ¿se puede cambiar en esa VM?

## 7. Riesgos
- **El mayor es el de las reglas de confianza** (3.3): sin la condición de tipo de trama, se tragarían los enlaces y la red perdería topología.
- **Sesiones activas durante el cambio:** al reinstalar las tablas de rol de un switch, los usuarios con sesión pierden tráfico unos instantes (no la sesión, que está en el flujo 40001 y en el Policy Mapper).
- **Servicio inalcanzable por falta de host fijo** es el fallo más probable al principio; la vista previa de flujos debe mostrarlo antes de activar nada.
