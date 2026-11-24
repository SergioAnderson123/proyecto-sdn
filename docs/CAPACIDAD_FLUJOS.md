# Capacidad de flujos: cuántos roles y servicios caben y cuándo crecen los flujos (24-nov-2026)

**Supuesto principal:** el tope de **8000 flujos es por switch** (lo que cabe en la tabla de flujos de un switch). Si fuera para toda la red, los números de abajo se reparten entre los switches. Las fórmulas salen del código (`policy_tables.py`, `capa3.py`) y las comprueba `R2/policy/tests/test_capacidad_flujos.py`; lo medido en la red real (acc-1: 58 flujos) cuadra con ellas.

## 1. Qué hay en un switch de acceso hoy (medido en acc-1, 24-nov)

| Qué | Flujos | De qué depende |
|---|---|---|
| Tablas de rol (10, 20, 40, 50) | **30** | roles y servicios permitidos (sección 2) |
| Resto de R2 (descubrimiento, DHCP, cuarentena, portal) | 9 | fijo |
| Vigilante, medidores, ARP del gateway, DHCP, MAB | 11 | por puerto y por host |
| Núcleo de ONOS y su REST | 8 | fijo |
| **Total** | **58** | |

## 2. Fórmulas (por switch)

```
flujos de roles   = Σ por rol (3 + servicios que ese rol puede alcanzar)      # ARP + portal + descarte final + 1 por servicio permitido
flujos de un acceso ≈ 20 (fijos) + 6 × puertos de acceso + 3 × hosts + flujos de roles
```

- **Por host: 3.** 1 flujo de sesión (R2, tabla 0) + 2 de capa 3 (MAC y /32), solo en el switch donde está el host.
- **Por puerto de acceso: ≈ 6.** 2 del vigilante (medido), 2 de medidores y 2 del ARP del gateway (estos cuatro, deducidos de acc-1 con **un solo** puerto: faltan por comprobar con más puertos).
- **Lo que NO cuesta flujos:** un servicio **denegado**, un servicio **sin regla** para ese rol o un servicio **sin host** en el catálogo (como `internet`). El descarte final ya cubre todo lo demás. Medido: 0.

## 3. Máximo de roles y servicios con 8000 flujos por switch

Un acceso grande (48 puertos, 48 hosts) gasta ≈ 450 en lo demás, quedan **≈ 7500 para las tablas de rol**: `R × (3 + S) ≤ 7500` si todos los roles alcanzan todos los servicios.

| Roles | Servicios que cada rol puede alcanzar (máximo) |
|---|---|
| 4 (hoy) | 1872 |
| 10 | 747 |
| 20 | 372 |
| 30 | 247 |
| 40 | 184 |
| 50 | 147 |
| **58** | **126** |

La cuenta real es de **pares permitidos (rol, servicio)**: `Σ (3 + P_rol) ≤ 7500`. El catálogo puede ser mucho mayor que lo que cada rol alcanza: con 58 roles hay hasta ≈ 7300 pares permitidos en total (por ejemplo, 58 roles × 126 servicios, o un catálogo de 500 servicios con cada rol alcanzando 126).

### El límite que llega antes que los 8000: 58 roles

Cada rol necesita **su propia tabla**, y la tabla de un rol debe ser **menor que 60** (la tabla de reenvío), porque en OpenFlow `goto_table` solo va hacia delante. Quedan las tablas 1 a 59 menos la 30 (reservada a IoT/MAB): **58 roles como máximo**, sea cual sea el número de flujos. Pasar de ahí exige mover la tabla de reenvío a un número mayor (cambio de código en varios módulos). Además, el rol es también la VLAN de la sesión.

**Cuidado con el hardware:** las tablas de rol son tablas OpenFlow distintas. Open vSwitch soporta 254; muchos switches físicos soportan pocas (a menudo entre 1 y 8) o comparten los 8000 entre todas. Si el tope de 8k viene de un switch de hardware, el límite real puede ser el número de tablas y no el de flujos.

## 4. Servidores o servicios

Un servicio es una pareja **IP + puerto TCP**. Un servidor con tres puertos son tres servicios. Hoy el código solo instala servicios TCP (`IP_PROTO 6` y `TCP_DST`); UDP o ICMP necesitarían un tipo de flujo nuevo. Cada servicio permitido a un rol cuesta **1 flujo por switch de acceso**.

## 5. Bajo qué condiciones aumentan los flujos

| Cambio | Flujos que añade | Dónde | Cómo se sabe |
|---|---|---|---|
| +1 **rol** con P servicios | **3 + P** | **cada** switch de acceso con usuarios | código y prueba |
| +1 servicio permitido a un rol | +1 | cada acceso, en la tabla de ese rol | prueba |
| +1 servicio denegado, sin regla o sin host | 0 | — | prueba |
| +1 **usuario** que se autentica | +1 (sesión) | su switch de acceso | código |
| +1 host con IP | +2 (capa 3) | su switch de acceso | prueba: 2 por host |
| +1 **puerto de acceso** | ≈ +6 | su switch de acceso | 2 medidos, 4 deducidos |
| +1 **switch de acceso** (una subred más) | **+1 ruta en cada core y en el borde**; 0 en los demás accesos | núcleo | prueba: core = accesos + 1 |
| +1 host de una subred ajena | 0 | el núcleo no lleva /32 de otros (medido con 0 y con 48 hosts por acceso) | prueba |
| IP bloqueada (lista negra, DROP de R5) | +1 por IP, en cada switch donde se instale | variable | **no medido** |
| Excepción por sesión (diseño, no hecho) | +1 por servicio y sesión, en la tabla del rol | su acceso | diseño |

### ¿Aumentan al crecer los switches de acceso?

- **En cada acceso, no.** Las tablas de rol son iguales en todos: 100 accesos llevan los mismos 39 flujos de R2 cada uno que acc-1. Un acceso con 48 puertos y 48 hosts tiene 193 flujos de capa 3 con 2, 50 o 200 accesos en la red (medido).
- **En el total de la red, sí, linealmente:** `accesos × flujos de roles`. Con 4 roles y 8 servicios son ≈ 30 por acceso, es decir 3000 con 100 accesos; con 58 roles × 126 servicios serían ≈ 7300 por acceso, es decir **más de 700 000 flujos en ONOS con 100 accesos**.
- **En el núcleo, sí:** +1 ruta por cada switch de acceso nuevo (core-1 = accesos + 1). Con 8000 no es el límite: harían falta unos 8000 accesos.

## 6. Límites que no son los 8000

- **Los flujos totales en ONOS**, no solo por switch. Cada flujo vive en Atomix; con muchos accesos y muchas reglas de rol, el total pesa mucho más que cualquier switch, y ya vimos timeouts de Atomix con unos cientos de flujos. Es el riesgo real al escalar.
- **El tiempo de instalar:** R2 envía los flujos de un switch de una vez (`sincronizar`); el vigilante limita un cambio a 200 flujos (`max_cambios`).
- **Las subredes:** un /24 admite 253 hosts y el ámbito DHCP actual 100 (.100–.199).
- **El archivo de política:** `rules` crece como roles × servicios (7300 líneas con 58 × 126); cada réplica del mapper lo carga entero.
