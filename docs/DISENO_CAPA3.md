# Diseño de la capa 3 por subredes (escalabilidad del reenvío proactivo)

Estado (16-nov-2026): **diseño + cálculo + simulación + pruebas sin red**. Nada está desplegado. Código: `R2/policy/capa3.py`, `benchmark_capa3.py`, `spike_capa3.py`,
`tests/test_capa3.py` y el simulador `tests/simulador_pipeline.py`. Contexto: `RESULTADOS_FASE_E.md` (lo que ya funciona en vivo) y `DISENO_FLUJOS_PROACTIVOS.md` (el reenvío por árbol de capa 2).

## 1. El problema que resuelve
Hoy (capa 2) cada switch tiene **una regla por host** en la tabla 60 y el ARP en difusión recorre un árbol que llega a *todos* los hosts. Con S switches y H hosts: S×H reglas. Medido con hosts
sintéticos (acceso de 48 puertos, 2 cores):

| Accesos | Hosts | Capa 2: reglas totales | Capa 2: máx. en un switch | Capa 3: totales | Capa 3: máx. en un switch | Capa 3: reglas del core-1 |
|---|---|---|---|---|---|---|
| 5 | 100 | 959 | 151 | 717 | 141 | 6 |
| 20 | 960 | 22 144 | 1 011 | 4 282 | 212 | 21 |
| 50 | 2 400 | 127 354 | 2 451 | 12 202 | 242 | 51 |
| 100 | 4 800 | 494 704 | 4 901 | 29 402 | 292 | 101 |
| 200 | 9 600 | **1 949 404** | **9 801** | **78 802** | **392** | **201** |

(`python3 benchmark_capa3.py`; el cálculo completo del estado deseado tarda 8 s en capa 2 y 2,3 s en capa 3 con 9 600 hosts.) Un switch de hardware con 2–4 k entradas de TCAM no soportaría la capa 2 a partir de ~50 accesos;
con la capa 3 el peor switch sigue por debajo de 400 reglas. Además el vigilante lee todos los flujos en cada ciclo: leer 1,9 millones por REST es inviable, 79 mil es razonable.

**Fórmulas** (S = switches, A = accesos/subredes, H = hosts, P = puertos de acceso de un switch, h = hosts de un acceso):
- Capa 2: total ≈ S·H + S·(puertos del árbol) + S; cada switch ≥ H reglas.
- Capa 3, **rutas completas**: core = A + 1; acceso = 2h + 2P + (A−1) + 1; total ≈ A·(A−1) + 2H + 2·A·P. Crece como A² en total, pero **cada switch** solo como A.
- Capa 3, **ruta por defecto en los accesos** (`nucleo=[cores]`): core = A + 1; acceso = 2h + 3P + 1 (constante: no sabe cuántas subredes hay); total ≈ 2·A + 2H + 3·A·P. Lineal. Mejor que las rutas completas a partir de ~50 subredes.

## 2. Arquitectura
- **Una subred por switch de acceso** (o por grupo de puertos), p. ej. un /24 (254 hosts; un /26 alcanza para 48 puertos). El server vive en su propia subred de servicios, colgada del core.
- **Gateway virtual** por subred: una IP (.254) y **una MAC compartida** (`gw_mac`, p. ej. `02:00:5E:00:00:FE`). No existe en ningún equipo: lo contesta el switch de acceso de cada subred.
- **Dentro de la subred** (mismo switch): ARP por inundación **local** (solo a los puertos de acceso de esa subred; jamás por una troncal) y reenvío por MAC.
- **Entre subredes:** el host manda el paquete a la MAC del gateway; los switches enrutan por **prefijo** (1 regla por subred) siguiendo el árbol de caminos hacia el switch de la subred destino; el switch
  del destino reescribe las MAC (origen = gateway, destino = host), decrementa el TTL y entrega (1 regla por host, **solo en su propio switch**).
- **No hay árbol de difusión**: nada que inunde sale de un switch. Un lazo físico no puede multiplicar nada porque lo único que se reenvía entre switches es unicast por prefijo, y cada prefijo sigue su propio árbol (sin ciclos por construcción).

Recorrido de un paquete `alumno (10.0.1.101, acc-1/1) → server (10.0.0.10, core-1/8)`:
1. El host ya resolvió `10.0.1.254` por ARP (contestado por flujo en `acc-1`) y manda el TCP a la MAC `02:00:5E:00:00:FE`.
2. Tabla 0 de `acc-1`: sesión 40001 → tabla de rol (política por IP+puerto, **sin cambios**) → `goto 60`.
3. Tabla 60 de `acc-1`: ruta del prefijo `10.0.0.0/24` (`ETH_DST=gw, IPV4_DST=10.0.0.0/24`) → `dec_ttl`, sale por el puerto del árbol hacia `core-1`.
4. `core-1` (entra por una troncal con confianza IPv4 → tabla 60): regla de entrega `IPV4_DST=10.0.0.10/32` → `eth_src=gw`, `eth_dst=MAC del server`, `dec_ttl`, puerto 8.
La respuesta hace el camino inverso (la ruta del prefijo `10.0.1.0/24` en `core-1`, la entrega por /32 en `acc-1`). Se prueba en `test_capa3.py` para las 12 parejas de hosts, incluidos TTL y MAC finales.

## 3. Flujos por tipo de switch (todos en la tabla 60; las tablas de rol de R2 no cambian)
| Prioridad | Dónde | Coincidencia | Acción |
|---|---|---|---|
| 300 | acceso, por puerto de acceso | ARP petición (`ARP_OP=1`) con `ARP_TPA = IP del gateway` | convierte la petición en respuesta del gateway y sale por el puerto de entrada (sin controlador) |
| 200 | acceso, por puerto de acceso | ARP en difusión | sale por los **demás puertos de acceso de esa subred** |
| 160 | switch del host | `ETH_DST=gw` + `IPV4_DST=host/32` | `eth_src=gw`, `eth_dst=MAC`, `dec_ttl`, puerto del host |
| 150 | todos (o solo el núcleo) | `ETH_DST=gw` + `IPV4_DST=prefijo` | `dec_ttl`, puerto del árbol hacia el switch de esa subred |
| 120 | acceso, con ruta por defecto | `IN_PORT=acceso` + `ETH_DST=gw` | `dec_ttl`, puerto hacia la raíz (la regla *exige entrar por un puerto de acceso*: lo que llega por troncal sin destino local se descarta y **no rebota**) |
| 100 | switch del host | `ETH_DST=MAC` | puerto del host (tráfico dentro de la subred) |
| 1 | todos | — | descarte |

Ninguna acción es `NORMAL/FLOOD/ALL` (prueba `test_ningun_flujo_inunda...`) y ningún ARP cruza una troncal (prueba `test_ningun_arp_cruza_una_troncal_jamas`, para los 4 orígenes y 5 destinos).

## 4. Qué se mantiene igual
- **La política por rol** (tablas 10/20/40/50, cuarentena, portal): casan IP+TCP del destino y terminan en `goto 60`. Funciona igual porque el destino sigue siendo una IP de servicio; solo cambia lo que hace la tabla 60.
- **Confianza IPv4 de las troncales** y **LLDP/BDDP al controlador** (40100): sin cambios.
- **Sesiones 802.1X, EAPOL, DHCP al controlador, lista negra:** sin cambios.
- **El vigilante y su garantía "romper antes de montar"**: sigue valiendo. Los flujos de un prefijo son un árbol; cambiar de árbol borrando primero y montando después nunca reenvía por un ciclo (mismo argumento que en capa 2, ahora por prefijo).

## 5. Lo que cambia y las decisiones que hay que tomar
1. **Plan de direccionamiento** (el usuario decide). Propuesta para topologia14: `10.0.0.0/24` servicios (server `10.0.0.10`, en `core-1/5`), `10.0.1.0/24` acc-1, `10.0.2.0/24` acc-2, `10.0.3.0/24` borde (host-jhon, externo);
   gateway `.254` en cada una y `gw_mac` común. Los hosts de prueba pasarían de `10.0.0.201/202` a `10.0.1.x/10.0.2.x`.
2. **Movilidad:** un host que cambia de edificio cambia de subred y de IP (las conexiones abiertas se cortan). La política no se rompe porque va por rol (VLAN), no por IP de origen.
3. **DHCP por subred.** La app DHCP de ONOS tiene **un solo rango**. Opciones: (A) `org.onosproject.dhcprelay` + un servidor DHCP con un ámbito por subred (estándar; requiere configurar interfaces en ONOS); (B) un DHCP propio (p. ej. `dnsmasq` con relé) o asignación desde el Policy Mapper al autenticar;
   (C) mantener el rango único y solo sectorizar el direccionamiento (no sirve para agregar). Recomendación: A o B; es independiente del reenvío, se puede resolver después.
4. **Descubrimiento de hosts (IP→MAC→puerto).** Las reglas de entrega necesitan saberlo. ONOS lo aprende del ARP/DHCP que llega al controlador. La petición ARP al gateway es el sitio natural para copiarla (`copiar_arp=True`: una copia por host y por renovación de su caché ARP,
   no por paquete). Esto reabre la decisión de `--copiar-arp`, ahora con un costo acotado.
5. **El server con dos tarjetas** sigue sin redundancia real (ver `RESULTADOS_FASE_E.md`, hallazgo 6). En capa 3 la solución limpia es un *bond* activo-respaldo (una IP y una MAC); el plan ya trata al server como un host de la subred de servicios.

## 6. El riesgo técnico principal: el ARP del gateway
Contestar el ARP del gateway *sin controlador* exige: coincidir `ARP_OP` y `ARP_TPA`, usar acciones de extensión de Open vSwitch (copiar campos: `move ETH_SRC→ETH_DST`, `ARP_SHA→ARP_THA`, `ARP_SPA→ARP_TPA`), poner `ARP_OP/ARP_SHA/ARP_SPA`
y `salir por IN_PORT`; y para el reenvío, `DEC_TTL`. **El REST de ONOS 2.7 no sabía emparejar `ARP_TPA`** (ya lo comprobó el equipo en `flujos_portal`) y el formato de las extensiones por REST exige códigos numéricos que no conocemos. Por eso `capa3.py` genera esos flujos
en un formato interno que está **sin verificar contra ONOS**. Hay tres caminos, de menos a más trabajo:
- **REST** (el actual): si `spike_capa3.py` demuestra que ONOS acepta los criterios e instrucciones, no hay nada más que hacer.
- **Flujos estáticos con `ovs-ofctl`** (Ansible en cada switch de acceso, un archivo por switch) y `allowExtraneousRules=true` en ONOS (para que no los borre). Los flujos del ARP del gateway son estáticos por subred (no cambian con los hosts), así que encajan. `ovs-ofctl` admite todo (incluido el intercambio SPA/TPA con `push/pop`, que permite contestar para *cualquier* IP).
  Hay que reinstalarlos al reiniciar OVS (un servicio systemd que los re-aplique).
- **Una app mínima de ONOS (Java)** que instale esos flujos con la API nativa (así lo hace `openstacknetworking`) y, de paso, atienda el ARP en el controlador. Es lo más limpio y lo más caro (compilar y empaquetar un .oar).

**Pruebas de viabilidad en el slice (de menor a mayor riesgo; todas se pueden revertir y ninguna toca el tráfico de producción):**
| Fase | Qué responde | Cómo | Riesgo |
|---|---|---|---|
| **V1** | ¿El REST de ONOS acepta ARP_OP, ARP_TPA, DEC_TTL, modificación de campos ARP, salida por IN_PORT y las extensiones de Nicira? | `python3 R2/policy/spike_capa3.py --dispositivo acc-1 --aplicar` (9 flujos de prueba **de a uno**, en la **tabla 61, a la que nada salta**; `--quitar --aplicar` los borra) | Ninguno (flujos inertes) |
| **V2** | ¿Open vSwitch acepta las acciones (ARP del gateway con copia de campos, `dec_ttl`, reescritura de MAC)? ¿ONOS borra los flujos instalados por `ovs-ofctl` ("extraños")? | `97_viabilidad_capa3.yml` (paso 2: instala con `ovs-ofctl`, cuenta antes, justo después y a los 30 s) | Ninguno: los flujos de prueba solo se alcanzan con un ARP a `10.99.x.254` o una MAC de gateway de prueba |
| **V3** | ¿Un host real resuelve el gateway virtual y le llegan las respuestas (ARP con la MAC de prueba)? | `97` (paso 3) | Igual |
| **V4** | ¿Funciona el enrutamiento de verdad entre tres switches (ruta por prefijo, entrega /32, reescritura de MAC, TTL) y la producción sigue igual? | `97` (pasos 3 y 4: ping h1↔h2 y h1→server con TTL esperado, destino inexistente descartado, ARP de producción a `10.0.0.10`) | Igual |

**Aislamiento de V2–V4** (probado en la simulación: `test_el_trafico_de_produccion_se_comporta_igual_con_y_sin_los_flujos_de_prueba`): los flujos de capa 3 van a la **tabla 62**, a la que ningún flujo de producción salta. Se entra a ella solo por dos reglas de la tabla 0 (prioridad 40060):
el ARP cuya IP objetivo es un gateway de prueba (`10.99.x.254`) y el IPv4 cuya MAC destino es la MAC de gateway de prueba (`02:00:5E:99:99:FE`). Nadie más usa esas direcciones. Todos los flujos llevan la cookie `0x99c3…` y se borran de una vez
(`del-flows cookie=0x99c3000000000000/0xffff000000000000`); los de producción no la llevan. Los hosts reciben una **IP secundaria** de prueba (10.99.x.y) y una ruta `10.99.0.0/16` por su gateway virtual; la IP de producción no se toca.
**Subredes de prueba:** `10.99.0.0/24` (core-1, puerto 5: server `10.99.0.10`), `10.99.1.0/24` (acc-1, puerto 1: h1 `10.99.1.101`) y `10.99.2.0/24` (acc-2, puerto 1: h2 `10.99.2.102`); gateway `.254`.
**TTL esperado:** h1↔h2 llega con TTL 61 (tres saltos de enrutamiento: ruta en acc-1, ruta en core-1, entrega en acc-2) y h1→server con 62.
**Resultado de V1 (16-nov, en vivo, `sw-acc-1`): el REST de ONOS 2.7 solo acepta 2 de los 9 elementos.**
| Elemento | Resultado | Mensaje de ONOS |
|---|---|---|
| Criterio `ARP_OP` | **rechaza** | `Type ARP_OP is unknown` |
| Criterio `ARP_TPA` | **rechaza** | `Type ARP_TPA is unknown` |
| Instrucción `DEC_TTL` | **acepta** | |
| Instrucciones `ARP_OP`, `ARP_SHA`, `ARP_SPA` | **rechaza** | `L3 Instruction subtype ... is not supported` |
| Salida por `IN_PORT` | **acepta** | |
| Extensiones de Nicira (copia de campos) | **rechaza** | por nombre: HTTP 500; por objeto: `Empty device identifier` (el formato pide el `deviceId`; aun acertándolo, faltan los criterios y las instrucciones ARP de arriba) |

**Consecuencia: el diseño se divide en dos.**
- **Lo dinámico va por REST, como hoy** (el vigilante): las rutas por prefijo, la entrega /32 con reescritura de MAC, la reescritura de TTL (`DEC_TTL` sí entra) y el reenvío por MAC solo usan criterios e instrucciones que ONOS acepta.
- **El ARP del gateway virtual NO se puede instalar por REST** (necesita `ARP_TPA`, `ARP_OP` y las instrucciones ARP). Es **estático** (una regla por puerto de acceso, no cambia con los hosts): se instala con `ovs-ofctl` desde Ansible en cada switch de acceso
  (y se reinstala si OVS se reinicia), con `allowExtraneousRules=true` en ONOS si este los borrara. Esa es la hipótesis de V2. La alternativa es una app de ONOS en Java.

**Resultado de la primera corrida de V2 (16-nov, en vivo, sin `allowExtraneousRules`):** `ovs-ofctl` instaló los flujos sin error en los 5 switches, pero **ONOS los borra por "extraños" en pocos segundos**: justo tras instalar (3 s) solo quedaban los de `sw-core-2` (5 de 5); a los 30 s, ninguno. Por eso la parte funcional (V3 y V4) falló con 100 % de pérdida: no era
un fallo del diseño sino que los flujos ya no estaban. La limpieza funcionó (0 flujos de prueba y solo la IP de producción en los hosts) y la producción no se alteró (el ARP al server siguió respondiendo). Consecuencia: los flujos estáticos del ARP del gateway **exigen `allowExtraneousRules=true`** en ONOS; el playbook 97 ahora lo pone por defecto durante la prueba y lo revierte.

**Resultado de V2–V4 con `allowExtraneousRules=true` (16-nov, en vivo): PASAN.**
| Prueba | Resultado medido |
|---|---|
| ONOS conoce la propiedad | `FlowRuleManager` lista `allowExtraneousRules: "true"` tras el POST (el nombre es correcto; también `importExtraneousRules=false`, `purgeOnDisconnection=false`, `fallbackFlowPollFrequency=30`) |
| V2: supervivencia a ONOS | los flujos de `ovs-ofctl` sobreviven 30 s en los 5 switches: `[5, 8, 5, 8, 8]` de `[5, 8, 5, 8, 8]` |
| V3: ARP del gateway | h1 y h2 resuelven `10.99.x.254` con la MAC de prueba `02:00:5e:99:99:fe` (la acepta Linux) |
| V4: enrutamiento | h1→h2 sin pérdida y **TTL 61**; h1→server sin pérdida y **TTL 62**; server→h1 y h2→h1 sin pérdida (el TTL coincide con lo predicho: un decremento por salto de ruta y otro al entregar) |
| IP inexistente (`10.99.2.77`) | 100 % de pérdida: se descarta sin inundar |
| Producción | el ARP al server (`10.0.0.10`) siguió contestando (2 de 2) y el 95 salió limpio antes y después |
| Limpieza | 0 flujos de prueba en los 5 switches, hosts solo con su IP de producción, la opción de ONOS revertida |
Salvedades: la prueba usó ping (ICMP) con la política de R2 esquivada a propósito (la entrada de la tabla 0 va antes de la sesión), así que **no** probó TCP con tablas de rol; y en el server el vecino del gateway apareció como `FAILED` en la primera lectura aunque el ping posterior funcionó (se resolvió después; no entendido del todo).
**Qué demuestra:** que Open vSwitch acepta el ARP del gateway con copia de campos, la reescritura de MAC y `dec_ttl`; que los hosts lo aceptan; que ONOS convive con los flujos estáticos si se le permite; y que el enrutamiento por prefijo entre tres switches funciona con el TTL y las MAC previstos. **Qué no demuestra aún:** los flujos dinámicos por REST (solo se probó que ONOS acepta `DEC_TTL`), la integración con el vigilante ni la política por rol sobre capa 3.

**Decisión (tras V1):** V1 rechazó el ARP, así que queda el camino de los flujos de ARP estáticos con `ovs-ofctl` (más `allowExtraneousRules=true` si ONOS los borra: `-e permitir_extranos=true`), **si V2 y V4 funcionan**. Si V2 falla (Open vSwitch no acepta algo o ONOS no tolera los flujos) → app de ONOS en Java.

### V5. La app de ONOS Gateway ARP, en vivo (16-nov, `allowExtraneousRules=false`)

`R2/onos-app/gateway-arp` + `76_gateway_arp.yml` resuelven el punto abierto de V2: el ARP del gateway se instala como flujo **de ONOS** (appId propio), así que ya no hace falta `allowExtraneousRules=true`.
- Resultado: 4 de 4 flujos `ADDED` a los 5 s y a los 40 s (uno en core-1, acc-1, acc-2 y borde; ninguno en core-2). `arping` a su gateway desde h1 y h2 y resolución del vecino desde el server: contestan los switches con la MAC `02:00:5E:00:00:FE`; el contador del flujo sube (acc-2: 0 a 3; core-1: 0 a 1). Producción intacta (arping a 10.0.0.10 sigue respondiendo).
- Hallazgos que costó encontrar: (1) ONOS dejó la app `ACTIVE` con el feature `Uninstalled` (carrera ya vista con la app AAA): el playbook ejecuta `feature:install gateway-arp` por Karaf, que es idempotente. (2) Las constantes de Nicira de ONOS 2.7 son `NICIRA_MOV_*`. (3) Los 5 switches (OVS 3.3.9) quedaban con driver `default`, porque el driver `ovs` de ONOS 2.7 solo coincide con `sw=2.*`: sin él no hay extensiones de Nicira. Se fuerza con netcfg (`devices/<id>` `basic.driver=ovs`); el `ovs` hereda el mismo pipeline que `default`, solo añade las extensiones. Se revierte con `DELETE .../devices/<id>/basic`.
- Pendiente: el driver ovs está forzado solo en los 4 switches del plan; core-2 sigue en `default`. Falta ver el vigilante en modo capa 3 contra ONOS con esta app.

### V6. El vigilante en modo capa 3 contra ONOS real, solo lectura (16-nov, `98_prueba_vigilante_capa3.yml`)

Un ciclo sin `--aplicar` con el plan de la slice, `--nucleo core-1,core-2,borde` y los 3 hosts con las IP del plan A. Leyó switches, enlaces y flujos reales sin errores y calculó la diferencia: **sobran 30 flujos** (los de reenvío L2 de hoy: unicast por MAC y difusión ARP por árbol) y **faltan 15** (entrega /32 con MAC y TTL en core-1, acc-1 y acc-2; rutas por prefijo en borde, core-2 y core-1). Raíz: core-1.
- Es la dimensión del cambio: aplicarlo ahora reemplazaría el L2 de producción y dejaría sin reenvío a los hosts reales (siguen con 10.0.0.201 y 10.0.0.202, fuera de sus subredes del plan) y a los hosts de ONOS sin IP. El único que hay es `FA:16:3E:05:44:06`, la segunda tarjeta del server (`ens5`, en core-2/5): no tiene IP y descarta el ARP (el server solo contesta por `ens4`), así que no necesita reglas de capa 3; el plan no la cubre a propósito. (En la salida del vigilante, `borde ... -> salidas ['3']` es el puerto de borde hacia core-2, no la ubicación del host.) Por eso no se aplica.
- Para aplicar de verdad hacen falta: IP del plan A en los hosts (DHCP por subred), una decisión para los hosts sin IP (hoy solo `ens5` del server; si se quiere redundancia del server, un bond con una sola IP) y una prueba aplicada en una tabla aparte con subredes de prueba antes de la conmutación.

### V7. El vigilante APLICA la capa 3 en vivo, aislado en la tabla 62 (16-nov, `77_prueba_vigilante_aislado.yml`)

Subredes de prueba 10.99.x, MAC de gateway de prueba `02:00:5E:99:99:FE`, `allowExtraneousRules=false`, app Gateway ARP con una MAC por gateway.
- **Aplicar:** el primer ciclo (`--tabla 62 --estable 0`) instaló 25 flujos (acc-1 5, acc-2 5, borde 5, core-1 6, core-2 5: una regla de entrada en la tabla 0 por switch y el resto en la tabla 62). El segundo ciclo: `sin cambios`.
- **Tráfico real:** el vecino del gateway en cada host fue la MAC de prueba; h1 a server 0 % de pérdida con TTL 62 (2 saltos), h1 a h2 0 % con TTL 61 (3 saltos), h2 a h1 y server a h1 0 %; el destino inexistente 10.99.2.77, 100 % perdido. Es decir, la capa 3 enruta entre subredes con TTL decrementado y MAC reescrita, con los flujos mantenidos por el vigilante y por la app (ambos de ONOS, sin flujos "extraños").
- **Aislamiento:** la tabla 60 no cambió salvo 1 flujo más de la app en core-1, acc-1 y acc-2 (el ARP de los gateways de prueba, IP que nadie usa); el ARP de producción a 10.0.0.10 siguió contestando.
- **Limpieza (`--retirar` y volver a publicar la configuración de producción):** tabla 62 y reglas de entrada en 0 en los 5 switches, la app con sus 4 flujos de producción y la tabla 60 igual que al empezar (8/11/7/8/8).
- Lo que se encontró por el camino: ONOS devuelve el `tableId` de `goto_table` como texto (el vigilante reponía las reglas de entrada en cada ciclo; corregido y con prueba); la app corriendo era la versión vieja hasta reinstalarla con `76 -e reinstalar=true` (el 77 ahora lo comprueba).
- **No cubierto en vivo:** la caída de un cable (afectaría a la producción); sigue probado solo en simulación (todos los estados intermedios sin lazo).

### V8. Conmutación a capa 3 en producción (16-nov, planes 66 a 69 y secuencia de `PLAN_CONMUTACION_CAPA3.md`)

Con sesiones 802.1X (h1 profesor01 = docente, h2 alumno01 = alumno) y el vigilante en modo capa 3 aplicando en la **tabla 60** con las subredes reales.
- **Punto de partida (L2):** docente a `10.0.0.10:8101` *refused*; alumno al 8101 *timed out* y al 8001 *refused*. `95` limpio.
- **Paso 2** (vigilante capa 3 aplica): la tabla 60 pasó de 42 flujos (8/11/7/8/8) a 27 (5/7/5/5/5): 30 menos (los L2) y 15 más (3 entregas /32 y las rutas), tal como lo simulado; 14 enlaces, app con 4 flujos, `95` limpio, el vigilante dice `sin cambios`.
- **Paso 3** (h1 y h2 sin la IP vieja, ruta por el gateway): docente (`10.0.1.101`) al 8101 → *refused*; alumno (`10.0.2.102`) → *timed out*. Es decir, **la política por rol sigue intacta con capa 3** y el tráfico del docente cruza las subredes por el gateway. Contador de los flujos de entrega /32: 0 → 2 paquetes (el SYN hacia el server y la respuesta hacia h1). `95` limpio, sin tormenta.
- Hallazgos del camino: el `ping` no sirve (R2 solo deja TCP a servicios del catálogo); la compuerta del playbook 69 leía mal una línea con dos contadores (corregido).
- **Estado resultante:** los vigilantes L2 están parados y deshabilitados (65 `parar`); `vigilante-capa3` activo con `--aplicar` en ctrl-3 (principal) y ctrl-2 (respaldo). La IP de h1 y h2 NO es persistente (un reinicio del host vuelve a la IP vieja, sin red). **No ejecutar `65_vigilante.yml` con aplicar, ni `60_proactivo.yml`/`70_fase_e.yml -e reiniciar=true` sin pensarlo:** restablecerían el reenvío L2 sobre la capa 3.
- **Pendiente:** la caída de un cable en vivo con la capa 3 (solo simulada), IP persistente en los hosts (netplan), DHCP por subred y hosts nuevos (ONOS no aprende sus IP), R5 y el bond del server.

### V9. Caída de un cable en vivo con la capa 3 (16-nov, `80_caida_cable.yml`)

Cable `acc-1/2 ↔ core-1/2` (la subida de h1 hacia core-1, raíz del árbol y donde está el server) bajado 60 s (22:07:18 a 22:08:19) mientras h1 (docente, sesión 802.1X) probaba `nc` a `10.0.0.10:8101` cada segundo.
- **Caída:** ONOS vio el cambio en ~2 s; el vigilante esperó 15 s de estabilidad y aplicó a las 22:07:35 (1 flujo reemplazado en acc-1, borde y core-1); h1 recuperó el servicio al segundo siguiente por el camino de reserva (acc-1/3, core-2, core-1/4). **Corte total: ~17 s** (2 s de detección + 15 s de estabilidad + <1 s de aplicación). Casi todo el corte es el parámetro `--estable`.
- **Vuelta del cable:** detectada a los 2 s y aplicada a los 16 s (respetando los 60 s mínimos entre cambios); h1 no registró ningún corte.
- **Parpadeo de 1 s** (primer intento, por un error del playbook): h1 perdió 1 s y el vigilante no cambió nada (exige estabilidad): no hay vaivén de rutas ante un cable inestable; el intervalo mínimo de 60 s acota además el número de cambios por minuto.
- **Ajuste posible:** `--estable` más bajo reduce el corte casi proporcionalmente (con 5 s se esperan ~7 s), a costa de reaccionar a caídas más breves; `68 -e aplicar=true -e estable=5`.
- Corrección en el camino: la primera ejecución dejó el cable caído solo 1 s porque `pause` solo corre en el primer host del play (ahora `sleep` en el propio switch).

## 7. Estado, pruebas y siguiente
**Hecho (sin red):** plan y validación de subredes (`Plan`, `cargar_plan`, 8 casos de plan inválido); `flujos_capa3` con rutas completas y ruta por defecto; entrega de punta a punta con MAC y TTL (12 parejas); tráfico dentro de la subred; ARP del gateway y ARP local; IP desconocida
y TTL agotado; **caída de cada uno de los 7 cables** y de un switch (todo sigue llegando, sin inundar ni lazos); propiedades de escala (el core no depende de H; un acceso crece 2 por host local; L3 < L2/2 con 960 hosts; ruta por defecto constante). **244 pruebas en total** (R2 + prioridades + plantillas).
**Falta, en este orden:**
1. La prueba de viabilidad en vivo (sección 6) y decidir el camino del ARP del gateway.
2. Decidir el plan de direccionamiento y el DHCP (sección 5).
3. Integrar en el vigilante: modo `--subredes archivo.json` (calcula con `flujos_capa3`, hosts con IP desde ONOS y `hosts_fijos`) y que el diagnóstico/explicar_ruta entiendan prefijos.
4. Simular la tubería completa (tablas de rol + capa 3) y ampliar el simulador al ARP real.
5. Optimización: una regla de ARP local por subred con un grupo `ALL` (el ARP local hoy lleva una regla por puerto: 2P reglas por acceso) y ARP del gateway sin `IN_PORT`.
6. Despliegue por fases como la fase E: primero la subred de servicios + gateway en un solo acceso, luego los demás; la bandera `reenvio` vuelve a capa 2 en cualquier momento.
