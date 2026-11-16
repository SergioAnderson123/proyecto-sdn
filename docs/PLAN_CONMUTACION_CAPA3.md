# Plan de conmutación a capa 3 (con retroceso)

> **Estado (16-nov): CONMUTADO en la slice.** Los pasos 1 a 3 se ejecutaron y se verificaron con sesión 802.1X (docente *refused*, alumno *timed out* en el 8101); ver V8 en `DISENO_CAPA3.md`. Para volver atrás sigue vigente la sección 5. Pendiente: caída de un cable en vivo e IP persistente en los hosts.

Cómo pasar la producción del slice de reenvío L2 (tabla 60: unicast por MAC y difusión ARP por árbol) a **capa 3 por subredes** (plan A de `PLAN_DIRECCIONAMIENTO.md`) sin dejar la red sin servicio por más de unos segundos, y cómo volver atrás. Diseño: `DISENO_CAPA3.md`. Estado de lo ya probado: V1–V7 allí.

## 1. Lo que ya está probado y lo que no

| Pieza | Estado |
|---|---|
| ARP del gateway como flujos propios de ONOS, `allowExtraneousRules=false` | en vivo (V5), ya en producción: 4 flujos, nadie usa esas IP |
| Vigilante aplicando capa 3 (rutas, entrega /32, TTL, MAC), mantenido por ONOS | en vivo, **aislado** en la tabla 62 con subredes de prueba (V7) |
| Mismo vigilante en la tabla 60 con las subredes reales | solo simulación y lectura (V6: sobran 30 flujos L2, faltan 15) |
| Caída de un cable con la capa 3 | solo simulación (ningún estado intermedio forma un lazo) |
| IP del plan A en los hosts, DHCP por subred | **pendiente** |

## 2. Hallazgo de diseño: la autorización de R2 (leer antes de decidir)

La regla de entrada de la prueba aislada (tabla 0, prioridad 40060) **se salta las tablas de rol de R2**: el IPv4 con la MAC del gateway sube a la tabla 62 sin pasar por la sesión 802.1X (40001) ni la cuarentena (1000). Con IP de prueba que nadie usa no importa; **con hosts reales sería un agujero**: un host sin sesión podría enrutar por el gateway. Por eso:
- la entrada aislada solo se usa con la MAC de gateway de prueba (queda anotado en `R5/deploy/prioridades.yaml` y probado);
- la capa 3 real va **en la tabla 60**, a la que se llega **después** de la sesión y de las tablas de rol (o por la confianza de las troncales), de modo que la autorización de R2 se mantiene. Comprobado en simulación con las tablas de rol reales de R2 (`tests/test_capa3_con_politica_r2.py`, 10 pruebas): un docente con sesión llega al 8101 por el gateway con MAC y TTL correctos, un alumno no, el ICMP y los puertos no permitidos se descartan, un host sin sesión no usa el gateway y la respuesta del servidor vuelve por la confianza. En vivo se comprobará en la conmutación. No existe una etapa de "coexistencia" segura con hosts reales en otra tabla: el paso real es la conmutación de la sección 4.

## 3. Condiciones previas (todas, antes de empezar)

1. Diagnóstico `95` limpio y 14 enlaces vivos en ONOS; freno `99` a mano.
2. Los 3 hosts con IP persistente del plan A: h1 `10.0.1.101/24` (gw `10.0.1.254`), h2 `10.0.2.102/24` (gw `10.0.2.254`), server `10.0.0.10/24` (gw `10.0.0.254`) con rutas `10.0.1.0/24` y `10.0.2.0/24` por `10.0.0.254`. Se ponen **primero como secundarias** (no quitan la IP vieja) y se comprueban con `arping` al gateway.
3. `hosts_fijos` con esas IP (`R2/policy/planes/hosts_fijos_capa3.json`): ONOS no aprende las IP (ARP sin copia), así que el vigilante las toma de ahí. Hosts nuevos (DHCP/802.1X) necesitan que ONOS conozca su IP: decisión abierta (`--copiar-arp` o DHCP snooping) antes de abrir la red a más hosts.
4. Comprobación previa de solo lectura (`66_preflight_conmutacion.yml`): diferencia esperada 30 sobran y 15 faltan, servicio L2 activo, app con 4 flujos.
5. Dos personas disponibles, ventana corta, y el retroceso de la sección 5 leído.

## 4. Conmutación (ventana de unos segundos)

1. **Parar el vigilante L2** en ctrl-3 y ctrl-2 (`65_vigilante.yml -e accion=parar`): dos vigilantes no pueden pelear por la tabla 60.
2. **Un ciclo del vigilante en modo capa 3 en la tabla 60** con `--aplicar --estable 0`: borra los 30 flujos L2 y monta los 15 de capa 3 (borrar antes de añadir: el peor estado intermedio descarta, nunca forma un lazo). Corte esperado: segundos.
3. **Quitar la IP vieja** de h1 y h2 (`10.0.0.201`, `10.0.0.202`): pasan a depender solo del gateway.
4. **Verificar con sesión 802.1X** (el ICMP nunca pasa por R2 y sin sesión todo IPv4 se descarta, así que no vale el `ping`): autenticar h1 (docente) y h2 (alumno) con `72_suplicante.yml`, y `69_conmutacion_hosts.yml -e accion=verificar`: ARP del gateway y `nc` a `10.0.0.10:8101`, que debe dar *refused* para el docente (llega y vuelve) y *timed out* para el alumno (bloqueado por su rol), más los contadores de los flujos de entrega /32 de la tabla 60. Después el diagnóstico `95`.
5. **Arrancar el servicio del vigilante en modo capa 3** (principal en ctrl-3, respaldo en ctrl-2) para que mantenga la tabla 60 ante caídas.

Criterios de **abortar** (ir al retroceso): el docente h1 con sesión no recibe *refused* en el 8101 a los 30 s del paso 3; el `95` muestra tormenta; ONOS pierde enlaces; cualquier duda.

## 4 bis. Lo que NO cubre el plan

**Cómo sabe el vigilante la IP de un host (y por qué un host nuevo no la tendría).**
- El servidor DHCP **sí está dentro de ONOS** (app `org.onosproject.dhcp`): R2 sube el DHCP del cliente (UDP 68 a 67, prioridad 40500) al controlador y esa app lo contesta, con un solo rango.
- Pero la lista de hosts de ONOS (`/hosts`, la que lee el vigilante) **no se alimenta de las concesiones DHCP**: aprende IP↔MAC de los ARP e IPv4 que llegan al controlador. El reenvío proactivo manda el ARP directo a la tabla 60 (regla 40003) **sin copia al controlador**, así que ONOS casi nunca lo ve y lista los hosts con `ipAddresses: []` (lo medido con h1). El EAPOL (802.1X) le da la MAC y el puerto, no la IP.
- Hoy el vigilante toma las IP de `hosts_fijos` (los 3 hosts de la slice). Para hosts nuevos hay tres caminos: (a) `--copiar-arp` (una copia de cada petición ARP por puerto de acceso: ONOS aprende la IP, a costa de tráfico al controlador); (b) que el vigilante lea las **concesiones de la app DHCP de ONOS** por su REST (la ruta exacta, `/onos/dhcp/allocations` según recuerdo, hay que comprobarla en vivo; es la fuente natural para hosts con DHCP); (c) seguir con `hosts_fijos` para los equipos con IP fija.
- Una IP desconocida **falla cerrada**: el host sin IP en el vigilante no recibe reglas de entrega y no se alcanza.

**Otras cosas sin cubrir:** R5 (`HOME_NET` agregado, espejo a la tabla 60, bloqueo por MAC; lo lleva otra persona, notas en `PLAN_DIRECCIONAMIENTO.md` §3), DHCP por subred (la app de ONOS tiene un solo rango), bond del server, y la caída de un cable en vivo (se prueba con `80_caida_cable.yml`).

## 5. Retroceso (en este orden)
1. Parar el vigilante capa 3 (si ya arrancó el servicio).
2. Volver a poner `10.0.0.201` en h1 y `10.0.0.202` en h2 (la IP del plan A puede quedarse como secundaria).
3. Arrancar el vigilante L2 (`65_vigilante.yml -e aplicar=true -e usar_hosts_fijos=true`): en su primer ciclo ve los flujos de capa 3 como "sobran" y los L2 como "faltan", borra y repone en ese orden (el mismo mecanismo, en sentido inverso).
4. `95` y `arping` h1 al server (10.0.0.10).
Tiempo esperado: menos de un minuto. Si el vigilante L2 no repusiera por algún motivo: `70_fase_e.yml` / `60_proactivo.yml` reinstalan el reenvío proactivo desde cero.

## 6. Qué se automatiza y qué es manual
- **Hecho y seguro de ejecutar ya:**
  - `66_preflight_conmutacion.yml`: solo lectura (ejecutado el 16-nov: 14 enlaces, app con 4 flujos, 30 sobran y 15 faltan, servicio L2 activo en ctrl-3 y ctrl-2).
  - `67_hosts_plan_a.yml`: IP del plan A como secundaria en los hosts y rutas hacia las otras subredes; no persiste y se quita con `-e accion=quitar`.
  - `68_vigilante_capa3_servicio.yml`: servicio `vigilante-capa3` en ctrl-3 (principal) y ctrl-2 (respaldo) en modo **solo avisar**, en una unidad aparte; no toca el L2. Con `-e aplicar=true` aplica en la tabla 60: solo durante la conmutación y con el L2 parado.
  - `65_vigilante.yml` corregido (copiaba sin `capa3.py`).
  - `69_conmutacion_hosts.yml`: quitar la IP vieja de h1 y h2 (se niega si la capa 3 no está montada), verificar y restaurar; el encabezado trae el orden completo y el retroceso.
- **Ejecutado el 16-nov:** 66, 67 y 68 (los dos servicios de capa 3 activos solo avisando). **Por ejecutar, solo con tu decisión:** la secuencia del encabezado de `69_conmutacion_hosts.yml` (parar el L2 con el 65, `68 -e aplicar=true`, `69 quitar_ip_vieja -e confirmar=true`, sesiones con el 72, `69 verificar`).
