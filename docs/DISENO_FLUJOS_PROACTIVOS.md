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
| Flujos unicast por host | Para cada host (MAC/IP conocida por `/hosts`), en cada switch: `eth_dst=MAC → output:<puerto del árbol>`; en el switch del host, el puerto de acceso | pendiente |
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

## Puntos por verificar en el entorno real
- Que ONOS descubra los `links` (LLDP) a través de VNRT; LLDP usa `01:80:c2:00:00:0e`, del mismo rango reservado que EAPOL/BPDU, y puede estar filtrado. Si `links` sale vacío, el árbol se construiría desde la topología declarada en un archivo.
- EAPOL (recomendación 1, VXLAN) sigue pendiente aparte.

## Siguiente incremento
Generador `flujos_unicast(hosts, arboles)` en `policy_tables.py` bajo la bandera, con prueba del invariante sobre la topología de 14 VM; después, ARP desde el controlador.
