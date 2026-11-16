# DHCP por subred como app de ONOS (propuesta de diseño)

**Estado:** diseño, sin código. Resuelve a la vez dos cosas pendientes de la capa 3: (1) DHCP por subred (la app `org.onosproject.dhcp` tiene **un solo rango**, `10.0.0.100-199`, y en capa 3 cada subred necesita el suyo) y (2) que el vigilante conozca la IP de un equipo **cuando se la dan**, que es lo que necesita un invitado para llegar al portal antes de validarse (`PLAN_CONMUTACION_CAPA3.md` §4 bis).

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

## 3. Flujo (cliente en `acc-1`, puerto 1, subred `10.0.1.0/24`)
1. DISCOVER (0.0.0.0 a broadcast) entra por acc-1/1; R2 lo sube al controlador (40500), con o sin sesión 802.1X.
2. La app mira (acc-1, 1), encuentra la subred `10.0.1.0/24`, elige una IP libre del ámbito (por ejemplo `.100-.199`), la reserva unos segundos y envía el OFFER por un packet-out al mismo puerto (a broadcast; todo cliente en INIT lo acepta).
3. REQUEST, ACK: la concesión queda confirmada (tabla distribuida de ONOS) y la app llama a `hostDetected(mac, ubicación, ip)`.
4. El vigilante, en su siguiente ciclo (5 s), ve el host con IP y monta la entrega /32: **alta rápida**, sin esperar estabilidad y sin tocar ninguna regla existente.
5. El cliente resuelve el gateway (la app Gateway ARP contesta) y ya puede hablar con el portal. Tras validarse, su sesión lo pasa a su rol (R2) con la misma IP.
6. Renovaciones: el cliente pide al servidor (`.250` de la subred); un flujo del ARP de esa IP (se añade como un "gateway" más en la configuración de la app Gateway ARP) y la regla 40500 lo llevan a la app.
7. Concesión vencida: la app libera la IP y retira el host (`hostVanished`); el vigilante quita la entrega.

## 4. Configuración (generada del mismo plan de subredes)
Clave `campusdhcp`, por subred: switch, puertos, prefijo, gateway, ámbito dinámico, IP y MAC del servidor virtual (`.250`), DNS, concesión. Un generador (como `generar_config_gateway_arp.py`) la saca de `R2/policy/planes/*.json`. Las estáticas (`.1-.99`: server, portal, servicios) quedan fuera del ámbito y siguen en `hosts_fijos`.

## 5. Qué se puede probar sin tocar hosts reales
- **Offline:** la lógica del ámbito (asignar, renovar, vencer, no repetir, ámbito agotado) y la construcción de OFFER y ACK se prueban con pruebas Java ejecutadas en Docker, sin ONOS.
- **En vivo y aislado:** un cliente DHCP de prueba en h1 con una **interfaz macvlan** (otra MAC sobre ens4, que entra por acc-1/1): obtiene IP de `10.0.1.0/24`, se ve en `/hosts` con su IP, y el vigilante monta su /32 (comprobable sin tráfico de usuarios). Se retira borrando la interfaz.

## 6. Riesgos y decisiones abiertas
1. **Cambiar el servidor DHCP de la red** (desactivar `org.onosproject.dhcp`, activar la nuestra): hoy los hosts de la slice tienen IP fija, así que no hay clientes que se queden sin IP; con usuarios reales habría que hacerlo en ventana.
2. **API de ONOS 2.7 sin verificar:** `HostProviderRegistry`, `DHCP` de `onlab-packet`, packet-out; se comprueba compilando (como pasó con `NICIRA_MOV_*`).
3. **Clúster de ONOS:** la concesión va en un mapa consistente (`StorageService`) para que un cambio de nodo maestro no duplique IP.
4. **Portal con capa 3:** el portal no está desplegado en la slice; su reescritura de destino (R2, `flujos_portal`) y la cuarentena en modo proactivo hay que probarlas con las rutas por gateway. Fuera de esta app.
5. **Seguridad:** un cliente puede agotar el ámbito con MAC falsas (DHCP starvation); mitigación posterior (límite de concesiones por puerto).
6. **Reservas por MAC** (un equipo siempre con la misma IP): no incluidas en la primera versión.

## 7. Plan de trabajo
1. Esqueleto de la app y la lógica del ámbito, con pruebas en Docker.
2. Generador de configuración y su prueba.
3. Compilar el `.oar` y un playbook de instalación (desactiva el DHCP estándar, instala la nuestra, publica la configuración).
4. Prueba en vivo con la interfaz macvlan de h1 (aislada, reversible).
5. Documentar y, solo entonces, decidir si h1 y h2 pasan a DHCP.
