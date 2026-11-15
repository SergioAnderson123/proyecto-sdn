# Reenvío proactivo sin NORMAL ni STP (recomendación 2 del profesor)

**Estado:** diseño + primer módulo (`R2/policy/caminos.py`, con pruebas). **Nada de esto toca aún los switches ni el Policy Mapper.**

## Problema
La fabric de 14 VM tiene lazos de capa 2 (cada acceso a los dos cores, cores entre sí, borde a los dos cores). Hoy R2 reenvía con `OUTPUT:NORMAL`,
que inunda broadcast/desconocidos; con lazos y sin STP eso es una tormenta. RSTP no sirve: VNRT descarta los BPDU (`01:80:c2:00:00:00`),
medido con `96_prueba_rstp.yml` (0 BPDU entrantes).

## Idea
Un árbol no tiene ciclos. Si cada switch reenvía **solo con flujos unicast de salida explícita** (`output:<puerto>`) y esos puertos salen de un
árbol hacia cada destino, no importa que el cableado tenga lazos: ningún paquete se duplica. Sin NORMAL, FLOOD ni ALL en ningún flujo.

## Piezas
| Pieza | Qué hace | Estado |
|---|---|---|
| `caminos.py` | Grafo desde `GET /onos/v1/links`; árbol de caminos más cortos hacia un destino (desempate: core-1 antes que core-2); `es_arbol`; `flujos_que_inundan` | **hecho + 10 pruebas** |
| Flujos unicast por host | Para cada host (MAC/IP conocida por `/hosts`), en cada switch: `eth_dst=MAC → output:<puerto del árbol>`; en el switch del host, el puerto de acceso (`flujos_unicast`, + descarte explícito) | **hecho + pruebas** |
| ARP (difusión) | **Árbol de difusión por flujos** (`flujos_difusion`): por switch y puerto de entrada, salida explícita por los puertos del árbol y de acceso, nunca por el de entrada ni por enlaces fuera del árbol. Un puerto con enlace jamás se trata como de acceso. 5 pruebas con simulador (llega 1 vez a cada switch, sobrevive a la caída de un enlace) | **hecho + pruebas** |
| ARP (respuesta) | Unicast: la cubren los flujos por destino de la tabla 60 | hecho (mismo generador) |
| DHCP | Ya va al controlador (prio 40500); la respuesta se devuelve con `packet-out` al puerto del cliente | pendiente |
| Servicios fijos | 10.0.0.40/.50/.60 con su MAC y puerto conocidos → flujos estáticos | pendiente |
| Retorno por sesión | Al crear la sesión, el Policy Mapper instala `eth_dst=MAC del cliente → output` en el camino de vuelta | pendiente |
| Cuarentena (VLAN 99) | Hoy `table 99` usa NORMAL; pasa a salidas explícitas al portal | pendiente |
| Recuperación de fallos | Al cambiar `/links`, recalcular árbol y reinstalar solo los flujos que cambian | pendiente |

## Reglas de seguridad (no negociables)
1. **Invariante en pruebas:** todo flujo generado en modo proactivo debe pasar `flujos_que_inundan(...) == []`.
2. **Bandera de política `reenvio: normal | proactivo`, por defecto `normal`**, para no romper el laboratorio Mininet (R2/R5, sin lazos). El modo proactivo se activa solo en `topologia14`.
3. Antes de activar en vivo: ejecutar `95_diagnostico_lazos.yml`, activar primero en **un** switch, y tener a mano `99_freno_emergencia.yml`.
4. Un destino desconocido se **descarta** (o va al controlador), nunca se inunda.
5. El broadcast que no es ARP/DHCP (p. ej. mDNS, NetBIOS) queda descartado: es el precio de eliminar la inundación.

## Hallazgos del entorno real (14-nov-2026)
- `links` de ONOS muestra los 7 cables en ACTIVE: LLDP atraviesa VNRT. Los árboles se calculan bien con datos reales (`plan_arboles.py`).
- Los cores no tienen NORMAL libre en la tabla 0; su ARP va solo al controlador, y eso evita hoy la tormenta.
- `org.onosproject.proxyarp` (ONOS 2.7) **no respondió ni inundó** ARP: el contador de la regla ARP→CONTROLLER subía, pero no hubo ningún `Reply` ni `Request` saliente (tcpdump en `sw-acc-2` y en el sensor). Por código inunda solo por edge points, pero no se pudo hacer funcionar. Se descarta y se resuelve con el árbol de difusión por flujos.
- Tras las pruebas de failover los 5 switches quedaron con maestro ctrl-2; se devolvieron a ctrl-1 con `--tags maestro`.

- **Red de monitoreo fuera de la difusión (15-nov-2026):** los sensores (node-sec-1/2) viven en una red de monitoreo y solo reciben la copia del espejo de R5. Los puertos que van a ellos (ens4 de core-1 y core-2, ens8/ens9 del borde) se excluyen de la difusión ARP (`MONITOREO` en `instalar_proactivo.py`). Quedan como puertos de acceso solo hosts y server.

## Recálculo ante caídas de enlaces o switches (vigilante)
Vive en `node-ctrl-3` como servicio `systemd` (`R1/deploy/topologia14/65_vigilante.yml`) y trabaja por la API REST de ONOS; ONOS empuja los flujos a los switches por la red de gestión.
- **Qué compara:** cada ciclo (5 s) calcula los flujos que DEBERÍA haber en la tabla 60 con la topología viva (`/devices` disponibles, `/links` ACTIVE en los dos sentidos) y los compara con los que HAY (`GET /flows`). Sirve igual para un cable caído, un switch caído o un host nuevo.
- **Qué nodos usa:** primero `node-ctrl-1` (maestro de los switches) y, si no responde, `.2` y `.3`. Se queda con el que respondió y reintenta el preferido cada minuto. Un 4xx es una respuesta real; un 5xx, un tiempo agotado o una conexión rechazada hacen pasar al siguiente.
- **Cambio sin lazos ("romper antes de montar"):** primero se BORRAN los flujos que sobran o cambian y después se AÑADEN los nuevos. Mientras se borra, cada switch tiene flujos del árbol viejo o ninguno (reenvío dentro del árbol viejo); mientras se añade, los switches sin cambios y los ya actualizados reenvían solo por aristas del árbol nuevo. Probado con un simulador sobre cada cable, cada switch y cada par de cables caídos, en cientos de estados intermedios al azar: ningún switch recibe el broadcast dos veces.
- **Raíz:** core-1; si cae, core-2 (orden de `PREFERIDOS`).
- **Cuándo NO actúa:** interruptor (`/etc/vigilante/desactivar`); ningún ONOS responde o `/links` viene vacío con switches vivos (ONOS reiniciando, no una caída); la red queda partida; el cambio no lleva 15 s estable; hubo otro cambio hace menos de 60 s; el cambio supera 200 flujos (datos incompletos); sin `--aplicar` solo escribe lo que haría.
- **Puertos de acceso:** salen de un archivo fijo (`acceso.json`) generado UNA VEZ con los 14 enlaces vivos. Además, todo puerto que alguna vez salió en un enlace (activo o no) se trata siempre como troncal: un cable caído nunca convierte sus puertos en "de acceso".
- **Hosts:** un host que ONOS deja de listar no se olvida (ONOS puede tardar en volver a listarlos tras reiniciar).
- **Compromiso asumido: broadcast más largo que el unicast.** El unicast sigue el camino más corto hacia cada destino (un árbol por destino) y puede usar los cables que están bloqueados SOLO para difusión; el broadcast sigue un único árbol compartido con raíz en core-1. Con el cable acc-2↔core-1 caído, de acc-2 a host-jhon el unicast va acc-2 → core-2 → borde (2 saltos), pero la petición ARP en broadcast da la vuelta acc-2 → core-2 → core-1 → borde (3 saltos). Afecta solo al descubrimiento de la MAC, no a los datos. Un árbol por origen no es posible sin distinguir el origen en los switches intermedios (una regla por host o etiquetado), lo contrario de lo que se busca para escalar.
- **Respaldo (node-ctrl-2):** el principal (node-ctrl-3) publica `GET :8099/salud` con la hora de su último ciclo; el respaldo lo consulta en cada ciclo y solo actúa si el principal lleva 45 s sin responder (o con un último ciclo de más de 30 s). En cuanto el principal vuelve, el respaldo cede (≤5 s), y como el principal necesita 15 s estables antes de aplicar nada, no llegan a actuar a la vez. Se evita así que dos instancias entrelacen sus cambios y rompan la garantía de "borrar antes de montar". Límite: una partición de red entre ctrl-2 y ctrl-3 con ambos alcanzando a ONOS haría que los dos actuaran; como ambos calculan el mismo estado deseado y las operaciones son idempotentes, el riesgo es bajo pero no nulo.
- **Probado en vivo (15-nov-2026):** cable acc-2↔core-1 caído (cambio aplicado en ~16 s, ARP funcionando, sin tormenta) y switch sw-core-2 caído (OVS parado: ONOS lo da por no disponible, solo core-1 cambia, -5 +4; al volver ONOS le reinstaló sus flujos, incluida la regla 40003 de la tabla 0, y el vigilante restauró core-1).
- **Limitaciones:** si la red queda partida no hace nada; los flujos de la tabla 0 (fase C) no los gestiona (en la prueba del switch ONOS los restauró solo); "switch no disponible en ONOS" no equivale a plano de datos caído (con fail-mode secure un switch sin controlador conserva sus flujos).

## Puntos por verificar en el entorno real
- (resuelto) ONOS sí descubre los 7 enlaces por LLDP a través de VNRT.
- Hay que ejecutar `vista_previa.py` con `edge_ports.txt` real (salida de `edge-ports`) antes de instalar nada.
- EAPOL (recomendación 1, VXLAN) sigue pendiente aparte.

## Siguiente incremento
Cablear los generadores a `policy_tables.py` bajo la bandera `reenvio: proactivo`: las tablas de rol hacen `goto_table 60` en vez de `OUTPUT NORMAL`, y el ARP de la tabla 0 pasa a `goto_table 60`. Primero solo en `sw-acc-1`, con diagnóstico 95 antes y después. El DHCP sigue yendo al controlador.
