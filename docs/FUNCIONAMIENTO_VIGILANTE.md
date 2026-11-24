# El vigilante de la red: cómo funciona (documento autocontenido)

Este documento explica, sin suponer que se conoce el repositorio, qué es el **vigilante**, qué problema resuelve, cómo decide, cómo se despliega, qué se midió en la red real y qué límites tiene. Está escrito para pasarlo a otra conversación o a otra persona.

Proyecto: SDN del grupo 4 (TEL354, PUCP), slice de 14 VM, controlador **ONOS 2.7** en clúster de 3 nodos, switches **Open vSwitch 3.3.9** hablando OpenFlow 1.3. Estado descrito: 24-nov-2026.

---

## 1. Qué es, en una frase

Un servicio Python que cada pocos segundos **compara los flujos que deberían estar instalados en los switches con los que realmente hay**, y si la topología cambió (cable caído, switch caído, host nuevo, host que se fue) **repara la diferencia** de forma que nunca se forme un lazo ni una tormenta de difusión.

Es la pieza que hace que el **reenvío proactivo** sea tolerante a fallos: sin él, los flujos quedarían calculados para la topología del día de la instalación.

## 2. El problema de fondo

- La red física tiene **lazos de capa 2** (cada switch de acceso va a los dos cores, los cores entre sí, el borde a los dos cores: 7 cables entre 5 switches).
- Con `OUTPUT:NORMAL` (aprendizaje clásico) un lazo sin STP produce una tormenta de broadcast.
- **RSTP no sirve**: la red virtual (VNRT) descarta los BPDU (`01:80:c2:00:00:00`); se midió con una prueba que recibió 0 BPDU.
- Solución elegida (recomendación del profesor): **flujos proactivos explícitos**. Cada switch reenvía **solo con reglas unicast de salida explícita** (`output:<puerto>`), nunca con NORMAL, FLOOD ni ALL, y los puertos salen de **árboles** (un árbol no tiene ciclos, aunque el cableado sí).
- Pero un árbol calculado una vez no sobrevive a una caída. De ahí el vigilante.

## 3. Dónde encaja en el pipeline de los switches

Cada switch de acceso procesa cada paquete por tablas (resumen):

| Tabla | Quién la maneja | Qué hace |
|---|---|---|
| 0 | Policy Mapper (R1), apps de ONOS, instalador | Entrada: EAPOL y DHCP al controlador (prioridad alta), lista negra (45000), **flujo de sesión 802.1X** (40001: puerto + MAC → VLAN y tabla del rol), cuarentena por defecto (VLAN 99), LLDP al controlador |
| 10 / 20 / 40 / 45 / 50 | R2 (política por rol) | Tablas de **docente, alumno, soporte, superadmin e invitado**: una regla de prioridad 30000 por servicio permitido (IP y puerto TCP del servicio) y un **descarte final** (prioridad 0). Las reglas permitidas terminan en `goto_table 60` |
| 99 | R2 | Cuarentena (host sin sesión): solo lo mínimo, el resto se descarta |
| **60** | **El vigilante** | **Reenvío**: decide por qué puerto sale el paquete |
| 61 / 62 | Reservadas | Meters de R1 (61) y pruebas aisladas del vigilante (62) |

Importante: el vigilante **solo gestiona la tabla 60**. La autorización (qué servicios ve cada rol) la deciden las tablas de rol *antes*; el vigilante solo resuelve "por dónde sale lo que ya fue autorizado". Las tablas de política no cambian al cambiar el modo de reenvío.

Los flujos del vigilante se instalan por la REST de ONOS con la app `r2.proactivo`; los flujos de sesión del Policy Mapper se instalan con `org.onosproject.rest`. Por eso, al filtrar flujos por aplicación, son distintos.

## 4. Los dos modos de reenvío (y cuál está en producción)

### 4.1 Modo capa 2 (el original, ya apagado)
- Una regla `eth_dst=MAC → output:<puerto del árbol>` **por host y por switch** (S×H reglas).
- El ARP (difusión) recorre un **árbol de difusión por flujos** (raíz core-1), con salida explícita por los puertos del árbol y de acceso, nunca por el puerto de entrada.
- Desventaja: **no escala** (con 9 600 hosts serían ~1,9 millones de reglas y 9 801 en un solo switch).

### 4.2 Modo capa 3 por subredes (el de producción desde el 16-nov)
- **Una subred por switch de acceso** (plan A del slice: servicios `10.0.0.0/24` en core-1, acc-1 `10.0.1.0/24`, acc-2 `10.0.2.0/24`, exterior `203.0.113.0/24`, gateway `.254`).
- **Gateway virtual** por subred: una IP y **una MAC compartida** (`02:00:5E:00:00:FE`) que no existe en ningún equipo. Su ARP lo contesta el propio switch con una **app de ONOS** (Gateway ARP), no el vigilante.
- Dentro de la subred: ARP por inundación **local** (solo puertos de acceso del mismo switch, jamás una troncal) y reenvío por MAC.
- Entre subredes: el host manda el paquete a la MAC del gateway; los switches **enrutan por prefijo** (una regla por subred) siguiendo un árbol de caminos más cortos hacia el switch de la subred destino; el switch del destino reescribe MAC (origen = gateway, destino = host), decrementa el TTL y entrega con una regla /32 por host (solo en su propio switch).
- **No hay árbol de difusión**: nada que inunde sale de un switch.
- Escala: con 9 600 hosts, 78 802 reglas en total y 392 como máximo en un switch (frente a 1 949 404 y 9 801 en capa 2).

Reglas que genera (todas en la tabla 60):

| Prioridad | Dónde | Coincidencia | Acción |
|---|---|---|---|
| 300 | acceso | ARP petición por la IP del gateway | responde el switch (sin controlador) |
| 200 | acceso | ARP en difusión | sale por los demás puertos de acceso de esa subred |
| 160 | switch del host | `eth_dst=gateway` + `ipv4_dst=host/32` | reescribe MAC, `dec_ttl`, puerto del host |
| 150 | todos | `eth_dst=gateway` + `ipv4_dst=prefijo` | `dec_ttl`, puerto del árbol hacia esa subred |
| 130 | núcleo | prefijos privados (10/8, 172.16/12, 192.168/16) | descarte (una IP interna inexistente nunca sale al exterior) |
| 120 | acceso | entrada por puerto de acceso + `eth_dst=gateway` | `dec_ttl`, hacia la raíz (exige entrar por un puerto de acceso: lo que llega por troncal sin destino local se descarta y no rebota) |
| 110 | borde y demás núcleo | IPv4 al gateway sin destino concreto | ruta por defecto hacia el siguiente salto exterior |
| 100 | switch del host | `eth_dst=MAC` | puerto del host (dentro de la subred) |
| 1 | todos | — | descarte |

Garantías verificadas con pruebas: ningún flujo usa NORMAL/FLOOD/ALL; ningún ARP cruza una troncal; la entrega funciona para todas las parejas de hosts con TTL y MAC finales correctos.

**Producción actual:** el vigilante L2 está parado y deshabilitado; corre el de **capa 3** (servicio `vigilante-capa3`). Nunca deben aplicar los dos a la vez.

## 5. Cómo decide: el ciclo

El vigilante **no reacciona a eventos**; en cada ciclo (cada **2 s** en producción) hace esto:

1. **Lee ONOS**: `GET /devices`, `GET /links`, `GET /hosts` (con conmutación entre nodos, ver 7).
2. **Actualiza su memoria**: puertos que alguna vez fueron troncales, hosts vistos, hosts fijos.
3. **Construye el grafo vivo**: solo switches *disponibles* y cables `ACTIVE` **en los dos sentidos** (un cable que va en un solo sentido no sirve).
4. **Elige la raíz**: core-1; si no está viva, core-2 (orden de `PREFERIDOS`).
5. **Comprueba condiciones de no actuar** (sección 6).
6. **Calcula el estado deseado**: los flujos que DEBERÍA haber en la tabla 60 con esa topología (árboles de caminos más cortos hacia cada destino, desempate: core-1 antes que core-2).
7. **Lee el estado real**: `GET /flows` de la aplicación del vigilante (de cada switch, preguntando a su nodo maestro).
8. **Calcula la diferencia**: qué sobra, qué falta, qué cambió.
9. **Espera estabilidad**: la diferencia debe mantenerse igual durante un tiempo antes de aplicarla (distinto para caídas y subidas).
10. **Aplica "romper antes de montar"**: primero borra lo que sobra o cambia, después añade lo nuevo.
11. Registra el evento en el historial (lo que ve el panel) y en el log.

Como compara *deseado contra instalado*, sirve igual para un cable caído, un switch caído, un host nuevo o un flujo borrado a mano.

### 5.1 Por qué "romper antes de montar" no forma lazos
- Mientras se **borra**: cada switch tiene flujos del árbol viejo o ninguno, así que el reenvío queda dentro del árbol viejo (sin ciclos).
- Mientras se **añade**: los switches sin cambios y los ya actualizados reenvían solo por aristas del árbol nuevo (sin ciclos).
- Un switch que no cambia tiene los mismos puertos en el árbol viejo y en el nuevo, así que nunca introduce una arista ajena a ambos.
- Verificado con un simulador sobre cada cable, cada switch y cada par de cables caídos, en cientos de estados intermedios al azar: ningún switch recibe el broadcast dos veces. En capa 3 se simularon todos los estados intermedios de la caída de cada cable.

### 5.2 Altas rápidas, caídas y subidas
- **Alta rápida:** si la diferencia son solo altas a nivel de host (host nuevo con sus reglas /32, prioridades 100 y 160), se aplica sin esperar.
- **Caída** (entre dos ciclos se perdió un cable o un switch: lo que lo usaba ya no funciona): se actúa tras `estable_caida` (2 s) y respetando solo `min_intervalo_caida` (10 s).
- **Subida** (vuelve un cable: los caminos actuales siguen funcionando, no hay urgencia): se mantiene la espera larga `estable` (15 s) y `min_intervalo` (60 s), que es lo que protege contra un cable que parpadea.

## 6. Cuándo NO actúa (protecciones)

Escribe el motivo en el log una sola vez y no toca nada si:
- Existe el archivo interruptor `/etc/vigilante/desactivar` (**pausa manual**).
- Ningún ONOS responde, o ONOS lista switches pero **ningún enlace** (probablemente reiniciando: no es una caída de la red).
- La **red está partida** (algún switch vivo no llega a la raíz).
- La diferencia aún no lleva `estable` segundos igual, o ya hubo un cambio hace menos de `min_intervalo`.
- El cambio supera **`max_cambios` = 200 flujos** (señal de datos incompletos).
- Se arrancó sin `--aplicar` (modo "solo avisar": calcula y escribe lo que haría).
- Es el respaldo y el principal está sano.

Más salvaguardas de diseño:
- **Puertos de acceso en capa 2:** salen de un archivo fijo generado una vez con los 14 enlaces vivos; todo puerto que alguna vez salió en un enlace (activo o no) se trata siempre como troncal. Un cable caído nunca convierte sus puertos en "de acceso".
- **Red de monitoreo fuera de la difusión:** los puertos hacia los sensores de seguridad (R5) se excluyen de la difusión; solo reciben la copia del espejo.
- **Destino desconocido:** se descarta, nunca se inunda. El broadcast que no es ARP/DHCP (mDNS, NetBIOS…) queda descartado: es el precio de eliminar la inundación.

## 7. Cómo habla con ONOS (clúster de 3 nodos)

- Usa la REST de ONOS. Orden de preferencia: node-ctrl-1, luego .2, luego .3; se queda con el que respondió y reintenta el preferido cada minuto. Un 4xx es respuesta real; un 5xx, tiempo agotado o conexión rechazada hacen pasar al siguiente nodo.
- **Lectura de flujos de un switch al nodo maestro de ese switch** (`GET /mastership/{switch}/master`, recordado 30 s): leerlos por un nodo no maestro obliga a ONOS a resolverlos con mensajes entre nodos y se vio que se atascaba (timeouts de 15 s). Si el maestro no se sabe, se sigue como siempre.
- **Vista mala de una réplica:** si el nodo en uso dice "0 switches disponibles" (lo que pasó tras reiniciar controladores: una réplica en espera no recupera el estado "disponible"), el vigilante pregunta a los demás nodos y usa el primero que sí ve alguno. Un solo switch disponible basta para fiarse de un nodo. Antes de ese arreglo concluía "red partida" y dejaba de recalcular.

## 8. Hosts: de dónde salen y cuándo se olvidan

- ONOS entrega los hosts (MAC, IP, ubicación). En capa 3 la IP llega gracias a la **app DHCP por subred** (`org.campus.dhcp`), que al confirmar una concesión anota el host en ONOS; así `/hosts` trae IP y el vigilante crea la regla /32.
- **Hosts fijos** (archivo `hosts_fijos_capa3.json`): equipos que ONOS no aprende solo (el server, etc.). Mandan sobre lo que diga ONOS y **nunca se olvidan**.
- Un host que ONOS deja de listar **no se olvida de inmediato** (ONOS puede perder hosts un momento, por ejemplo al reiniciar). Se olvida solo si:
  1. En capa 3, otro host que ONOS sí lista reclama la misma IP (evita dos reglas /32 iguales para MACs distintas), o
  2. Lleva `olvidar_host_s` (900 s = 15 min en producción) sin aparecer.
- Las reglas del host olvidado pasan a "sobran" y se retiran con la espera normal.

## 9. Salida al exterior (ruta por defecto)

El plan puede incluir `salida: {dispositivo, puerto, mac, bloquear}`: el **siguiente salto** hacia fuera (en el laboratorio, la VM del atacante en el puerto 1 del borde). Con él se generan la ruta por defecto (110) y el descarte de privados (130). Una IP externa nueva no necesita ninguna regla. **No hay NAT** (decisión del 23-nov). La política de roles sigue decidiendo qué puede salir. Probado en vivo: desde el atacante, `nc` al server dio `Connection refused` (el rechazo vuelve por la ruta nueva; antes daba `timed out`).

## 10. Alta disponibilidad: principal y respaldo

- **Principal:** node-ctrl-3. **Respaldo:** node-ctrl-2.
- El principal publica `GET :8098/salud` (capa 3; el vigilante L2 usaba `:8099`) con la hora de su último ciclo.
- El respaldo lo consulta en cada ciclo y **solo actúa si el principal lleva 45 s sin responder** (o con un último ciclo de más de 30 s). En cuanto el principal vuelve, el respaldo **cede** (≤5 s). Como el principal necesita 15 s estables antes de aplicar, no llegan a actuar a la vez; así no se entrelazan sus cambios y se conserva la garantía de "borrar antes de montar".
- Límite: una partición de red entre ctrl-2 y ctrl-3 con ambos alcanzando ONOS haría que actuaran los dos. Como calculan el mismo estado deseado y las operaciones son idempotentes, el riesgo es bajo pero no nulo.
- Probado en vivo (16-nov): parado el principal, ctrl-2 asumió el mando a los 42 s, aplicó un cable caído en 15 s con ARP funcionando y cedió 5 s después de volver el principal.

## 11. Despliegue y operación

Servicio `systemd` **`vigilante-capa3`**, instalado con el playbook `68_vigilante_capa3_servicio.yml` (el de capa 2 es `65_vigilante.yml`, hoy parado). Código en `/opt/vigilante`, configuración en `/etc/vigilante` (`plan_capa3.json`, `hosts_fijos_capa3.json`; en L2 además `acceso.json`).

Parámetros por defecto del playbook (en producción):

| Parámetro | Valor | Significado |
|---|---|---|
| `intervalo` | 2 s | cada cuánto consulta a ONOS (el programa usa 5 si va vacío) |
| `estable` | 15 s | tiempo que debe durar una diferencia antes de actuar (subidas y cambios en general) |
| `min_intervalo` | 60 s | mínimo entre dos cambios (acota el vaivén ante un cable inestable) |
| `estable_caida` | 2 s | espera cuando el cambio viene de una caída |
| `min_intervalo_caida` | 10 s | intervalo mínimo tras una caída |
| `olvidar_host` | 900 s | tiempo sin aparecer en ONOS tras el cual se olvida un host |
| `max_cambios` | 200 flujos | tope de seguridad |
| `nucleo` | core-1, core-2, borde | switches con rutas completas / que pueden hacer tránsito |
| respaldo | 45 s | plazo para asumir el mando |

Importante: cualquier cambio de estos valores debe quedar en los **valores por defecto del playbook**. Una vez se perdió un ajuste (`estable=5`) porque solo se pasó con `-e` y se volvió a 15 s al reaplicar.

Comandos útiles (desde el nodo de despliegue, carpeta de los playbooks):

```bash
PLAYBOOK=68_vigilante_capa3_servicio.yml ./lanzar.sh                       # instala/arranca en modo SOLO AVISAR
PLAYBOOK=68_vigilante_capa3_servicio.yml ./lanzar.sh -e aplicar=true       # aplica en la tabla 60
PLAYBOOK=68_vigilante_capa3_servicio.yml ./lanzar.sh -e quitar_aplicar=true # vuelve a solo avisar
PLAYBOOK=68_vigilante_capa3_servicio.yml ./lanzar.sh -e accion=parar       # parar y deshabilitar
```

En el controlador donde corre:

```bash
journalctl -u vigilante-capa3 -f                    # ver lo que decide
curl -s http://192.168.201.3:8098/salud             # estado breve (último ciclo, "sin cambios", "partida"...)
sudo touch /etc/vigilante/desactivar                # PAUSAR (no hace nada hasta borrar el archivo)
sudo rm /etc/vigilante/desactivar                   # reanudar
```

Seguridad al operar: antes y después de cualquier cambio en vivo se corre el **diagnóstico de lazos** (`95_diagnostico_lazos.yml`: cuenta flujos con NORMAL/FLOOD/ALL, mide paquetes por segundo por puerto y da el veredicto "sin tormenta"); existe un **freno de emergencia** (`99_freno_emergencia.yml`). Nunca ejecutar `65` con aplicar ni `60/70` con reiniciar sin pensar: restablecen la capa 2.

## 12. Cómo se ve en el panel

El panel de administración tiene la pestaña **Vigilante**: la topología dibujada (núcleo arriba, accesos abajo, borde a un lado, posiciones fijas) con el color de cada cable: **verde** = algún árbol de rutas lo usa; **gris discontinuo** = vivo pero de reserva (la redundancia que tomaría el relevo); **rojo** = caído. El doble borde marca la raíz. Muestra también qué vigilante manda (por ejemplo `ctrl3 · principal · activo · estado: sin cambios · capa3 · aplica cambios`) y el **historial de recálculos** (cable o switch caído o restaurado, cambio detectado con flujos por switch, aplicado, alta rápida, red partida y recuperada).

La fuente es `GET :8098/estado` del vigilante que manda. No incluye hosts, MAC ni IP de nadie, solo la topología de switches. El panel lee de una caché de 10 s; el vigilante registra el historial sin que eso afecte sus decisiones.

## 13. Lo medido en la red real

| Prueba | Resultado |
|---|---|
| Cable caído en L2 (15-nov) | detectado y aplicado en ~16 s, ARP sigue funcionando, sin tormenta; al volver el cable recalcula |
| Switch core-2 caído (OVS parado) | solo core-1 cambia (−5 +4); ONOS reinstala sus flujos al volver |
| Switch core-1 (la raíz) caído | la raíz pasa a core-2; el cambio previsto offline coincidió exactamente con el real; aplicado en 15 s; al volver, la raíz regresa a core-1 |
| Respaldo (16-nov) | asume a los 42 s, cede 5 s después de volver el principal |
| Conmutación a capa 3 (16-nov) | tabla 60: de 42 a 27 flujos; docente `refused` y alumno `timed out` en el servicio de notas con sesión 802.1X; diagnóstico de lazos limpio |
| Cable caído en capa 3 con `estable=15` | **~17 s** de corte (2 detección + 15 estabilidad + <1 aplicación); la vuelta del cable no cortó nada |
| Con `estable=5` | **~6 s** |
| Con espera corta ante caídas (`estable_caida=2`, `intervalo=2`; 22-nov) | **~3 s** (2 pruebas fallidas de 144); la vuelta esperó 15 s y no cortó nada |
| Parpadeo de 1 s | no cambia nada (exige estabilidad) |
| Olvido de host (23-nov) | a los 61 s sin aparecer, retira sus reglas (`APLICADO: acc-1: -2 +0`) sin reiniciar el servicio |
| Ruta por defecto (23-nov) | desde el atacante el server responde `Connection refused`; diagnóstico de lazos "sin tormenta" |

## 14. Incidentes y lecciones

1. **Atasco de ONOS al leer flujos por un nodo no maestro (23-nov):** lecturas de 11 a 15 s o errores 500; se cambió a leer por el maestro de cada switch. El atasco **intermitente** de ONOS no desapareció del todo (también ocurre con el vigilante pausado); la causa raíz no se averiguó.
2. **Vista mala de réplicas tras reinicios (23-nov):** los cinco switches soltaron a la vez la conexión con un ONOS ("no response to inactivity probe"); la réplica en espera quedaba con los switches "no disponibles" y el vigilante concluía "red partida". Arreglo en el vigilante (usar otro nodo) y reparación manual: **dar el mando de un switch a otro nodo y devolverlo**. Un detector de solo lectura avisa de las vistas desiguales; la reparación automática no está hecha.
3. **Ajuste perdido (22-nov):** `estable=5` solo se había pasado con `-e`; hay que dejarlo en los valores por defecto del playbook.
4. **Host que no se olvidaba:** una regla /32 de un host que ya no existía siguió instalada; se añadió el olvido por IP reclamada y por tiempo.
5. **Falso positivo inicial:** ONOS devolvía el descarte con `NOACTION`; se corrigió la comparación. También se normaliza la MAC a mayúsculas porque ONOS puede devolverla en minúsculas.
6. **"Dispositivo no disponible en ONOS" no equivale a plano de datos caído:** con `fail-mode secure` un switch sin controlador conserva sus flujos; los cinco switches están en ese modo (no hay riesgo de tormenta si pierden a todos los controladores).
7. **ICMP no pasa:** R2 solo instala reglas TCP por servicio, así que un `ping` al servidor se descarta aunque el servicio esté activo. Para verificar conectividad con sesión se usa `nc` a un puerto (docente `refused` = llega; alumno `timed out` = bloqueado), no `ping`.

## 15. Limitaciones conocidas

- Si la red queda partida, no hace nada (protección deliberada).
- El corte ante una caída de cable es de **~3 s** (detección + espera corta + aplicación); no es de milisegundos.
- Un parpadeo más corto que `estable_caida` no cambia nada; uno más largo provoca un cambio de ida (a los 2 s) y otro de vuelta (tras 15 s y el mínimo entre cambios).
- Si el vigilante arranca con un cable ya caído no sabe que algo se perdió.
- Los flujos de la tabla 0 los gestiona ONOS/otros componentes, no el vigilante.
- **Capa 2:** el broadcast sigue un único árbol compartido (raíz core-1) y puede dar más saltos que el unicast. En **capa 3** no hay árbol de difusión.
- Una partición entre los dos vigilantes con ambos alcanzando ONOS podría hacerlos actuar a la vez.
- La MAC del siguiente salto exterior es un dato fijo del plan (el vigilante no la aprende).
- La reparación automática de "vistas desiguales" de ONOS no existe.

## 16. Alternativas consideradas y propuestas

- **Intents y VPLS de ONOS:** es la misma arquitectura (unicast = un árbol hacia cada destino; broadcast = árboles sin ciclos) con otro dueño del cálculo. Se eligieron flujos propios por: garantía demostrable anti-lazos (simulador), no romper el pipeline de política de R2 (prioridad por defecto de un intent = 100, bajo las sesiones), la recomendación del profesor y la escalabilidad por subredes. No se probó cómo resuelve ONOS dos árboles por origen en un mismo switch.
- **Fast-failover (propuesta escrita, sin implementar):** grupos OpenFlow `FAILOVER` con `watch_port` para que el propio switch conmute en milisegundos al camino de reserva. Cálculo offline: con la restricción de tránsito (`core-1, core-2, borde`) todo origen/destino tiene reserva sin lazos (criterio LFA, RFC 5286) y las 7 caídas de un cable quedan cubiertas solo con conmutación local; 9 de 21 parejas de cables caídos a la vez no, y las seguiría resolviendo el vigilante. Decisiones abiertas: rango de `groupId`/`appCookie` (R5 ya crea grupos ALL con cookie `0x52354d49`), si la reconvergencia modifica en sitio o sigue "romper antes de montar", BFD en qué troncales (por verificar que cuente para `watch_port` en OVS 3.3.9). La fase 0 sería un experimento de media hora en un switch de acceso con la tabla aislada 62.

## 17. Código y pruebas

Carpeta `R2/policy`: `vigilante.py` (el servicio, ciclo, protecciones, respaldo, `/salud` y `/estado`), `recalculo.py` (funciones puras: grafo vivo, estado deseado, diferencias, "romper antes de montar"), `caminos.py` (grafo y árboles de caminos más cortos), `capa3.py` (plan de subredes y generación de flujos de capa 3), `flujos_proactivos.py` (generadores de capa 2), `estado.py` (lo que se publica al panel), `onos_multi.py` (clúster de ONOS: nodos, maestro, vista mala), `hosts_fijos.py`, `plan_arboles.py`, `explicar_ruta.py` (explica la ruta de un paquete y la compara con `/paths` de ONOS), `vista_previa.py`. Más de 400 pruebas en `R2/policy` (simulaciones de caídas de cada cable y switch, estabilidad, olvido de hosts, maestro y vista mala de ONOS, ruta por defecto con los datos reales de 14 enlaces).

## 18. Glosario rápido

- **Tabla 60:** tabla de reenvío del pipeline; la única que gestiona el vigilante.
- **Romper antes de montar:** borrar primero lo que sobra o cambia y luego instalar lo nuevo, para no crear lazos intermedios.
- **Estado deseado / instalado:** lo que debería haber según la topología viva / lo que realmente hay en los switches.
- **Árbol de caminos más cortos hacia un destino:** conjunto de puertos de salida de cada switch hacia ese destino; sin ciclos por construcción.
- **Núcleo:** core-1, core-2 y borde, los switches que pueden hacer tránsito (los accesos no).
- **Gateway virtual:** IP y MAC compartidas que contesta el switch de acceso; no existe en ningún equipo.
- **Principal / respaldo:** node-ctrl-3 / node-ctrl-2.
- **Interruptor:** archivo `/etc/vigilante/desactivar` que pausa al vigilante.
- **Maestro de un switch:** el nodo de ONOS que lo controla.
