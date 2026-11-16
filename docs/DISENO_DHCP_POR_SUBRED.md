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

## 6. Defensa contra el agotamiento del ámbito y el abuso (DHCP starvation, servidor falso)

Un atacante en un puerto de acceso puede pedir IP con una MAC distinta cada vez hasta vaciar el ámbito (nadie más obtiene IP), o inundar al controlador con DHCP, o contestar como un servidor DHCP falso. Se defiende **en capas**, porque ninguna sola sirve:

| Capa | Qué hace | Dónde | Estado |
|---|---|---|---|
| **Cuota por puerto** | un puerto de acceso no puede tener más de N concesiones y ofertas a la vez (N=3 por defecto), por muchas MAC que invente | app (.oar) | **hecho y probado** |
| Una concesión por MAC, oferta corta | una MAC reutiliza su IP; las ofertas sin confirmar vencen a los 15 s y liberan IP y cuota | app | **hecho y probado** |
| Límite de tasa por puerto | 2 solicitudes/s en régimen, ráfaga de 8: una inundación se descarta sin tocar el ámbito | app | **hecho y probado** |
| Validación | la MAC del cliente (`chaddr`) debe ser la MAC de origen de la trama; solo se atiende lo que entra por un puerto con ámbito; un REQUEST para otro servidor se ignora; una IP declinada se aparta 5 min | app | **hecho y probado** |
| Ámbito por subred | el daño de un puerto queda en su subred (las demás siguen dando IP) | app | **hecho y probado** |
| **Medidor (meter) en el switch**: el de R1 para MAB (ARP y DHCP, **10 pps con ráfaga 10 por puerto**, exceso DROP) sobre la regla del DHCP al controlador (40500) | protege el **canal OpenFlow y la CPU del controlador** de una inundación de DHCP, que la app aún tendría que recibir para descartar | R2 (`policy_tables.flujos_dhcp`) + `ovs` | por hacer; antes hay que comprobar en vivo que ONOS 2.7 crea el medidor y acepta la instrucción por REST |
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
4. Prueba en vivo con interfaces macvlan en h1 (aislada, reversible): `83_prueba_dhcp.yml` (una; y `-e prueba=agotamiento` para ver la cuota por puerto en acción). **Pendiente de ejecutar.** Riesgo: si VNRT filtra por MAC de origen en el puerto de h1, las interfaces no reciben respuesta.
5. Documentar y, solo entonces, decidir si h1 y h2 pasan a DHCP.
