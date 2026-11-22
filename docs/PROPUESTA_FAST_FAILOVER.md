# Propuesta: conmutación rápida ante fallo (fast-failover) en el reenvío proactivo

**Estado: PROPUESTA, sin implementar ni probar en la red.** Escrita el 22-nov-2026 para tenerla a mano más adelante. No cambia ningún código ni configuración. Todo lo que aquí figura como medido sale de las pruebas del proyecto (`docs/DISENO_CAPA3.md`, V9 y V9 bis); lo que figura como **hipótesis** o **por verificar** no se ha comprobado.

## 1. Resumen

Hoy, cuando cae un cable, el switch no sabe qué hacer: espera a que el vigilante se entere por ONOS, recalcule los árboles e instale reglas nuevas. Tras el ajuste del 22-nov eso son **~3 s** (antes ~18 s). Con *grupos fast-failover* de OpenFlow cada switch llevaría **montado de antemano el camino de reserva** y cambiaría solo, sin consultar al controlador: el corte pasaría a depender de lo que tarda el switch en notar que el puerto cayó, **decenas de milisegundos** si el puerto local se cae y del orden de **cientos de milisegundos** si se detecta con BFD (ambas cifras son estimaciones de la documentación de OVS, no medidas aquí).

El vigilante seguiría existiendo: el grupo cubre el hueco inmediato y el vigilante deja la red en su estado definitivo después.

## 2. De dónde partimos (medido)

| Prueba | Corte de h1 hasta recuperar el servidor | Dónde se va el tiempo |
|---|---|---|
| 16-nov (V9), `estable=15` | ~17 s | 2 s de detección + 15 s de espera de estabilidad + <1 s de aplicación |
| 16-nov, `estable=5` | ~6 s | ídem con 5 s de espera |
| 22-nov, servicio vuelto a `estable=15` por una reaplicación | ~18 s | ídem |
| 22-nov, espera corta ante caída (`estable_caida=2`, `intervalo=2`) | **~3 s** | detección ≤2 s + 2 s de espera + <1 s de aplicación |

El suelo de este diseño es la suma de **consultar a ONOS + una espera de seguridad + instalar flujos por REST**. Se puede bajar un poco más (por ejemplo `intervalo=1`), pero no a milisegundos sin cambiar el mecanismo.

## 3. Cómo funciona un grupo fast-failover

Hoy una regla de la tabla 60 dice «para este destino, salir por el puerto 2». Con grupos:

- La regla dice «salir por el **grupo 7**».
- El grupo 7 (tipo `FAILOVER`) tiene varias opciones ordenadas, cada una con un `watch_port`: primero «puerto 2 (vigilado: 2)», después «puerto 3 (vigilado: 3)».
- El switch envía por la primera opción cuyo puerto vigilado esté vivo. Cuando el puerto 2 cae, el propio switch pasa a la opción 2 **en el mismo paquete**, sin intervención del controlador.

Detectar el fallo:
- **Fallo del puerto local** (la interfaz del propio switch cae): Open vSwitch lo ve al instante. Lo comprobamos el 22-nov: al bajar `ens4` en sw-acc-2, OVS pasó el puerto a `config: PORT_DOWN` / `state: LINK_DOWN` en el acto.
- **Fallo en el otro extremo** sin que el puerto local lo note (el caso habitual de un enlace virtual de VNRT o de un cable cuyo extremo remoto cae): hace falta un detector, **BFD** (Open vSwitch lo soporta por interfaz). **Por verificar:** que en esta versión (OVS 3.3.9) un puerto con BFD en estado `down` cuente como caído para `watch_port`; según la documentación de OVS debería, pero no lo hemos probado.

## 4. Cómo encaja en el código actual

| Pieza | Qué hace hoy | Qué habría que cambiar |
|---|---|---|
| `caminos.arbol_hacia` / `decisiones` | para cada switch y destino devuelve **un** siguiente salto (el más preferido entre los que están un salto más cerca) | `decisiones` ya expone los candidatos; haría falta una función nueva que calcule la **reserva sin lazos** (sección 5) |
| `capa3.flujos_capa3` | por prefijo, `PRIO_RUTA` (150) con `dec_ttl, output:<puerto>`; en los accesos `PRIO_SUBIDA` (120); entrega al host (`PRIO_ENTREGA` 160) | las reglas de ruta y subida referenciarían un grupo en vez de un puerto; la entrega al host y el ARP local no cambian |
| `flujos_proactivos.flujos_unicast` | un flujo `eth_dst → output` por (switch, host) | igual que arriba (modo capa 2) |
| `recalculo.diferencia` / `pasos_de_cambio` | compara flujos deseados e instalados; «romper antes de montar» | habría que comparar y gestionar **grupos** además de flujos (sección 7) |
| `onos_multi` | `instalar`, `borrar`, `flujos_de_app` por REST | habría que añadir `grupos_de_app`, `crear_grupo`, `borrar_grupo` |
| R5 (Sergio) | ya crea por REST un grupo `ALL` de espejo por core (`POST /groups/{device}`, `appCookie 0x52354d49` «R5MI»), esperando a que ONOS lo dé por `ADDED` | **ya sabemos que el REST de grupos funciona en esta slice**; hay que acordar un rango de `groupId` y un `appCookie` propios |

## 5. Qué camino de reserva usar y por qué no hay lazos

Sin STP un lazo es una tormenta, así que la reserva no puede ser cualquier vecino. Se usa el criterio de **alternativa libre de lazos** (LFA, RFC 5286): para un switch **S** que va hacia un destino **D**, un vecino **M** (distinto del siguiente salto principal) sirve de reserva si

`dist(M, D) < dist(M, S) + dist(S, D)`

es decir, el camino más corto de M hacia D **no pasa por S**, así que M nunca devolverá el paquete. Además, en la capa 3 solo pueden ser tránsito los switches del `nucleo` (`core-1, core-2, borde`; los accesos no llevan rutas completas), así que M debe ser del núcleo o el propio destino.

**Cálculo offline (22-nov)** con las funciones reales de `caminos.py` sobre los 7 cables de la slice, con destinos `acc-1`, `acc-2`, `core-1` (servicios) y `borde` (externa):

- **Todo origen y destino tiene al menos una reserva sin lazos.** Ejemplos: `acc-1 → core-1`: principal `core-1`, reserva `core-2`; `core-1 → acc-1`: principal `acc-1`, reserva `core-2`; `core-2 → core-1`: principal `core-1`, reserva `borde`.
- **Las 7 caídas de un solo cable quedan cubiertas** solo con la conmutación local: simulando cada cable caído y siguiendo, salto a salto, «principal si su puerto vive, si no la reserva», todos los orígenes llegan a todos los destinos sin lazos.
- **Dos cables caídos a la vez: 9 de las 21 parejas** dejan algún origen sin camino o con lazo solo con la conmutación local. Esas las seguiría resolviendo el vigilante (como hoy, ~3 s).
- **La caída de un switch** equivale, para sus vecinos, a la caída de los cables hacia él: se cubre igual, salvo el tráfico cuyo destino es ese mismo switch.

Este cálculo es un modelo del plano de datos, no una prueba en la red. Asume que cada switch reenvía por su principal salvo que el puerto esté caído y que los demás switches siguen con sus reglas originales.

## 6. Qué no cubre

- **El enlace de acceso de un host** (un solo cable al switch): no tiene alternativa.
- **La difusión y el ARP por árbol** (`flujos_difusion`): son salidas múltiples, no unicast; seguirían recuperándose con el vigilante. El ARP del gateway lo contesta el propio switch, así que el impacto es pequeño.
- **Fallos dobles** (9 de 21 parejas, arriba) y **cualquier fallo que no cambie el estado de un puerto sin BFD**.
- **Sesiones y medidores**: no cambian; las reglas de la tabla 0 y las tablas de rol siguen igual.

## 7. Puntos delicados del diseño

1. **Identificadores de grupo.** R5 ya crea grupos en los cores. Hay que reservar un rango de `groupId` y un `appCookie` para el reenvío y acordarlo con quien mantiene R5.
2. **Orden de instalación.** Un flujo que referencia un grupo solo se puede instalar cuando ONOS dé el grupo por `ADDED` (R5 ya espera eso). Al borrar, primero los flujos y después el grupo.
3. **El «romper antes de montar» del vigilante.** Hoy `_aplicar` borra primero lo que sobra y luego monta lo nuevo, lo que abre un hueco de milisegundos. Con grupos no tendría sentido volver a abrir un hueco justo después de haber evitado el corte: la reconvergencia posterior debería **modificar** grupos y flujos sin borrar antes. Es una decisión abierta.
4. **Pocos grupos, muchos flujos.** Un grupo se comparte entre todas las reglas que tengan la misma pareja (principal, reserva) en ese switch, así que el número de grupos por switch sería de unos pocos. Habría que contarlos con `benchmark_capa3.py` antes de afirmarlo.
5. **ONOS y lo que no conoce.** Ya vimos que ONOS purga meters creados con `ovs-ofctl`; probablemente pase igual con grupos. Los grupos de la propuesta se crearían por el REST de ONOS (como hace R5), nunca a mano en el switch.
6. **Reservas elegidas con las preferencias actuales.** Las reservas dependen del árbol principal. Si cambian los preferidos o cae un cable, se recalculan junto con los principales.

## 8. Plan de trabajo propuesto

| Fase | Qué se hace | Dónde se prueba |
|---|---|---|
| 0. Experimento de media hora | un grupo `FAILOVER` por REST de ONOS en **un** switch de acceso, con una regla en una tabla de prueba (la 62, como la prueba aislada `77`), y medir el corte bajando un puerto con un sondeo mucho más fino que el de `80` (cada 10 a 20 ms) | sw-acc-1, tabla aislada |
| 1. Offline | función de reservas (`caminos.alternativas`) con pruebas: cada cable caído, cada origen y destino, sin lazos ni callejones sin salida; ampliar el cálculo de la sección 5 a una prueba automática | pruebas de `R2/policy` |
| 2. Generador | variante de `flujos_capa3` con grupos, detrás de una opción (`--failover`, apagada por defecto) | pruebas |
| 3. Instalación y vigilante | `onos_multi` con grupos; `recalculo` compara grupos y flujos; decisión del punto 3 de la sección 7 | pruebas con el ONOS simulado |
| 4. En vivo, en la tabla aislada | un switch primero, luego todos, midiendo con el mismo `80` mejorado | tabla 62 (producción sin tocar) |
| 5. BFD | activar BFD en los troncales y comprobar que cuenta para `watch_port` | sw-acc-1 y sw-core-1 |
| 6. Producción | pasar a la tabla 60 con `95` antes y después y el freno `99` a mano | tabla 60 |

**Criterio de éxito.** Corte de h1 hacia el servidor de **≤100 ms** al caer el puerto local y **≤500 ms** con BFD (objetivos razonables, no medidos), ningún paquete en lazo, `95` limpio, sesiones y medidores intactos, y misma recuperación tras la vuelta del cable.

**Retroceso.** Apagar `--failover`: el vigilante reinstala flujos con puertos simples y borra los grupos; o `99_freno_emergencia.yml`.

**Esfuerzo.** Tamaño medio, de varias sesiones de trabajo. La parte incierta no es el código sino cómo se comportan los grupos en OVS 3.3.9 con ONOS 2.7, y eso lo resuelve la fase 0.

## 9. Cuándo compensa

Para una red de laboratorio con acceso a servicios web, ~3 s probablemente sea aceptable. El salto a milisegundos se justifica si hay tráfico sensible al corte (voz, vídeo, sesiones que no toleran perder paquetes) o si se quiere defender como mejora del diseño. Toca código de varias manos (generación de flujos, vigilante y la convivencia con los grupos de R5), por eso conviene decidirlo con el equipo antes de empezar.

## 10. Preguntas abiertas

- ¿Rango de `groupId` y `appCookie` para el reenvío? (acordar con quien lleva R5)
- ¿La reconvergencia posterior modifica en sitio o sigue «borrar y montar»?
- ¿BFD en todos los troncales o solo en los que no tengan detección local fiable?
- ¿Se aceptan los fallos dobles como «los resuelve el vigilante en ~3 s» (9 de 21 parejas)?
- ¿Se cubre también el tráfico de difusión o se deja como hoy?
