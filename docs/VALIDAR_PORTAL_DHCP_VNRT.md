# Validar en VNRT: DHCP, cuarentena, portal, ascenso de Alumno a Docente e invitado

Guion de prueba, paso a paso, con los logs que corresponden a cada uno. Todo se lanza desde la VM Controller salvo lo que diga "en h1", "en h4" o "en sw2" (se entra por SSH con `ssh -p <puerto> ubuntu@<IP_PUBLICA>`; los puertos son los de `~/slice.json`).

**Reparto de hosts para esta prueba** (4 hosts: h1 y h2 en sw2, h3 y h4 en sw3):

| Host | Qué es | Para qué |
|---|---|---|
| h1 | `alumno01` | Ascender a Docente (cruza sw2 → core → sw3 hasta el portal) |
| h2 | `alumno02` | Comprobar que un alumno no asciende y que la sesión de otro equipo no se toca |
| h3 | `invitado` | Cuarentena, DHCP sin 802.1X y entrada por el portal con `guest01` |
| h4 | `portal` | Servidor del portal, IP fija `10.0.0.60` |

Lo que valida cada bloque está explicado en `DESPLIEGUE_UBUNTU.md` (3d y 3e) y `CERTIFICADOS_Y_ESCALABILIDAD.md` (6). **Nada de esto se había ejecutado antes en las VM**: si algo no sale como dice "Debes ver", el apartado "Si falla" de cada paso es el primer sitio donde mirar.

## 0. Terminales de logs (déjalos abiertos todo el rato)

Cada uno en una sesión SSH al Controller:

```bash
# A. Policy Mapper: decisiones, flujos instalados, ascensos, portal
docker logs -f --tail 0 policy_mapper
# B. RADIUS: resultado de cada autenticación 802.1X
docker logs -f --tail 0 radius 2>&1 | grep --line-buffered -E "Access-Accept|Access-Reject|ERROR"
# C. ONOS: DHCP y AAA (el nivel DEBUG se pone una vez en la consola de ONOS; contraseña rocks)
ssh -p 8101 onos@127.0.0.1          # ahí dentro:  log:set DEBUG org.onosproject.dhcp   y   log:set DEBUG org.opencord.aaa   y salir con Ctrl+D
docker logs -f --tail 0 onos 2>&1 | grep --line-buffered -i -E "dhcp|aaa"
```

Además, en cada paso se usan estos comandos puntuales (los llamo por su nombre):

```bash
python3 slice.py estado                                                        # ESTADO: apps, switches, sesiones, flujos, concesiones DHCP, LDAP
curl -s http://127.0.0.1:5000/events | python3 -m json.tool | tail -30         # EVENTOS del Policy Mapper (admitido, rechazado, conflicto, logout)
curl -s http://127.0.0.1:5000/sessions | python3 -m json.tool                  # SESIONES activas (con "base" si es un ascenso)
sudo ovs-ofctl -O OpenFlow13 dump-flows sw2 | grep -v 0x888e                   # FLUJOS de un switch (en sw2 o sw3)
```

## 1. Actualizar y configurar

```bash
cd ~/proyecto-sdn && git pull
chmod +x R1/deploy/controller/*.sh
cd R1/deploy && python3 slice.py config        # hosts: h1 alumno01, h2 alumno02, h3 invitado (6), h4 portal (4); los puertos SSH y la interfaz como siempre
```
En la tabla de `config`: 1 = `alumno01`, 2 = `alumno02`, 3 = `alumno03`, 4 = `portal`, 5 = `invitado`, 6 = `iot`; también puedes escribir el nombre.

**Parámetros de DHCP** (rango, concesión, DNS): tienen valor por defecto y se pueden cambiar antes del paso 2 en `R1/deploy/.env` (ver `.env.example`). Para esta prueba sirven tal cual.

## 2. Controller: ONOS con DHCP, Policy Mapper y RADIUS reconstruidos

```bash
python3 slice.py controller
```
Reconstruye las imágenes (RADIUS ya sin el gancho de accounting), genera `PORTAL_TOKEN` en `.env`, recrea el Policy Mapper con la clave y ejecuta `init-onos.sh`, que ahora también configura la app DHCP.

**Debes ver**
- En la salida de `init-onos.sh`: `Configurando la app DHCP de ONOS (rango 10.0.0.100 - 10.0.0.199, concesion de 3600 s)` y, al final, `org.onosproject.dhcp: ACTIVE`.
- Terminal A (Policy Mapper): arranque sin trazas; luego `[R2 TABLAS]`-/sincronización de tablas cuando conecten los switches.
- Terminal B (RADIUS): `Ready to process requests` si miras el arranque con `docker logs radius --tail 20`.
- La configuración que ONOS tiene de DHCP:
  ```bash
  curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/network/configuration/apps/org.onosproject.dhcp | python3 -m json.tool
  # {"dhcp": {"ip": "10.0.0.2", "startip": "10.0.0.100", "endip": "10.0.0.199", "lease": "3600", ...}}
  ```
- `grep PORTAL_TOKEN ~/proyecto-sdn/R1/deploy/.env` y `docker exec policy_mapper printenv PORTAL_TOKEN` dan el mismo valor.

**Si falla:** la app DHCP no queda `ACTIVE` o la config vuelve vacía → `docker logs onos --tail 60 | grep -i dhcp` y repetir `bash R1/deploy/controller/init-onos.sh`. Si el nombre de la clave `dhcp` no fuera el que espera ONOS 2.7 la config se guarda pero la app no la usa: en ese caso, en la consola de ONOS `log:tail` lo mostrará y es el primer dato que necesito para corregirlo.

## 3. Switches

```bash
python3 slice.py switches          # si ya estaban conectados, no cambia nada
```
**Debes ver** en ESTADO los 3 dispositivos `available`. Cuando conectan, el Policy Mapper instala las tablas de R2 (terminal A).

Comprueba las reglas nuevas en un switch de acceso (en sw2):
```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows sw2 | grep -E "priority=40500|priority=1000|table=99"
# priority=40500,udp,tp_src=68,tp_dst=67 actions=CONTROLLER:65535          (DHCP al controlador)
# priority=1000 actions=push_vlan:0x8100,set_field:4195->vlan_vid,goto_table:99     (cuarentena)
# table=99, ... arp ... / ... tcp,nw_dst=10.0.0.60,tp_dst=8080 ... / table=99, priority=0 actions=drop
```
**Si falla:** no hay `table=99` → el Policy Mapper no cargó el `policy.yaml` nuevo: `docker restart policy_mapper && curl -s -X POST http://127.0.0.1:5000/policy/sync`.

## 4. Hosts: agente 802.1X y cliente DHCP

```bash
python3 slice.py hosts             # instala el agente, el cliente DHCP y deja el portal sin tocar; no autentica nada
```
**Debes ver** en el resumen del playbook (`dhcp.yml`), por host, una línea `inet 10.0.0.1xx/24 ... ens4`: **ya tienen IP aunque aún no autenticaron**.

Comprobación en h1 (y en h2 y h3):
```bash
ip -4 -o addr show ens4                       # inet 10.0.0.1xx/24
journalctl -u r1-dhcp -n 20 --no-pager        # DHCPDISCOVER ... DHCPOFFER ... DHCPACK
sudo tcpdump -ni ens4 -c 6 'port 67 or port 68'    # (opcional) Discover/Offer/Request/Ack
```
Terminal C (ONOS): líneas de la app DHCP (Discover, Offer, Ack) con la MAC de cada host. En el Controller:
```bash
python3 slice.py estado            # sección "Concesiones DHCP": cada host y su IP
curl -s -u onos:rocks http://127.0.0.1:8181/onos/dhcp/dhcp/mappings | python3 -m json.tool
```
En sw2, la regla de cuarentena ya ve tráfico (los hosts aún no tienen sesión):
```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows sw2 | grep -E "priority=40500|priority=1000"      # n_packets > 0 en las dos
```

**Si no hay IP:** (1) `n_packets=0` en la regla de 40500 → el Discover no llega al switch: `tcpdump` en el host. (2) Sube el contador pero ONOS no responde → terminal C vacía: revisa que la app esté `ACTIVE` y la config (paso 2). (3) `journalctl -u r1-dhcp` dice que el script falla → puede ser AppArmor: `sudo dmesg | grep -i apparmor | tail`; dímelo con esa salida. (4) La red virtual descarta el broadcast: `tcpdump` en un host vecino.

## 5. Portal: servidor, IP fija y puertos de confianza

```bash
python3 slice.py portal
```
Hace: IP fija `10.0.0.60` y el servicio `r1-portal` en h4, lee de ONOS dónde está h4 y los enlaces entre switches, escribe `R2/policy/policy.local.yaml` con los puertos de confianza, y **reinicia el Policy Mapper** (se pierden las sesiones; por eso va antes de `alta`).

**Debes ver**
- La salida: `h4: 10.0.0.60 MAC ... en of:... puerto N` y las líneas `confianza of:...: puertos [...]` (un switch por línea; el core aparece con sus enlaces).
- En h4: `systemctl status r1-portal --no-pager` activo, y `journalctl -u r1-portal -f` (déjalo en otra terminal: aquí salen todas las peticiones del portal con la IP de cada cliente).
- `cat ~/proyecto-sdn/R2/policy/policy.local.yaml`.
- Flujos de confianza en sw3 (donde está h4): `sudo ovs-ofctl -O OpenFlow13 dump-flows sw3 | grep "priority=40001" | grep -v dl_src` → un `in_port=<N>` por cada puerto de confianza.

**Si falla:** `ONOS aun no ve 10.0.0.60` → en h4 `ip -4 addr show ens4` y `journalctl -u r1-portal`; vuelve a lanzar `slice.py portal` (el ARP gratuito repite).

## 6. Cuarentena antes de autenticar (todavía sin `alta`)

Ningún host tiene sesión, así que todos están en cuarentena. **En h1** (y en h3):
```bash
curl -s -m 5 http://10.0.0.60:8080/ | grep -o "<title>[^<]*"     # SÍ llega: el portal está permitido en la cuarentena
ping -c 2 -W 1 10.0.0.60                                         # NO responde: solo se permite TCP al portal
curl -s -m 3 http://10.0.0.40:8003/ >/dev/null; echo $?          # NO llega (código distinto de 0)
```
**Debes ver:** el título del portal en el primero y fallo en los otros dos. En h4, `journalctl -u r1-portal` muestra la petición con la IP de h1. En sw2: `dump-flows sw2 | grep priority=1000` cuenta paquetes y `table=99` muestra los TCP al portal.

## 7. Autenticar los equipos: `alta`

```bash
python3 slice.py alta
```
**Debes ver**
- Terminal B (RADIUS): `Access-Accept` para `alumno01@example.org` y `alumno02@example.org`.
- Terminal A (Policy Mapper): por cada uno `EVALUANDO POLÍTICA ZERO TRUST`, `APROBADO` (VLAN 20), `Flujo Prio 40001 inyectado` y la regla de EAPOL de 50000.
- ESTADO: 2 sesiones (h3 y h4 no tienen 802.1X), y en sw2 un flujo `priority=40001,in_port=...,dl_src=<MAC>` por equipo con `goto_table:20`.
- **La IP de h1 y h2 no cambió** (`ip -4 addr show ens4`): la sesión las sacó de la cuarentena sin tocar la concesión.
- La ubicación que usará el ascenso, tomada de la tabla AAA de ONOS:
  ```bash
  curl -s -u onos:rocks http://127.0.0.1:8181/onos/aaa/app/users | python3 -m json.tool | grep -E "username|macAddress|connectPoint|authState"
  # alumno01@example.org, MAC de h1, of:00001a749039894a/<puerto>, AUTHORIZED_STATE
  ```
- Con sesión, h1 sigue alcanzando el portal (la tabla 20 lo permite): `curl -s http://10.0.0.60:8080/ | grep -o "Subir de rol"` **en h1** → `Subir de rol` (el portal le ofrece subir porque su equipo es Alumno).

**Si falla:** rechazo `MAC no coincide` → es el device binding: `alta` lo corrige (libera y fija en LDAP lo que ONOS vio).

## 8. Ascender h1 de Alumno a Docente

**En h1:**
```bash
curl -s -d 'usuario=profesor01&clave=password123' http://10.0.0.60:8080/login | grep -o -i -E "docente|vuelve al rol alumno"
```
**Debes ver**
- Terminal A (Policy Mapper): `[V] ASCENSO: profesor01 sube alumno01@example.org (Alumno) a Docente en of:...:N -> tabla 20 -> 10`.
- Terminal de h4 (`journalctl -u r1-portal -f`): `POST /login` desde la IP de h1.
- SESIONES: la sesión de la MAC de h1 es `profesor01`, `Docente`, método `Portal`, con `"base"` = `alumno01@example.org`.
- FLUJOS en sw2: el flujo de la MAC de h1 pasó de `goto_table:20` a `goto_table:10`.
- EVENTOS: `admitido ... Ascenso por el portal: Alumno -> Docente`.

**Pruebas negativas (cada una debe dar 409 y un evento `conflicto`; el flujo de h1 no cambia):**
```bash
# en h1: otro profesor sobre un equipo ya ascendido
curl -s -d 'usuario=profesor02&clave=password123' http://10.0.0.60:8080/login | grep -o -i "ya existe una sesi[^<]*"
# en h2: un alumno no sube (alumno -> alumno no está en la tabla `ascenso`)
curl -s -d 'usuario=alumno02&clave=password123' http://10.0.0.60:8080/login | grep -o -i "no admite subir[^<]*"
# en h2: un profesor que ya tiene sesión en h1 no puede abrir otra
curl -s -d 'usuario=profesor01&clave=password123' http://10.0.0.60:8080/login | grep -o -i "ya existe una sesi[^<]*"
```
En terminal A cada una deja `conflicto` con el motivo (`Portal: ...`). Contraseña incorrecta (`clave=mala`) → 401 y evento `rechazado`.

**Volver a Alumno** (en h1):
```bash
curl -s -X POST -d '' http://10.0.0.60:8080/logout -o /dev/null -w "%{http_code}\n"       # 302
```
Terminal A: `Ascenso de profesor01 ... cerrada por el usuario; el equipo vuelve a Alumno`; sw2: el flujo de h1 vuelve a `goto_table:20`; SESIONES: `alumno01@example.org`, Alumno, 802.1X, sin `base`.

**Ascenso y antispoofing, sin cruzarse** (vuelve a ascender a h1 y luego, en la página del agente de h1 —`python3 slice.py interfaz`— o con `sudo systemctl restart r1-agente` en h1, reautentica con `alumno01`): terminal A debe mostrar que el ascenso se anula y h1 queda como Alumno **sin** `lista negra`. Autenticar con otra identidad (`alumno02`) en h1 sigue siendo suplantación: 409 y MAC en lista negra 600 s.

## 9. Entrar como invitado (h3)

h3 tiene IP por DHCP y está en cuarentena desde el paso 4. **En h3:**
```bash
ip -4 -o addr show ens4
curl -s -d 'usuario=guest01&clave=guest123' http://10.0.0.60:8080/login | grep -o -i -E "invitado|sesi[^<]*"
```
**Debes ver**
- Terminal A (Policy Mapper): `[V] PORTAL: guest01 (invitado) en of:...:N -> tabla 50`. Para ubicar a h3 el Policy Mapper pregunta a ONOS por su MAC: la app DHCP ya lo registró con IP y ubicación (`curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/hosts | python3 -m json.tool | grep -B3 -A8 <MAC de h3>`).
- SESIONES: `guest01`, `Invitado`, método `Portal`, y `expira` a 30 minutos (`portal.duracion_s`).
- sw3: aparece `priority=40001,in_port=...,dl_src=<MAC de h3>` con `goto_table:50`, y la IP de h3 no cambió.
- Con la sesión de invitado, h3 solo alcanza `university_portal` (`10.0.0.40:8003`, que en el slice no existe) y el portal: `curl -s -m 3 http://10.0.0.60:8080/ | grep -o -i "sesi[^<]*"` funciona y lo demás sigue sin llegar.

**Cerrar la sesión** (en h3): `curl -s -X POST -d '' http://10.0.0.60:8080/logout -o /dev/null -w "%{http_code}\n"`. Terminal A: `Portal: sesión cerrada por el usuario`; el flujo de h3 desaparece y h3 **vuelve a la cuarentena** (repite el `curl` al portal y el `ping`, como en el paso 6). Contraseña incorrecta: 401, evento `rechazado`.

**Si falla:** `No se pudo ubicar el equipo en la red` → ONOS no conoce a h3: comprueba que la concesión DHCP existe (paso 4) y que `/onos/v1/hosts` lo lista; ese es el único punto donde el invitado depende de la app DHCP.

## 10. Cierre de sesión 802.1X (ya validado antes) y limpieza

```bash
python3 slice.py limpiar           # borra sesiones y flujos y reinstala las tablas (incluida la cuarentena)
```
Para repetir: `limpiar` y `alta`.

## Qué log mira cada comprobación

| Qué compruebas | Dónde |
|---|---|
| ONOS configuró y atiende DHCP | Terminal C; `network/configuration/apps/org.onosproject.dhcp`; `dhcp/dhcp/mappings` |
| El host pidió y recibió IP | En el host: `journalctl -u r1-dhcp`, `tcpdump`; en el switch: contador de `priority=40500` |
| Cuarentena | Contador de `priority=1000` y `table=99` en el switch; el `curl`/`ping` del paso 6 |
| Autenticación 802.1X | Terminal B (RADIUS) y terminal A (`APROBADO`, `Flujo Prio 40001`) |
| Ascenso | Terminal A (`ASCENSO`), `journalctl -u r1-portal` en h4, EVENTOS, SESIONES, `goto_table` del flujo |
| Invitado | Terminal A (`PORTAL ... tabla 50`), `/onos/v1/hosts`, SESIONES, `goto_table:50` |
| Ubicación del ascenso | `/onos/aaa/app/users` (entrada `AUTHORIZED` de la MAC) |
| Rechazos y conflictos | EVENTOS (`rechazado`, `conflicto`) y terminal A |
