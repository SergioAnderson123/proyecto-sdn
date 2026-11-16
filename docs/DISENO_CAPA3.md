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

**Prueba de viabilidad (30 minutos, sin riesgo):** `python3 spike_capa3.py --dispositivo acc-1` muestra 9 flujos de prueba; con `--aplicar` los envía **de a uno** a la **tabla 61, a la que nada salta**, con prioridad 1 y sin salida real
(son inertes: ningún paquete puede llegar a ellos) y dice cuáles acepta ONOS y cuáles rechaza (con el mensaje). `--quitar --aplicar` los borra. Con ese resultado se elige el camino.

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
