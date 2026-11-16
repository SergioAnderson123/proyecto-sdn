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

### 3.3 Reglas de confianza y descubrimiento de enlaces (dos capas)
La regla actual `in_port → NORMAL` a 40001 **gana a LLDP/BDDP (40000)** y se tragaría el descubrimiento de enlaces; y si ONOS perdiera los
enlaces, el vigilante creería que los cables cayeron. Por eso dos capas:
1. **Capa principal: la confianza solo cubre IPv4.** Las reglas de confianza casan además `ETH_TYPE = IPv4` (el ARP ya va por la 40003). LLDP, BDDP,
   EAPOL y cualquier trama de control futura nunca entran en la tabla 60.
2. **Capa de refuerzo: más prioridad a LLDP/BDDP.** Reglas propias `ETH_TYPE 0x88cc` (LLDP) y `0x8942` (BDDP) → controlador, a **prioridad 40100** en
   cada switch: sobre la confianza (40001), el portal (40002) y el ARP (40003), bajo el DHCP (40500). Se registra en `R5/deploy/prioridades.yaml`.

**Quién confía en qué** (por puerto, no por switch entero):
| Switch | Puertos de confianza (IPv4 → tabla 60) | Puertos que NO son de confianza |
|---|---|---|
| core-1, core-2 | las troncales y el del **server** (p5) | el de monitoreo (p1) |
| sw-acc-1, sw-acc-2 | las troncales (p2, p3) | **p1 (host): sesión o cuarentena** |
| switch-borde | las troncales (p2, p3) | **p1 (host-jhon): sesión o cuarentena**, y p4/p5 (monitoreo) |

host-jhon cuelga del borde: se trata como **un usuario más** (tablas de rol y cuarentena), no como equipo de confianza.

### 3.4 Lo que la tabla 60 necesita para que esto funcione
1. **¿Cómo conoce ONOS a los hosts?** Aprende de los paquetes que llegan al controlador: el **DHCP** (40500) enseña a cada cliente, el **EAPOL** (AAA) a los
   autenticados, y la **copia del ARP** (punto 2) a los de IP fija. **Un invitado es un cliente DHCP normal: ONOS lo aprende al primer `DISCOVER`, antes de
   que haga login.** No necesita estar en ninguna lista. La lista de **hosts fijos** es solo para máquinas de IP fija que nunca hablan con el controlador:
   el **server** (con las MAC de sus dos tarjetas y sus puertos) y el **portal**. El vigilante la une a lo que diga ONOS.
2. **Visibilidad de los hosts de usuario.** La regla de difusión de **entrada por un puerto de acceso** añade `OUTPUT CONTROLLER`: ONOS recibe una copia del
   ARP (aprende MAC, IP y ubicación) y **no reenvía nada** (no hay `proxyarp`), así que no hay riesgo de inundación.
3. **Camino rápido para hosts nuevos (aceptado).** Si la diferencia son **solo altas de flujos unicast** (ni borrados ni cambios de difusión), el vigilante
   las aplica de inmediato, sin esperar los 15 s: no pueden formar lazos. Un host nuevo recibe su tráfico en ≤5 s.
4. **Server con dos tarjetas** (una por core), en la misma subred. El host se queda con la MAC de la tarjeta que le conteste, y hacen falta flujos hacia
   las dos. Además, Linux puede descartar paquetes que llegan por la segunda tarjeta si `rp_filter` es estricto (ver 6).
5. **Los servicios en una sola VM, con UNA sola IP (decidido).** La política ya distingue los servicios por **IP y puerto TCP** (todos los puertos son únicos:
   8001-8003, 8101-8105, portal 8443); la tabla 60 solo mira la **MAC** de destino. El server tendrá **una única IP de servicios**: se propone **10.0.0.10**
   (fuera del rango DHCP 100-199 y distinta del servidor DHCP .250; falta comprobar que está libre). Todos los servicios y el portal pasan a esa IP, cada
   uno con su puerto. El cambio va en el `policy.local.yaml` de este slice (que ya mezcla sobre `policy.yaml`): el laboratorio de Mininet conserva sus .40/.50/.60.

### 3.5 R5
La regla de espejo (41000) pasa de `NORMAL + sensores` a `goto_table:60 + sensores` (la copia a los sensores es una acción inmediata y no cambia).

## 4. Invariantes y pruebas (sin red, antes de tocar nada)
1. **Ningún flujo generado inunda**: con `reenvio: proactivo`, `flujos_que_inundan(...)` vacío para cada switch y cada rol.
2. **Todo lo que era `NORMAL` es ahora `goto_table:60`** y no cambia ninguna regla de política (mismas coincidencias, mismas prioridades).
3. **Las tramas de control no se pierden**: ninguna regla de confianza casa LLDP, BDDP ni EAPOL.
4. **Simulador de la tubería completa** (tabla 0 → tabla de rol → tabla 60) con paquetes de ejemplo: alumno → servicio permitido (llega), alumno → sistema de notas (se descarta), respuesta del servidor → alumno (llega), invitado → portal (llega), invitado → servicio (se descarta), DHCP (al controlador), broadcast (nunca duplicado). Sobre la topología de 14 VM y con cada fallo simple.
5. Las pruebas existentes de R2 y R5 con la bandera en `normal` siguen pasando sin cambios.

## 5. Despliegue (orden elegido: primero la entrega, después la entrada)
0. **Código con la bandera apagada:** no cambia nada en la red.
1. **Preparar el `server`:** alias de IP (.40, .50, .60), `rp_filter` flexible, y comprobar que los servicios están escuchando (`ss -ltn`).
2. **Hosts fijos** (MAC de las dos tarjetas del server y del portal) y **vista previa**: deben existir los flujos unicast hacia el server en cada switch.
3. **Núcleo y borde** (sin usuarios; solo `IPv4 → tabla 60` en puertos troncales y del server, más las reglas LLDP/BDDP), uno a uno, con el diagnóstico 95 y el
   freno abiertos.
4. **`sw-acc-1`**: borrar y reinstalar sus flujos de R2 con la bandera activa. Prueba de punta a punta con un usuario real: autenticar, llegar a un servicio
   permitido, ser rechazado en uno prohibido, y un invitado que llega al portal.
5. **`sw-acc-2`**, y por último la bandera global.
6. **Reversible:** apagar la bandera y volver a sincronizar devuelve los flujos a `NORMAL`.
Razón del orden: cuando un acceso empiece a entregar a la tabla 60, el núcleo ya debe saber reenviar; si fuera al revés, el tráfico se descartaría.

## 6. Decisiones
**Tomadas (16-nov-2026):** orden de despliegue recomendado (arriba); camino rápido para altas unicast aceptado; los servicios viven todos en el `server` con
**una sola IP**; `rp_filter` **ya vale 2** en `all`, `ens4` y `ens5` del server (comprobado): no se cambia nada.
**Pendiente:** confirmar que 10.0.0.10 está libre (`ip -br addr` en el server) y las dos capas de 3.3 (se asumen aceptadas si no hay objeción).

**`rp_filter`, en corto.** Es una protección de Linux: al llegar un paquete por una interfaz con origen S, el kernel comprueba si respondería a S por esa misma
interfaz; en modo estricto (1), si no, lo descarta en silencio. El server tiene dos tarjetas en la misma subred y **una sola ruta** hacia ella: lo que llega por
la segunda tarjeta se respondería por la primera, y el kernel lo tiraría aunque la red lo haya entregado bien. Con `rp_filter=2` (flexible) o `0` no lo hace.

## 7. Riesgos
- **El mayor es el de las reglas de confianza** (3.3): sin la condición de tipo de trama, se tragarían los enlaces y la red perdería topología.
- **Sesiones activas durante el cambio:** al reinstalar las tablas de rol de un switch, los usuarios con sesión pierden tráfico unos instantes (no la sesión, que está en el flujo 40001 y en el Policy Mapper).
- **Servicio inalcanzable por falta de host fijo** es el fallo más probable al principio; la vista previa de flujos debe mostrarlo antes de activar nada.

## 8. Qué protocolos llegan y cuáles no
La política es de **capa 3 y 4**, no solo IP: las reglas de las tablas de rol casan `IPv4 + TCP + ip_dst del servicio/32 + puerto TCP`. **Todo lo que no
coincide se descarta** (prioridad 0): FTP, SMTP, SSH, Telnet, ICMP, UDP y cualquier otro puerto o IP no figuran en `services`, así que **no llegan** (el SSH
al puerto 22 del server se descarta salvo que se añada como servicio). La tabla 60 solo decide **por dónde** va lo ya permitido. Excepciones, que existen hoy
y no cambian con la fase E: el **ARP**, el **DHCP** (va al controlador) y el tráfico que entra por **puertos de confianza** (servidores y troncales), que no se
filtra: la política protege a los servicios *de los usuarios* y no a los usuarios *de los servidores*. Endurecerlo (por ejemplo, que la confianza del server
solo deje salir TCP desde los puertos de los servicios) es posible, pero queda fuera de esta fase.

## 9. Cambio aparte, NO se hace ahora: la lista negra sobre el EAPOL
Hoy el EAPOL del puerto (50000) está **sobre** la lista negra (45000), y una prueba (`test_prioridades`) lo exige. La lista negra casa `in_port + eth_src` de
una MAC concreta; la regla de EAPOL casa el puerto entero y **no se borra nunca** (solo se inyecta al crear una sesión). Consecuencia: una MAC bloqueada
puede seguir enviando EAPOL durante el bloqueo, volver a autenticarse y dejar una sesión que se activa sola al expirar el bloqueo. Poner la lista negra por
encima del EAPOL (por ejemplo 50500) evita que una MAC bloqueada intente siquiera autenticar. Toca R1 y R5 (la constante, `prioridades.yaml`, el panel y la
prueba de orden), por eso se hace como un cambio aparte, después de la fase E.

## 10. Servicios más ricos sin perder el "denegar por defecto"
Una red real tiene SSH, SMTP, FTP, Telnet, etc. La solución no es dejar pasar todo, sino que **`services` admita más que un puerto TCP**: `proto`
(`tcp`, `udp`, `icmp` o `any`) y `puertos` (lista o rangos). Cada entrada genera sus reglas (`ip_proto` + `tcp_dst`/`udp_dst`, o solo `ip_dst` para `any`) y la
matriz `rules` sigue diciendo **qué rol** puede usarlas. Ejemplo: `ssh: {ip: 10.0.0.10, proto: tcp, puertos: [22]}` permitido a `administrator`. Lo que no figura
sigue descartado. Es un cambio pequeño en `flujos_tabla_rol` y se hace después de la fase E.

## 11. Lo que llega de afuera (datos de esta topología)
- **El borde** tiene `ens3` con la IP pública (10.20.11.14), `ens5` en la red de gestión (192.168.201.254) y `br0` (el plano de datos) **sin IP y en `DOWN`**
  (comprobado). No hay ninguna ruta entre `br0` y la red pública.
- **Entrada desde Internet:** solo el SSH a cada VM por `iptables DNAT`, que va a la **red de gestión** (192.168.201.0/24). **Nada entra al plano de datos**
  (10.0.0.0/24).
- **Salida de los usuarios a Internet:** las VM usan como ruta por defecto 192.168.201.254 (la gestión, con NAT del borde); la interfaz de datos no es la salida.
  Es decir, el acceso a Internet **no pasa por R2**, y el servicio `internet` de `policy.yaml` no tiene host ni flujos. Es un artefacto de VNRT.
- **host-jhon** (el atacante) cuelga del puerto 1 del borde y además tiene su propia IP pública por `ens3`. En el plano de datos se trata como **un usuario más**
  (cuarentena o sesión).
- Para meter Internet bajo la política de R2 haría falta un **gateway L3 en el borde** (puerto interno de `br0` con IP y NAT) y el `internet` como servicio.
  Queda fuera de la fase E.
