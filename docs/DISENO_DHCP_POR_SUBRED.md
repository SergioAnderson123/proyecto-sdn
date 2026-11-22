# DHCP por subred como app de ONOS (propuesta de diseño)

**Estado:** la lógica del servidor (ámbitos, defensas, OFFER/ACK/NAK) está escrita y probada con 20 pruebas Java en Docker, la conexión con ONOS **compila** (paquetes, `HostProvider`, configuración, vencimiento), y están el generador de configuración y los playbooks `82_dhcp_app.yml` (instalar o quitar) y `83_prueba_dhcp.yml` (prueba con interfaces macvlan en h1). **Falta ejecutarlo en vivo** (ver §8). Resuelve a la vez dos cosas pendientes de la capa 3: (1) DHCP por subred (la app `org.onosproject.dhcp` tiene **un solo rango**, `10.0.0.100-199`, y en capa 3 cada subred necesita el suyo) y (2) que el vigilante conozca la IP de un equipo **cuando se la dan**, que es lo que necesita un invitado para llegar al portal antes de validarse (`PLAN_CONMUTACION_CAPA3.md` §4 bis).

## 1. Idea
Una app de ONOS propia, `org.campus.dhcp`, que:
1. recibe el DHCP del cliente que R2 ya sube al controlador (regla 40500, UDP 68 a 67), **sin cambiar ninguna regla de R2**;
2. decide la subred por **el switch y el puerto por el que llegó** (el mismo plan de subredes que usan la app Gateway ARP y el vigilante) y entrega una IP de ese ámbito, con máscara, gateway (la IP `.254` virtual), DNS y concesión;
3. al confirmar la concesión **le dice a ONOS que ese host tiene esa IP** (con el `HostProvider` de ONOS: `hostDetected` con MAC, ubicación e IP). El vigilante ya lee `ipAddresses` de `/hosts`, así que **no hace falta cambiarlo**, y R5 (`ubicar_host(ip)`) también gana IP↔MAC fiables.

Reemplaza a la app DHCP estándar (hay que desactivar `org.onosproject.dhcp`: dos servidores contestando al mismo paquete se pelean).

## 2. Por qué así y no otra cosa
| Opción | Veredicto |
|---|---|
| Esta app propia | Encaja con el plan (misma fuente de subredes), sin servidor externo, y da la IP al vigilante en el momento. Es código nuevo (Java, ya tenemos el pipeline de compilación con Docker). |
| Relé de ONOS + servidor DHCP externo con un ámbito por subred | Estándar, pero pide configurar interfaces/IP en ONOS, un servidor aparte y un camino para que ONOS aprenda la IP de las concesiones: más piezas. |
| Seguir con la app de ONOS y todos en una subred | No sirve en capa 3. |
| Copiar el ARP al controlador | Resuelve el aprendizaje de IP pero no el DHCP por subred, y mete tráfico al controlador. |

## 2 bis. Cómo decide la app la subred de un host

**No usa un inventario de equipos: usa el lugar por el que entra la petición.** La configuración de la app (`campusdhcp`) trae una lista de ámbitos, y cada uno dice **en qué switch y en qué puertos de acceso** vive (`device`, `puertos`). Cuando llega un DHCP, ONOS dice por qué (switch, puerto) entró; la app busca el ámbito que atiende ese par y entrega una IP de él. Nada del cliente interviene en esa decisión (ni su MAC, ni lo que él pida), así que no se puede falsificar desde el equipo: es el cable por donde entra.
- Esa lista sale del **mismo plan de subredes** que usan la app Gateway ARP y el vigilante (`R2/policy/planes/*.json`), con `generar_config_dhcp.py`: es un inventario de **puertos y subredes**, no de hosts.
- Un puerto que no está en ninguna subred (por ejemplo los de servicios y externa, que no llevan DHCP) **no se atiende** y se deja pasar a otras apps: falla cerrado.
- Lo que sí cambia por equipo es el **rol** (docente, alumno, invitado...), y eso lo decide 802.1X, MAB o el portal, no el DHCP.
- Agregar un switch de acceso = una línea en el plan y regenerar la configuración (igual que con el ARP del gateway). Quitar un switch o cambiar un puerto, lo mismo.
- Si un equipo cambia de puerto o de switch, recibe IP de la subred del nuevo lugar en su siguiente petición; la concesión anterior sigue en su ámbito hasta vencer (o hasta su RELEASE).
- Si un puerto de acceso alimenta un pequeño switch con varios equipos, todos comparten la subred y la **cuota por puerto** (`maxPorPuerto`, ajustable).

## 2 ter. Cuánto dura una concesión y qué pasa cuando el equipo se va

**Dónde se fija:** en el **plan de subredes**, en el bloque `dhcp` de cada subred de acceso (`R2/policy/planes/vnrt_topologia14.json`):
`{"nombre": "acc-1", ..., "puertos": [1], "dhcp": {"leaseSeg": 900}}`. `generar_config_dhcp.py` lo copia a la configuración de la app; si una subred no trae bloque `dhcp` vale `--lease` del generador (3600 s). El mismo bloque admite `inicio`, `fin`, `servidor` y `dns`. Cambiar el valor = editar el plan y repetir `82_dhcp_app.yml` (la app recarga sus ámbitos; las concesiones en memoria se pierden).

**Cómo muere una concesión (tres caminos, del más rápido al más lento):**
1. **El cliente la libera** (RELEASE al apagarse bien): la app retira la IP de ONOS al instante.
2. **El puerto pierde el enlace** (cable desconectado) o **el switch cae**: la app escucha los eventos de puerto y de dispositivo de ONOS y **libera al instante** las concesiones de ese puerto o de ese switch; ONOS deja de tener la IP del host y el vigilante quita su entrega /32. Si el equipo vuelve a conectarse, su cliente pide la IP que tenía (arranque con IP anterior) y la recupera si sigue libre.
3. **Vence la concesión:** para un equipo que desaparece sin que el puerto lo note (un equipo detrás de otro switch, una máquina virtual apagada) la IP se retira sola a los `leaseSeg` segundos. Con 900 s (15 min) como máximo queda una IP "fantasma".

Límite honesto: en la red virtual de VNRT apagar o bajar la interfaz de un host **no** baja el puerto del switch, así que el camino 2 no se puede probar ahí con equipos reales; se prueba con la prueba Java (liberación por puerto) y, en VNRT, bajando la interfaz del propio switch (`80_caida_cable.yml` con el puerto de acceso).

## 2 quater. Concesiones compartidas entre nodos (mapa del cluster)
**El problema.** `Pool` guardaba las concesiones en memoria de la instancia. Al reinstalar la app, reiniciar un nodo o cambiar el maestro de un switch el ámbito empezaba vacío y podía ofrecerle a otro cliente **la IP de un host vivo** (se vio en vivo con h1: el cliente de prueba de `83` recibió `10.0.1.100`, la IP real de h1) hasta que ese host renovaba, a los 450 s.

**La solución.** Cada concesión confirmada se guarda en un `ConsistentMap` de ONOS llamado `campus-dhcp-concesiones` (sin vincularlo a la app, para que sobreviva a reinstalarla). Clave = MAC del cliente; valor = texto simple `ip|subred|puerto|vencimiento` (ejemplo `10.0.1.100|acc-1|of:0000e6925d87354b/1|1763830000000`), sin clases propias que serializar. `RegistroConcesion` lo codifica y lo lee; un texto corrupto o de otra versión se ignora.
- **Escribir:** cada ACK (alta y renovación) lo guarda; liberar, vencer, dar por ausente o caer el enlace lo borran.
- **Restaurar:** al activar la app y al recargar su configuración se leen del mapa y se meten en el ámbito (`Pool.restaurar`, idempotente, sin aplicar la cuota del puerto). Se ignora lo vencido, lo de otra subred o puerto y lo que cae fuera del ámbito dinámico. Si dos equipos reclaman la misma IP gana la concesión más nueva.
- **Mantener la copia de cada nodo:** un *listener* del mapa refleja las altas y bajas de los demás nodos, así un maestro nuevo ya sabe quién tiene qué.
- **Solo el maestro del switch actúa.** Con el estado compartido todos los nodos tienen todas las concesiones, pero solo el maestro de un switch recibe sus paquetes y por tanto las respuestas ARP del sondeo. Un nodo que no lo es y sondeara daría por ausente a un equipo vivo y le pediría al Policy Mapper que cierre su sesión. Por eso el sondeo de presencia (`Servidor.objetivos(predicado)`), los avisos a ONOS y los borrados del mapa se hacen solo en el maestro (`esMio`); los demás solo reflejan.
- **Anunciar a ONOS tras restaurar.** ONOS pierde los hosts de una app que se reinstala, así que cada 10 s el maestro anuncia (`hostDetected`) los equipos con concesión que aún no anunció; así `/hosts` vuelve a traer la IP y el vigilante a crear la `/32`.
- **Limpieza.** Cada 10 s se quitan del mapa los registros vencidos hace más de un minuto (borrado condicional: si alguien lo renovó entretanto no se toca).
- **Si el almacén falla** (no se puede abrir el mapa, o una escritura da error) el DHCP no se detiene: se avisa en el log y se sigue con la memoria local, como antes.

**Cómo se prueba.** Offline, `PersistenciaTest` (15 pruebas): el caso del fallo (sin restaurar se ofrece la IP del equipo vivo; restaurando, no), renovar tras restaurar, vencidas, registros que no encajan, idempotencia, choque de dos equipos, avisos atrasados, filtro de objetivos por maestro, codificación y textos corruptos. Lo que depende de ONOS (el `ConsistentMap`, el listener, el maestro) solo se puede comprobar en vivo: reinstalar la app con `82 -e reinstalar=true` y ver que el log dice `N concesiones restauradas del mapa del cluster`, que `83` ya no recibe la IP de h1 y que h1 y h2 conservan sus IP y sus `/32`.

**Lo que NO resuelve.** Un host con IP fija dentro del ámbito dinámico (`.100`-`.199`) que nunca pidió DHCP no figura en el mapa; para eso es el sondeo ARP previo (§2 quinquies). La primera instalación que trae este cambio parte del mapa vacío, porque la versión anterior no guardaba nada: ese arranque sí necesita que los hosts renueven (`networkctl reconfigure ens4`).

## 2 quinquies. Sondeo ARP previo a ofrecer una IP (opción B)
**El problema.** El mapa del cluster (§2 quater) solo conoce a quien pidió IP por DHCP. Un equipo con **IP fija dentro del ámbito dinámico** (`.100`-`.199`, por ejemplo un equipo que antes era cliente DHCP y se pasó a fija, o una IP escrita a mano por error) no figura en ningún sitio: la app podía ofrecerle su IP a otro cliente y dejar **dos equipos con la misma dirección**. Es el mismo riesgo que el cliente DHCP común evita con el ARP de conflicto (RFC 5227), pero aquí lo hace el servidor.

**La solución.** Antes de ofrecer una IP **nueva** a un cliente, la app pregunta por ARP si alguien la tiene y espera `sondeoPrevioMs` (800 ms por defecto):
1. DISCOVER: el servidor reserva una candidata como siempre, pero **no envía el OFFER todavía**. Pide a la app un ARP de difusión «¿quién tiene `X`?» (emisor: el gateway virtual, igual que el sondeo de presencia) por **todos los puertos de acceso de esa subred**.
2. Un equipo que tenga `X` contesta al gateway; esa respuesta ya sube al controlador por el flujo propio de la tabla 60 (prioridad 310) que usa la presencia, **sin reglas nuevas**. Si contesta **otro** equipo distinto del cliente, `X` se **aparta** (el tiempo de una IP declinada, 300 s), se libera la reserva y se prueba la siguiente candidata, hasta `sondeoIntentos` (5). Si agota los intentos no ofrece nada y el cliente reintenta.
3. Si nadie contesta en el plazo (un hilo propio revisa cada 100 ms), se envía el OFFER con `X`. El cliente que repite su DISCOVER mientras tanto no abre otro sondeo (se ignora como «sondeo previo en curso»).

**Qué no se sondea.** Una oferta o concesión que el cliente ya tenía (renovaciones, DISCOVER repetido tras el OFFER, REQUEST de la IP ofrecida): ya se comprobó o es suya. La concesión confirmada que renueva por unicast tampoco. Sí se sondea el **REQUEST de un equipo sin registro** que pide una IP concreta (arranque con una IP anterior, por ejemplo tras perder el estado): se espera igual y, si contesta otro equipo, recibe **NAK** y vuelve a empezar con DISCOVER.

**Defensas que se conservan.** La cuota por puerto y el límite de tasa se aplican antes de sondear: un atacante con MAC falsas no puede llenar la lista de espera (las ofertas ya cuentan en su cuota; además hay un tope de 256 equipos esperando). La respuesta ARP solo cuenta si va **dirigida a la MAC del gateway**; las de otros equipos no se miran. Solo el maestro del switch recibe el DISCOVER y las respuestas, así que la lista de espera es local a ese nodo; si el mando cambia a mitad, el cliente reintenta y lo atiende el nuevo maestro.

**Cómo se configura.** En la política de la app (`politica.sondeoPrevioMs` y `politica.sondeoIntentos`), que saca `generar_config_dhcp.py` (`--sondeo-previo-ms 800`, `--sondeo-intentos 5`). `82_dhcp_app.yml` lo pasa con `-e sondeo_previo_ms=800 -e sondeo_intentos=5`; **`-e sondeo_previo_ms=0` lo apaga** sin quitar la app (solo cambia la configuración: la app recarga sus ámbitos). Sin la clave en la configuración la app lo trae apagado (comportamiento de antes) y sin `gwMac` (el emisor del ARP) se desactiva solo con un aviso.

**Costo.** Una IP nueva tarda `sondeoPrevioMs` más en ofrecerse (0,8 s; el cliente DHCP normal espera varios segundos al OFFER). No cuesta nada cuando se renueva.

**Límites honestos.**
- Un equipo que **no contesta a ARP** (apagado, o con el cable fuera) no se ve: si luego aparece con esa IP habrá conflicto. Eso lo cubre el ARP de conflicto del propio cliente y su DECLINE (la IP se aparta 5 min).
- Solo se ve a los equipos **cuyas respuestas ARP llegan al controlador**: los de los puertos de acceso de esa subred. Un equipo con IP fija en otro segmento no figura en este ámbito por definición.
- Si la subred tiene varios puertos de acceso, el ARP sale por todos; un equipo de otra subred que reciba la difusión no la contesta (otra red, otra IP).

**Cómo se prueba.** Offline, `SondeoPrevioTest` (17 pruebas, con el tiempo como parámetro): la IP nueva no se ofrece de inmediato, se ofrece tras el plazo, si contesta otro equipo se aparta y se ofrece la siguiente, si contesta el propio cliente no es conflicto, una respuesta de otra IP se ignora, DISCOVER repetido, renovaciones y REQUEST de la IP ofrecida sin sondeo, sin sondeo previo todo igual que antes, desistir tras `sondeoIntentos`, cuota del puerto, REQUEST sin registro (ACK si nadie contesta, NAK si contesta otro y queda apartada), REQUEST fuera del ámbito, reserva vencida durante la espera y la política. Y en el generador (`test_generar_config_dhcp.py`). En vivo: `83_prueba_dhcp.yml -e prueba=ocupada` crea en h1 una macvlan `sq1` con IP fija `10.0.1.150/32` (y `arp_ignore=1` en `ens4`, que se restaura al limpiar, para que conteste solo `sq1`), y un cliente que **pide** esa IP: debe recibir otra, y el log de la app dice `la IP 10.0.1.150 ya la usa 02:aa:bb:00:01:0f ... se aparta y se busca otra`.

**Resultado en vivo (22-nov, `82 -e reinstalar=true` y `83`).** La app quedó `Active` en los tres nodos y la configuración llevó `sondeoPrevioMs: 800` y `sondeoIntentos: 5`. `83` normal: el cliente recibió ACK de `10.0.1.101` (h1 tiene la `.100`) con la opción 121 y su regla /32, y h1 conservó su IP. `83 -e prueba=ocupada`: la macvlan `sq1` (`02:aa:bb:00:01:0f`, **sin sesión 802.1X**) con `10.0.1.150/32`, y el cliente pidiendo esa IP: el log dijo `la IP 10.0.1.150 ya la usa 02:AA:BB:00:01:0F (contesto al sondeo ARP previo a 02:AA:BB:00:01:01): se aparta y se busca otra` y el cliente recibió ACK de `10.0.1.101` a ~0,9 s del sondeo. Queda así comprobado que **la respuesta ARP de un equipo sin sesión sí llega al controlador** (tabla 0 a tabla 60, prioridad 310). Efecto lateral esperado: ONOS aprendió a `sq1` con su IP por esa misma respuesta y el vigilante le montó una regla /32; la prueba borra el host de ONOS al terminar y la regla se retira en el siguiente ciclo del vigilante. **Control sin sondeo (mismo día):** con `82 -e sondeo_previo_ms=0` y la misma prueba `ocupada`, el cliente recibió ACK de **`10.0.1.150`**, la IP que ya tenía `sq1` (dos equipos con la misma dirección), y el log no dijo nada. Es el fallo que el sondeo previo evita; se volvió a dejar en 800 ms con `82 -e confirmar=true` y `95` salió limpio al final. **Regla /32 sobrante de `10.0.1.150`:** tras la prueba seguía en acc-1 (la de `.101`, cuyo host sigue en ONOS con `ips=[]`, sí se retiró). La causa no es la app DHCP sino la memoria del vigilante: `vigilante.py` guarda cada host que ha visto (`self.hosts`, «un host que ONOS deja de listar no se olvida», para no perder reglas si ONOS pierde hosts un momento), y la prueba borra a `sq1` de ONOS con un DELETE, así que el vigilante lo recuerda con su IP y mantiene su regla (el registro `journalctl -u vigilante-capa3` no vuelve a marcar `sobra` tras el 21:54:28). Es inofensiva con una prueba (apunta a un puerto sin host), pero si luego un cliente recibiera esa IP habría dos hosts recordados con la misma IP y dos reglas /32 con el mismo selector. Se limpia **reiniciando el servicio del vigilante** (`sudo systemctl restart vigilante-capa3` en el nodo activo, hoy ctrl-3): al arrancar sin memoria, la regla pasa a `sobra` y se retira tras su espera. **Comprobado el 22-nov:** tras reiniciar el servicio en ctrl-3, el vigilante marcó `sobra` el `.150` y su regla p100, aplicó a los 15 s y en acc-1 quedó solo `['10.0.1.100']`. Queda como pendiente aparte (no es de la opción B): que el vigilante pueda olvidar un host borrado de ONOS cuando otro host reclama su IP.

## 3. Flujo (cliente en `acc-1`, puerto 1, subred `10.0.1.0/24`)
1. DISCOVER (0.0.0.0 a broadcast) entra por acc-1/1; R2 lo sube al controlador (40500), con o sin sesión 802.1X.
2. La app mira (acc-1, 1), encuentra la subred `10.0.1.0/24`, elige una IP libre del ámbito (por ejemplo `.100-.199`), la reserva unos segundos, comprueba por ARP que nadie la usa (§2 quinquies, 0,8 s) y envía el OFFER por un packet-out al mismo puerto (a broadcast; todo cliente en INIT lo acepta).
3. REQUEST, ACK: la concesión queda confirmada (tabla distribuida de ONOS) y la app llama a `hostDetected(mac, ubicación, ip)`.
4. El vigilante, en su siguiente ciclo (5 s), ve el host con IP y monta la entrega /32: **alta rápida**, sin esperar estabilidad y sin tocar ninguna regla existente.
5. El cliente resuelve el gateway (la app Gateway ARP contesta) y ya puede hablar con el portal. Tras validarse, su sesión lo pasa a su rol (R2) con la misma IP.
6. Renovaciones: el cliente pide al servidor (`.250` de la subred); un flujo del ARP de esa IP (se añade como un "gateway" más en la configuración de la app Gateway ARP) y la regla 40500 lo llevan a la app.
7. Concesión vencida: la app libera la IP y retira el host (`hostVanished`); el vigilante quita la entrega.

## 4. Configuración (generada del mismo plan de subredes)
Clave `campusdhcp`, por subred: switch, puertos, prefijo, gateway, ámbito dinámico, IP y MAC del servidor virtual (`.250`), DNS, concesión. Un generador (como `generar_config_gateway_arp.py`) la saca de `R2/policy/planes/*.json`. Las estáticas (`.1-.99`: server, portal, servicios) quedan fuera del ámbito y siguen en `hosts_fijos`.

### 4 bis. Rutas por DHCP: la opción 121 (RFC 3442)
**Para qué.** Con solo la opción 3 (router) el cliente aprende una ruta por defecto. Los hosts de la slice tienen dos tarjetas: `ens3` (gestión, con su propia ruta por defecto) y `ens4` (campus). Si `ens4` aceptara también la ruta por defecto de DHCP, habría dos rutas por defecto compitiendo y se podría afectar a la gestión, así que sus netplan llevan `use-routes: false`; sin ella, `ens4` solo sabía llegar a su propia subred y la ruta hacia el resto del campus (`10.0.0.0/16 via <gateway>`) estaba **escrita a mano** en cada netplan. La opción 121 permite que esa ruta la anuncie la app.

**Qué hace la app.** Si una subred tiene `rutas` en su bloque `dhcp` del plan (`R2/policy/planes/vnrt_topologia14.json`: `"dhcp": {"leaseSeg": 900, "rutas": ["10.0.0.0/16"]}`), el OFFER y el ACK llevan la opción 121 con cada red y **el gateway de esa subred** (acc-1: `10.0.1.254`; acc-2: `10.0.2.254`). La opción 3 se sigue mandando (la 121 se suma, no la reemplaza). Un NAK no lleva rutas. Sin `rutas` no se manda la opción, y un cliente que no la entiende la ignora.

**Formato.** Por cada ruta: 1 byte con la longitud del prefijo, los `(longitud + 7) / 8` octetos significativos de la red y los 4 octetos del gateway. `10.0.0.0/16 via 10.0.1.254` son 7 bytes: `16, 10, 0, 10, 0, 1, 254`. Máximo 255 bytes en total (28 rutas); el generador y la app rechazan redes con bits de host, rutas repetidas o demasiadas.

**Cómo se prueba.** Java: 7 pruebas nuevas en `ServidorTest` (bytes exactos de OFFER y ACK, sin rutas, NAK, codificación de /24, /12, /32 y /0, rechazos). Python: `tests/test_generar_config_dhcp.py`. En vivo: el cliente de `83_prueba_dhcp.yml` decodifica la opción y la muestra como `"rutas": ["10.0.0.0/16 via 10.0.1.254"]`. En un host real, `94_hosts_dhcp.yml` pone por defecto `use-routes: true` y no escribe la ruta fija (antes comprueba en ONOS que la app anuncia `rutas` para la subred del host y se detiene si no); `-e ruta_fija=true` conserva ademas la ruta fija (retroceso inmediato) y `-e rutas_dhcp=false -e ruta_fija=true` vuelve a la forma anterior.

**Comprobado en h1 y h2 (22-nov).** La RFC dice que un cliente que recibe la 121 debe ignorar la opción 3, y `systemd-networkd` lo hace así con `use-routes: true`: la única ruta por defecto sigue siendo la de `ens3`, la ruta `10.0.0.0/16` llega por DHCP (`proto dhcp`) y la política se cumple (`nc` al 8001/8101 con sesión). Los dos hosts quedaron sin ruta fija en el netplan.

## 5. Qué se puede probar sin tocar hosts reales
- **Offline:** la lógica del ámbito (asignar, renovar, vencer, no repetir, ámbito agotado) y la construcción de OFFER y ACK se prueban con pruebas Java ejecutadas en Docker, sin ONOS.
- **En vivo y aislado:** un cliente DHCP de prueba en h1 con una **interfaz macvlan** (otra MAC sobre ens4, que entra por acc-1/1): obtiene IP de `10.0.1.0/24`, se ve en `/hosts` con su IP, y el vigilante monta su /32 (comprobable sin tráfico de usuarios). Se retira borrando la interfaz.

## 6. Defensa contra el agotamiento del ámbito y el abuso (DHCP starvation, servidor falso)

Un atacante en un puerto de acceso puede pedir IP con una MAC distinta cada vez hasta vaciar el ámbito (nadie más obtiene IP), o inundar al controlador con DHCP, o contestar como un servidor DHCP falso. Se defiende **en capas**, porque ninguna sola sirve:

| Capa | Qué hace | Dónde | Estado |
|---|---|---|---|
| **Cuota por puerto** | un puerto de acceso no puede tener más de N concesiones y ofertas a la vez (N=3 por defecto), por muchas MAC que invente | app (.oar) | **hecho y probado** |
| Una concesión por MAC, oferta corta | una MAC reutiliza su IP; las ofertas sin confirmar vencen a los 15 s y liberan IP y cuota | app | **hecho y probado** |
| Límite de tasa por puerto | 2 solicitudes/s en régimen, ráfaga de 8: una inundación se descarta sin tocar el ámbito | app | **hecho y probado** |
| Validación | la MAC del cliente (`chaddr`) debe ser la MAC de origen de la trama; solo se atiende lo que entra por un puerto con ámbito; un REQUEST para otro servidor se ignora; una IP declinada se aparta 5 min | app | **hecho y probado** |
| Ámbito por subred | el daño de un puerto queda en su subred (las demás siguen dando IP) | app | **hecho y probado** |
| **Medidor (meter) en el switch**: el de R1 para MAB (ARP y DHCP, **10 pps con ráfaga 10 por puerto**, exceso DROP) sobre la regla del DHCP al controlador (40500) | protege el **canal OpenFlow y la CPU del controlador** de una inundación de DHCP, que la app aún tendría que recibir para descartar | R2 (`policy_tables.flujos_dhcp`) + `ovs` | **verificado en vivo (16-nov)**: ONOS 2.7 crea el meter por REST y acepta el flujo con `METER`; falta generarlos por puerto (ver §9 ter) |
| **DROP de respuestas DHCP desde puertos de acceso** (UDP 67 a 68 que entra por un puerto de usuario) | cierra el servidor DHCP falso; nuestras respuestas salen por packet-out y no pasan por las tablas | R2 (mismo mecanismo que `dhcp_externo`, 40600) | por hacer |
| Cuota mayor para equipos autenticados, concesión corta antes de autenticar | un invitado o un equipo sin sesión no puede quedarse con muchas IP | app + AAA | futuro |

**Los meters son los mismos que define R1** (`R1/flujos_R1_R2.md`, diagrama `seguridadmeters.png`): EAPOL 20 pps con ráfaga 20 y MAB (ARP y DHCP) 10 pps con ráfaga 10, exceso DROP. Hoy solo existen en el diseño: **no están implementados en ningún código**, y `DESPLIEGUE_UBUNTU.md` pide comprobar primero que el Open vSwitch los soporta (`81_prueba_meters.yml`). Se aplican **por puerto** (un meter compartido sumaría el tráfico de todos los puertos y una inundación en uno dejaría sin servicio a los demás: hace falta un meter por puerto y clase). El DHCP de un equipo sin sesión es justamente el tráfico de la clase MAB, así que comparte su meter. El fallback MAB (`R2/mab_vm`, `fallback-mab-1.0.0.oar`, trampa de prioridad 20000) existe en el repositorio pero no está desplegado en la slice: su meter se define igual y se aplica cuando se despliegue. El límite de la app (2 solicitudes por segundo, ráfaga 8) es más estricto que el del switch (10 pps): el switch protege el canal del controlador y la app protege el ámbito.

**Por qué la política va en el .oar y no en los medidores.** Un medidor solo cuenta paquetes por segundo: no distingue "100 MAC distintas" de "un equipo insistente", y un atacante lento (una MAC nueva por segundo) vacía un ámbito de 100 direcciones en menos de dos minutos sin pasar ningún límite de tasa razonable. Lo único que lo frena es la **cuota por puerto**, y eso solo se puede contar en la app, que conoce MAC, puerto y concesión. A la inversa, la app sola deja al controlador expuesto a una inundación de paquetes: el medidor es el escudo de la CPU, no la defensa del ámbito. Por eso: **política en el .oar (ya hecha) + medidor y DROP de servidor falso en el switch (siguiente)**.

Límite conocido: la cuota por puerto asume pocos equipos por puerto (cable a un equipo; con un switch pequeño o varias máquinas virtuales se sube N por puerto). Un atacante sigue pudiendo ocupar N IP de su propia subred y **solo** de su puerto.

## 7. Riesgos y decisiones abiertas
1. **Cambiar el servidor DHCP de la red** (desactivar `org.onosproject.dhcp`, activar la nuestra): hoy los hosts de la slice tienen IP fija, así que no hay clientes que se queden sin IP; con usuarios reales habría que hacerlo en ventana.
2. **API de ONOS 2.7 sin verificar:** `HostProviderRegistry`, `DHCP` de `onlab-packet`, packet-out; se comprueba compilando (como pasó con `NICIRA_MOV_*`).
3. **Clúster de ONOS:** la concesión va en un mapa consistente (`StorageService`) para que un cambio de nodo maestro no duplique IP.
4. **Portal con capa 3:** el portal no está desplegado en la slice; su reescritura de destino (R2, `flujos_portal`) y la cuarentena en modo proactivo hay que probarlas con las rutas por gateway. Fuera de esta app.
6. **Reservas por MAC** (un equipo siempre con la misma IP): no incluidas en la primera versión.

## 8. Plan de trabajo
1. ~~Esqueleto de la app y la lógica del ámbito, con pruebas en Docker.~~ Hecho (20 pruebas).
2. ~~Generador de configuración y su prueba.~~ Hecho (`generar_config_dhcp.py`, 5 pruebas; el ARP del servidor DHCP `.250` de cada subred se añade a la app Gateway ARP para que la renovación por unicast funcione).
3. ~~Compilar el `.oar` y un playbook de instalación.~~ Hecho (`campus-dhcp-1.0.0.oar` compila; `82_dhcp_app.yml`, sin ejecutar).
4. Prueba en vivo con interfaces macvlan en h1 (aislada, reversible): `83_prueba_dhcp.yml`. **`una` ejecutada el 16-nov con éxito** (ver §9). Pendiente: `-e prueba=agotamiento` (la cuota por puerto en acción).
5. Documentar y, solo entonces, decidir si h1 y h2 pasan a DHCP.

## 9. Resultado en vivo (16-nov, `82_dhcp_app.yml` y `83_prueba_dhcp.yml`)

Cliente de prueba propio en una macvlan de h1 (MAC 02:aa:bb:00:01:01, entra por acc-1/1), con la app DHCP estándar desactivada:
- El cliente recibió ACK con `10.0.1.100`, router `10.0.1.254`, máscara `/24`, servidor `10.0.1.250` y **concesión de 900 s** (la que fija el bloque `dhcp` del plan de la slice).
- ONOS (`/hosts`) listó el equipo con `ips=['10.0.1.100']` ubicado en acc-1/1 y el vigilante montó su regla de entrega /32 (alta rápida).
- A los 15 s el cliente envió su RELEASE: ONOS retiró la IP (`ips=[]`) y **la regla /32 desapareció**. h1 conservó su IP real y su sesión; no quedó ninguna interfaz de prueba.
- Camino hasta aquí: dhclient no sirvió (AppArmor impedía su guion y no liberaba), y el cliente lanzado con `nohup` moría al cerrarse la tarea de Ansible; con `async` de Ansible llega a liberar.

**Ideas diferidas por decisión del equipo (no se hacen ahora):** (1) usar las señales de R1 (logoff y link-down que ya quitan la sesión de un equipo autenticado) para liberar también su concesión DHCP; (2) acortar la concesión (p. ej. 300 s) para acotar las IP de equipos que desaparecen sin avisar, a cambio de más renovaciones.

### 9 bis. La defensa contra el agotamiento en vivo (16-nov, `83_prueba_dhcp.yml -e prueba=agotamiento`)

Seis clientes con MAC distintas (02:aa:bb:00:01:01 a 06) por el mismo puerto (acc-1/1), a la vez:
- **3 de 6 recibieron IP** (`10.0.1.100`, `.101`, `.102`, concesión 900 s); los otros 3 quedaron en `SIN_OFERTA`: la **cuota por puerto** (3) hizo su trabajo.
- ONOS listó los 6 equipos pero **solo los 3 con IP** tienen regla de entrega /32; el log de la app trae `cuota del puerto of:0000e6925d87354b/1 agotada` (limitado a un aviso cada 10 s por puerto y motivo, para que una inundación no llene el log).
- La respuesta correcta frente a un atacante que pide con MAC falsas: solo ocupa 3 de las 100 IP de su subred y nada más.

Cabos sueltos de la prueba: (a) el playbook paraba los clientes con `pkill`, que también mataba el shell de Ansible (`rc -15`) y abortaba antes de limpiar; ahora la parada es por un archivo (`/tmp/dhcp_prueba_parar`) y la limpieza va en un bloque `always` (y se puede lanzar sola con `--tags limpiar`); (b) el log de la app mostraba avisos periódicos `chaddr distinto de la MAC de origen` por el puerto de h1 fuera de la prueba: **era un `dhclient` zombi del primer intento de prueba** (vivo desde las 23:00), que intentaba renovar su concesión con la MAC real de h1 como origen y el `chaddr` de una macvlan ya borrada; la defensa lo rechazó siempre, como debía. De paso confirmó que la renovación por unicast llega al servidor (la entrada ARP de `.250` de la app Gateway ARP funciona).

### 9 ter. Meters de R1 por el REST de ONOS (16-nov, `81` y `84`)

- **Open vSwitch 3.3.9** acepta meters y **descarta de verdad**: con 10 pps y ráfaga 20, de 47 tramas que llegaron al medidor descartó 19 (`81_prueba_meters.yml`).
- **ONOS 2.7 los crea por REST** (`84_prueba_meters_onos.yml`): `POST /onos/v1/meters/{deviceId}` con `{"deviceId", "unit": "PKTS_PER_SEC", "burst": true, "bands": [{"type": "DROP", "rate": 10, "burstSize": 20}]}` devuelve **HTTP 201** con el identificador en el encabezado `Location` (`.../meters/{deviceId}/{id}`), y el meter aparece en OVS con su banda. La lista `GET /meters/{deviceId}` lo muestra **más tarde** (el registro es asíncrono): hay que usar el id del `Location`, no esperar a verlo en la lista.
- **Un flujo con la instrucción `{"type": "METER", "meterId": N}`** se instala por `POST /flows/{deviceId}` y OVS lo muestra como `actions=meter:N`.
- **`DELETE` no debe declarar `Content-Type`** (ONOS contesta 415). Borrar el flujo y el meter son dos DELETE; borrar un flujo **no** borra su meter: hay que gestionar el ciclo de vida de los meters (crear antes que el flujo, borrar después).
- **Cómo aplicarlo sin tocar el Policy Mapper** (propuesta, sin implementar): un meter por puerto de acceso y clase (EAPOL 20 pps con ráfaga 20; DHCP 10 pps con ráfaga 10) y una regla **por puerto** de mayor prioridad que la actual (DHCP por puerto a 40510, sobre la general de 40500 y bajo la de externos, 40600; EAPOL por puerto a 50010, sobre la de sesión, 50000), con el meter y la salida al controlador. El exceso muere en el switch dentro de ese mismo flujo; la regla general queda de respaldo. Es aditivo y reversible (borrar las reglas y los meters).
