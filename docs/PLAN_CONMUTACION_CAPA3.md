# Plan de conmutación a capa 3 (con retroceso)

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
- la capa 3 real va **en la tabla 60**, a la que se llega **después** de la sesión y de las tablas de rol (o por la confianza de las troncales), de modo que la autorización de R2 se mantiene. No existe una etapa de "coexistencia" segura con hosts reales en otra tabla: el paso real es la conmutación de la sección 4.

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
4. **Verificar** (`arping` al gateway, ping h1–h2 con TTL 61, h1–server con TTL 62, ARP a 10.0.0.10 desde h1 ya enrutado) y el diagnóstico `95`.
5. **Arrancar el servicio del vigilante en modo capa 3** (principal en ctrl-3, respaldo en ctrl-2) para que mantenga la tabla 60 ante caídas.

Criterios de **abortar** (ir al retroceso): ping h1–h2 sin respuesta a los 30 s del paso 3; el `95` muestra tormenta; ONOS pierde enlaces; cualquier duda.

## 4 bis. Lo que NO cubre el plan
Hosts nuevos sin IP conocida por ONOS (no reciben reglas: falla cerrado), R5 (`HOME_NET` agregado, espejo a la tabla 60, bloqueo por MAC), DHCP por subred, bond del server, caída de un cable en vivo (se recomienda probarla en la ventana, con un cable de acceso redundante y el retroceso a mano).

## 5. Retroceso (en este orden)
1. Parar el vigilante capa 3 (si ya arrancó el servicio).
2. Volver a poner `10.0.0.201` en h1 y `10.0.0.202` en h2 (la IP del plan A puede quedarse como secundaria).
3. Arrancar el vigilante L2 (`65_vigilante.yml -e aplicar=true -e usar_hosts_fijos=true`): en su primer ciclo ve los flujos de capa 3 como "sobran" y los L2 como "faltan", borra y repone en ese orden (el mismo mecanismo, en sentido inverso).
4. `95` y `arping` h1 al server (10.0.0.10).
Tiempo esperado: menos de un minuto. Si el vigilante L2 no repusiera por algún motivo: `70_fase_e.yml` / `60_proactivo.yml` reinstalan el reenvío proactivo desde cero.

## 6. Qué se automatiza y qué es manual
- **Hecho:** comprobación previa de solo lectura (`66_preflight_conmutacion.yml`), plan y archivos de hosts fijos del plan A, el servicio del vigilante L2 con el código completo (se corrigió que `65_vigilante.yml` no copiaba `capa3.py`).
- **Por hacer, tras revisar este plan:** playbook de hosts (IP secundaria y luego primaria, con retroceso), servicio del vigilante en modo capa 3 y el playbook de conmutación con `-e confirmar=true`. No se ejecutan sin tu decisión.
