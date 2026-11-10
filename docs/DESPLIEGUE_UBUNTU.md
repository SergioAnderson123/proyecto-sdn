# Despliegue en un slice nuevo (Ubuntu 22.04, una VM por rol)

Todo se lanza desde la **VM Controller** con un asistente en Python, `R1/deploy/slice.py` (solo biblioteca estándar; Python 3.10 viene en Ubuntu 22.04). Va pidiendo los datos que cambian entre slices (IP, puertos SSH, interfaz, usuarios de cada host); el resto (switches, MAC, puertos, `deviceId`) se descubre solo.

| VM | Qué corre | Lo gestiona |
|---|---|---|
| Controller | ONOS 2.7, OpenLDAP, Policy Mapper, FreeRADIUS (y Ansible) | `slice.py controller` |
| sw1..sw3 | Open vSwitch (sin contenedores) | `slice.py switches` |
| h1..h4 | `wpa_supplicant` (suplicantes 802.1X con la identidad de su equipo) y cliente DHCP; uno de ellos puede hacer de servidor del portal cautivo (IP fija) | `slice.py hosts`, `dhcp`, `portal` y `alta` |
| IDS | Suricata 7 + integrador R5 | `R5/deploy/ids/` (sección 4) |

Topología de referencia: h1 y h2 cuelgan de sw2; h3 y h4, de sw3; sw1 es el core y no recibe flujos de sesión.

## 1. Preparar el Controller (una vez)

Trabaja siempre con el **mismo usuario** (recomendado: `ubuntu`, no `root`): la configuración de `slice.py` (`~/slice.json`) y la llave SSH se guardan en la home del usuario que lo ejecuta, y los archivos que crean los scripts quedan a su nombre.

```bash
sudo apt-get update && sudo apt-get install -y git curl wget ca-certificates
curl -fsSL https://get.docker.com | sudo sh                 # Docker Engine + plugin compose v2
sudo systemctl enable --now docker                          # arranca el demonio (y en cada reinicio)
sudo usermod -aG docker "$USER" && newgrp docker            # tu usuario puede usar Docker sin sudo
docker ps                                                   # debe responder sin error (aunque la lista esté vacía)
git clone <URL-del-repo> /home/ubuntu/proyecto-sdn          # en la home de ubuntu, no en /root
sudo chown -R "$USER":"$USER" /home/ubuntu/proyecto-sdn     # el dueño correcto evita "Permission denied" al crear archivos
cd /home/ubuntu/proyecto-sdn
chmod +x R1/deploy/controller/*.sh R5/deploy/ids/up.sh      # los .sh pueden llegar sin permiso de ejecución (clonado desde Windows)
cd R1/deploy && python3 slice.py
```
Si `docker ps` dice `permission denied ... docker.sock`, tu usuario aún no está en el grupo `docker`: cierra la sesión SSH y vuelve a entrar (o usa `newgrp docker`). Si dice `Cannot connect to the Docker daemon`, el demonio no está iniciado: `sudo systemctl enable --now docker` y vuelve a probar con `docker ps`; si falta el comando, repite la instalación.

Antes de levantar nada, comprueba el **espacio en disco**: las imágenes de Docker (ONOS ~1,2 GB, más FreeRADIUS, OpenLDAP y el Policy Mapper) piden **al menos 3 GB libres** (`df -h /`). Para liberar: `sudo apt-get clean && sudo apt-get autoremove -y --purge`, `sudo journalctl --vacuum-size=20M`, y `docker system prune -af --volumes` si hay imágenes viejas.

ONOS necesita ~1-2 GB de RAM: no lo pongas junto al IDS en una VM de 2 vCPU / 2 GB. El Controller necesita internet (SADIS y AAA salen de Maven Central, y las imágenes de Docker).

Si ya habías desplegado antes en esa VM y quieres empezar de cero: `docker rm -f $(docker ps -aq)` borra todos los contenedores, `docker system prune -af --volumes` borra además las imágenes y los volúmenes sin uso (se vuelven a descargar), y `docker network rm sdn` quita la red del proyecto.

## 2. Desplegar y validar (lo normal)

```bash
python3 slice.py             # menú interactivo: elige un paso por número y va pidiendo lo que falte
```
El menú tiene las mismas opciones que los comandos de abajo; sin argumentos, o con un paso, así:

```bash
python3 slice.py config      # pregunta IP pública, IP del Controller, cuántos switches y hosts, puertos SSH, interfaz y usuario de cada host; guarda ~/slice.json
python3 slice.py preparar    # instala Ansible y copia tu llave SSH a switches y hosts (pide sus contraseñas una vez)
python3 slice.py todo        # controller + switches + hosts + portal (si hay host de portal) + alta (pide una vez la contraseña de sudo de ubuntu)
python3 slice.py estado      # resumen: apps, switches, sesiones, flujos y LDAP
```
En `config`, el asistente enseña la tabla de la sección 5 y para cada host pregunta el **puerto SSH** y **qué es** (identidad de equipo `alumno01..03`, o un host sin 802.1X: `portal`, `invitado`, `iot`), por número o por nombre; avisa si repites una identidad en dos hosts (solo puede tener una sesión a la vez) o pones dos portales. Al final de `estado` debes ver una sesión por host con 802.1X y, por switch de acceso, un flujo de sesión por cada uno. Con el reparto por defecto:

| Host | Usuario | Rol / VLAN | Switch de acceso |
|---|---|---|---|
| h1 | `alumno01` | Alumno / 20 | sw2 |
| h2 | `alumno02` | Alumno / 20 | sw2 |
| h3 | `alumno03` | Alumno / 20 | sw3 |
| h4 | `portal` (sin 802.1X, IP 10.0.0.60) | puerto de confianza | sw3 |

Con más de cuatro hosts, el quinto es `invitado` y el sexto `iot` (también sin 802.1X). Todos toman la IP por DHCP salvo el portal (`10.0.0.60`, fija). Los profesores no llevan certificado en ninguna PC: suben el equipo a Docente desde el portal (sección 3d).

Para repetir la prueba desde cero: `python3 slice.py limpiar` y luego `python3 slice.py alta`.

## 3. Qué hace cada paso

| Comando | Qué hace |
|---|---|
| `config` | Pregunta los datos del slice (Enter acepta el valor por defecto, que es el de la configuración anterior si existe) y los guarda en `~/slice.json`. Los puertos de switches y hosts son los SSH |
| `preparar` | Instala Ansible, crea `~/.ssh/id_ed25519` y la copia con `ssh-copy-id` a cada switch y host |
| `controller` | Rellena `R1/deploy/.env`, quita contenedores de otro despliegue con el mismo nombre, ejecuta `controller/up.sh` (ONOS, LDAP, Policy Mapper, RADIUS e `init-onos.sh`) y comprueba que las 7 apps de ONOS estén `ACTIVE`; si falta alguna, repite `init-onos.sh` |
| `switches` | Genera el inventario y lanza el playbook `R1/deploy/switches/switches.yml`: instala Open vSwitch si falta, fail-mode `secure`, `OpenFlow13` y controlador `tcp:<CONTROLLER_IP>:6653`, y espera a que cada switch conecte |
| `hosts` | Genera el inventario y lanza `R1/deploy/hosts/hosts.yml`: instala `wpasupplicant`, copia la CA y el certificado y `.conf` de las identidades de equipo (`alumno01..03`) más el de la asignada a ese host si es una cuenta de prueba, **quita los certificados que no son del host** (por ejemplo los de profesor de versiones anteriores) y deja los servicios `r1-eapol-bcast` y `r1-agente` (el agente 802.1X con interfaz web, sección 3c). El agente queda **habilitado** (arranca con el host) pero este paso no lo arranca. Después lanza `dhcp.yml` (ver `dhcp`) |
| `dhcp` | Instala en los hosts con y sin 802.1X el cliente DHCP (servicio `r1-dhcp`): el host pide IP desde que arranca, aun sin autenticar, y la app DHCP de ONOS se la da (sección 3e) |
| `portal` | Pone el portal cautivo en el host de tipo `portal`, con IP fija, y fija los puertos de confianza (sección 3d). Hay que lanzarlo antes de `alta`: reinicia el Policy Mapper |
| `alta` | (1) Libera en LDAP la MAC, el switch y el puerto de los usuarios; (2) arranca el agente de cada host, que se autentica solo con la identidad asignada; (3) espera a que ONOS vea las autenticaciones y **fija en LDAP lo que ONOS vio**; (4) los reautentica ya con la comprobación de MAC y ubicación activa |
| `estado` / `limpiar` | Resumen del estado / borra sesiones y flujos de R2 (sin tocar los flujos base de ONOS) y reinstala las tablas de rol |

Dos detalles de `alta`:
- **Por qué se libera primero.** El LDIF del repo (`R1/shared/ldap/ldif/custom/estructura.ldif`) trae la MAC, el switch y el puerto de los alumnos de otro slice. Con datos que no coinciden, el Policy Mapper rechaza y deja la MAC 300 s en lista negra. Mientras un usuario está "liberado" no se comprueba su MAC ni su ubicación: no lo dejes así en una prueba de seguridad (el segundo paso de `alta` ya la reactiva).
- **Por qué existe `r1-eapol-bcast`.** La red virtual de VNRT descarta las tramas a `01:80:c2:00:00:03`, donde `wpa_supplicant` manda el EAPOL, antes de que lleguen al switch. Es una limitación de esa red, no del diseño. El servicio cambia el destino a broadcast al salir del host (`tc`), y ONOS lo captura igual porque filtra por `eth_type=0x888e`. Pedir al personal de VNRT que permita esa dirección lo haría innecesario.

## 3b. Ver los logs de Policy Mapper y RADIUS

Se ejecutan en el Controller. Los dos servicios corren en contenedores Docker. Para seguirlos a la vez, abre dos sesiones SSH.

**Policy Mapper** (decide si se admite a cada usuario e instala su flujo de sesión):
```bash
docker logs policy_mapper --tail 50                                      # últimas 50 líneas
docker logs -f --tail 20 policy_mapper                                   # en vivo (Ctrl+C para salir)
docker logs policy_mapper 2>&1 | grep -E "APROBADO|RECHAZO|ALERTA"      # solo las decisiones
```
Qué buscar: `EVALUANDO POLÍTICA ZERO TRUST: <usuario>`, luego `APROBADO` con su VLAN o `RECHAZO` con el motivo (`MAC no coincide`, `Puerto físico no asignado`...), y `Flujo Prio 40001 inyectado` con el switch donde se instaló. También hay un registro por la API:
```bash
curl -s http://127.0.0.1:5000/events | python3 -m json.tool | tail -30
curl -s http://127.0.0.1:5000/sessions | python3 -m json.tool           # sesiones activas
```

**RADIUS** (FreeRADIUS corre con `-X`, en modo depuración: es muy verboso, conviene filtrar):
```bash
docker logs radius --tail 50
docker logs -f --tail 0 radius 2>&1 | grep --line-buffered -E "Access-Accept|Access-Reject|Login OK|Login incorrect|ERROR|Error"
docker logs radius 2>&1 | grep -i "profesor01"                         # solo un usuario
```
Qué buscar: `Sent Access-Accept` o `Sent Access-Reject` (el resultado), `ERROR` con `TLS` (problemas de certificado) y las llamadas a `rest` (Policy Mapper) y `ldap`.

Si `docker logs` dice `permission denied`, tu usuario no está en el grupo `docker` (sección 1); si dice `No such container`, el servicio aún no está arriba: `docker ps -a`.

## 3c. Agente 802.1X con interfaz web (cómo se vería en una PC)

En una PC real el suplicante lo lanza el sistema, no una persona: en Windows el servicio *Configuración automática de redes cableadas* y en Linux NetworkManager o `wpa_supplicant`, que autentican cuando el enlace sube (se conecta el cable) y, según el perfil, al arrancar o al iniciar sesión el usuario. El certificado no va en variables de entorno, sino en el almacén de certificados (Windows) o en el perfil de conexión (Linux).

En los hosts de VNRT eso lo hace el **agente** (`R1/agente_802_1x/agente.py`, servicio `r1-agente`), instalado por `python3 slice.py hosts`:
- **Arranque automático:** al arrancar el host (o al repetir `alta`), el agente se autentica solo con la identidad que asignaste a ese host en `slice.py`. Si la interfaz baja y sube (`sudo ip link set <iface> down && sudo ip link set <iface> up`), `wpa_supplicant` vuelve a autenticar.
- **Interfaz web:** una página (identidad, Conectar y Desconectar, estado, VLAN, rol, switch y puerto, y el registro del suplicante) en el puerto 8080 del propio host, solo en `127.0.0.1`. Se ve desde tu PC con un túnel SSH; `python3 slice.py interfaz` imprime el de cada host (h1 en el puerto local 8081, h2 en el 8082...):
  ```bash
  ssh -L 8081:127.0.0.1:8080 -p <puerto-de-h1> ubuntu@<IP_PUBLICA>      # y abrir http://localhost:8081
  ```
- **Una sola instancia:** el agente es quien lanza `wpa_supplicant`, siempre uno a la vez (dos instancias cruzan el EAP). Si el suplicante se cae, lo vuelve a lanzar.
- **Estado:** lo lee con `wpa_cli`; el rol, la VLAN, el switch y el puerto los consulta al Policy Mapper (`http://<CONTROLLER_IP>:5000/sessions`), y el motivo de un rechazo, a `/events`.

**Guion de demo**
1. `python3 slice.py alta` y abre la página de h1: *Autenticado, Alumno, VLAN 20, sw2*.
2. Baja y sube la interfaz de h1: se reautentica sola (la página pasa por *Autenticando* y vuelve a *Autenticado*).
3. En la página de h1 elige `alumno02` y pulsa *Conectar*: el Policy Mapper lo **rechaza** (*MAC registrada distinta de la física*), porque cada identidad está atada a la MAC y al puerto de su equipo (*device binding*). Ese rechazo deja la MAC de h1 en lista negra **300 s**: durante ese tiempo h1 no podrá autenticarse ni con su identidad. Vuelve a elegir `alumno01` pasado ese tiempo, o ejecuta `python3 slice.py limpiar`.
4. *Desconectar* equivale a cerrar la sesión: el suplicante envía el *logoff*, ONOS pasa el host a `IDLE` y el agente avisa al Policy Mapper, que borra la sesión y su flujo de sesión. Al volver a pulsar *Conectar*, el Policy Mapper lo aprueba desde cero.

**Cierre de sesión: qué cubre y qué no.** ONOS AAA 2.7.0 procesa el `EAPOL-Logoff` (`AUTHORIZED → IDLE`, se ve en `docker logs onos` con `log:set DEBUG org.opencord.aaa`), pero **no envía `Accounting-Request` a RADIUS** (sus contadores no tienen accounting), así que no hay otro aviso de cierre. Por eso el agente avisa al Policy Mapper con `POST /sessions/logout` (por la red de gestión, con la clave `AGENTE_TOKEN` que `slice.py controller` genera en `R1/deploy/.env` y reparte al Policy Mapper y a los hosts). El endpoint borra la sesión 802.1X y su flujo, sin lista negra, y queda desactivado si no hay clave.

| Situación | ¿Cierra la sesión? |
|---|---|
| *Desconectar* en la página, o cambiar de identidad | Sí |
| Apagar o reiniciar el host, o parar `r1-agente` | Sí (el agente recibe SIGTERM y desconecta antes de salir) |
| `ip link set <iface> down` (cable desconectado) | Sí: el agente vigila `operstate`, avisa por la red de gestión (la interfaz de datos no puede enviar nada) y, cuando el enlace vuelve, hace `logoff` y `logon` para reautenticar limpio |
| Cerrar sesión de un usuario en un escritorio (XFCE) | **No todavía**: el agente es un servicio del sistema. Se resolverá con un gancho PAM que llame a su API local (`POST 127.0.0.1:8080/api/desconectar`) |
| El host se cae sin apagado ordenado | **No**: nadie avisa. Haría falta un *heartbeat* con caducidad de la sesión |

Para comprobarlo: pulsa *Desconectar* y mira, en el Controller, `docker logs policy_mapper --since 2m 2>&1 | grep LOGOUT`, `curl -s http://127.0.0.1:5000/sessions` (h1 ya no está) y `sudo ovs-ofctl -O OpenFlow13 dump-flows sw2 | grep 40001` (ya no está su flujo de sesión); la regla de EAPOL de `priority=50000` sigue, porque es por puerto.

El ascenso de un equipo de Alumno a Docente con la cuenta personal de un profesor se explica y se prueba en la sección 3d.

**Qué no es idéntico a una PC real:** la interfaz es una página web, no una ventana del sistema (un escritorio real, como XFCE con RDP, cabe en disco: 139 MB el mínimo y 1,5 GB el completo, medido en h1; pero la RAM de h1, 448 MB disponibles, y su único vCPU no alcanzan: ver docs/Consideraciones_VM_VNRT.pptx); y el EAPOL sigue necesitando el `tc` de `r1-eapol-bcast`, que es una limitación de la red virtual de VNRT.

## 3d. Portal cautivo en un host y ascenso a Docente (R2)

**Dónde va el servidor.** En uno de los hosts (tipo `portal` en `slice.py config`), no en el Controller: el portal toma la MAC del cliente de su **tabla ARP** (nunca del cliente), así que tiene que estar en el mismo dominio de difusión que los clientes, y el Controller solo tiene la red de gestión. Solo usa la biblioteca estándar de Python. Para consultar al Policy Mapper sale por la red de gestión (`http://<CONTROLLER_IP>:5000`) con la clave `PORTAL_TOKEN`, que `slice.py controller` genera en `R1/deploy/.env` y reparte al Policy Mapper y al host. Si ya tenías el Controller levantado, repite `python3 slice.py controller` para que el Policy Mapper la reciba.

```bash
python3 slice.py config     # elige el tipo portal para un host (h4 por defecto)
python3 slice.py portal     # IP de datos de cada host, servidor del portal (servicio r1-portal) y puertos de confianza
python3 slice.py alta       # portal reinicia el Policy Mapper: se pierden las sesiones
```
`slice.py portal` hace cuatro cosas:
1. **IP fija del portal** en la interfaz que va al switch (servicio `r1-ip-datos`): `10.0.0.60`, la de `portal.ip` en `policy.yaml`, fuera del rango DHCP. Los demás hosts toman la suya por DHCP (sección 3e).
2. **Servidor del portal** (`R2/portal_cautivo`) en `/opt/portal` del host, como servicio `r1-portal` en el puerto 8080, y un ARP gratuito para que ONOS lo ubique.
3. **Puertos de confianza.** El tráfico del portal y el que pasa de un switch al otro no tiene sesión 802.1X, así que R2 no debe aplicarle las tablas de rol. El paso lee de ONOS dónde está el portal y los enlaces entre switches, y escribe `R2/policy/policy.local.yaml` (no se versiona; `policy_tables.py` lo mezcla con `policy.yaml`): puertos `trusted` y `portal.switches`.
4. Reinicia el Policy Mapper y reinstala las tablas de rol.

**Ascenso desde un host con sesión de Alumno** (h1 autenticado como `alumno01`; el portal está en `10.0.0.60:8080`, que el rol Alumno ya puede alcanzar):
```bash
curl -s http://10.0.0.60:8080/ | grep -i "Subir de rol"                                         # el equipo puede subir
curl -s -d 'usuario=profesor01&clave=password123' http://10.0.0.60:8080/login | grep -i Docente  # ascenso: tabla 10
curl -s -X POST -d '' http://10.0.0.60:8080/logout                                                 # el equipo vuelve a Alumno (tabla 20)
```
En el Controller se ve igual: `curl -s http://127.0.0.1:5000/sessions | python3 -m json.tool` (la sesión de esa MAC es `profesor01`, Docente, con `base`: el equipo) y `sudo ovs-ofctl -O OpenFlow13 dump-flows <switch> | grep 40001` (el flujo de esa MAC hace `goto_table:10`). También se puede probar sin el servidor, contra la API (`<MAC>` es la de la interfaz de datos del host):
```bash
MAC=<MAC del host>
curl -s -X POST http://127.0.0.1:5000/portal/login -H 'Content-Type: application/json' -H "X-Portal-Token: $PORTAL_TOKEN" -d "{\"usuario\":\"profesor01\",\"clave\":\"password123\",\"mac\":\"$MAC\"}"
curl -s -X POST http://127.0.0.1:5000/portal/logout -H 'Content-Type: application/json' -H "X-Portal-Token: $PORTAL_TOKEN" -d "{\"mac\":\"$MAC\"}"
```
(`PORTAL_TOKEN` está en `R1/deploy/.env`: cárgalo con `set -a; . ~/proyecto-sdn/R1/deploy/.env; set +a`; sin definirlo en el Policy Mapper no se pide la cabecera.)

**Qué valida el ascenso.** La MAC tiene una sesión 802.1X de Alumno sin ascenso previo, el rol Alumno puede subir a Docente (`ascenso` en `policy.yaml`), el profesor no tiene otra sesión y **la ubicación coincide**: la que ONOS AAA tiene para esa MAC (`/onos/aaa/app/users`, entrada `AUTHORIZED`: switch y puerto donde recibió el EAPOL) debe ser la que el Policy Mapper guardó al admitir la sesión. Si ONOS AAA no tiene a la MAC autorizada se rechaza (`ascenso_exigir_ubicacion: true`); con `false` solo se compara cuando la tiene. Cualquier otra combinación responde 409. Explicación completa y su relación con el antispoofing en [`CERTIFICADOS_Y_ESCALABILIDAD.md`](CERTIFICADOS_Y_ESCALABILIDAD.md), sección 6.

**Invitados e IoT.** Los hosts `invitado` e `iot` no llevan agente ni certificado; solo el cliente DHCP. Sin sesión quedan en la **cuarentena** (sección 3e: reciben IP, pero solo alcanzan ARP y el portal). El invitado entra por el mismo portal con `guest01` / `guest123`: `curl -s -d 'usuario=guest01&clave=guest123' http://10.0.0.60:8080/login`; el IoT se identifica por su MAC (MAB, sección 7).

**Límites.** Es una prueba con `curl`: los hosts no tienen navegador. El portal envía la contraseña en HTTP plano (HTTPS fuera del laboratorio). Un navegador desde tu PC puede abrirlo con un túnel SSH al host del portal (`ssh -L 8090:10.0.0.60:8080 -p <puerto-del-portal> ubuntu@<IP_PUBLICA>`), pero entonces la MAC que ve el portal es la de su propia tabla ARP (la de ese host), no la de tu PC. **No se ha ejecutado todavía contra las VM**: `slice.py portal`, los playbooks y los puertos de confianza solo tienen pruebas unitarias.

## 3e. DHCP y cuarentena (R2)

**Cómo se obtiene la IP.** Cada host corre un cliente DHCP (`r1-dhcp`) desde que arranca, **antes de autenticar**:
1. El host envía un `DHCPDISCOVER` (UDP 68 → 67) por la interfaz de datos.
2. El switch lo manda al controlador con una regla de R2 en la tabla 0 (prioridad **40500**, sobre la sesión 40001; sin ella un host con sesión caería en el DROP de su tabla de rol).
3. La app `org.onosproject.dhcp` de ONOS le asigna una IP del rango y responde por el mismo puerto. También responde al ARP de la IP del servidor.
4. Mientras el host **no tenga sesión** (802.1X, portal o MAB) está en la **cuarentena**: una regla de la tabla 0 (prioridad 1000) le pone la VLAN 99 y su tabla solo deja pasar ARP y TCP al portal. Quien autentica conserva su IP; el flujo de sesión lo saca de la cuarentena hacia la tabla de su rol. Un invitado, tras iniciar sesión en el portal, pasa a la VLAN 50 (solo `university_portal`).

La regla de DHCP queda bajo la lista negra de R1 (45000) y el DROP de R5 (42000): un host bloqueado tampoco obtiene IP. El mapa de prioridades es `R5/deploy/prioridades.yaml`.

**Qué parámetros hay y quién los decide.** Los defino con un valor por defecto en `R1/deploy/.env.example` (en el Controller: `R1/deploy/.env`, líneas sin `#`) y `init-onos.sh` los aplica a ONOS. **Los que marco como tuyos conviene que los confirmes**, sobre todo el rango, el DNS y la duración:

| Parámetro (`.env`) | Por defecto | Qué es | Quién lo decide |
|---|---|---|---|
| `DHCP_INICIO` / `DHCP_FIN` | `10.0.0.100` / `10.0.0.199` | Rango único de ONOS: 100 direcciones, fuera de las IP fijas (portal `.60`, servicios `.40` y `.50`, servidor `.250`, router `.254`) | **Tuyo**: cuántos hosts, invitados e IoT esperas a la vez |
| `DHCP_LEASE` | `3600` s | Duración de una concesión | **Tuyo**: más corta libera antes las IP de invitados que se van; más larga genera menos tráfico. Conviene que no sea menor que la sesión de invitado (1800 s en `portal.duracion_s`) |
| `DHCP_RENEW` / `DHCP_REBIND` | `1800` / `3150` s | Cuándo el cliente intenta renovar (T1) y cuándo pide a cualquiera (T2): la mitad y el 87,5 % de la concesión | Mío: derivados de `DHCP_LEASE` |
| `DHCP_DNS` | `8.8.8.8` | DNS que se anuncia | **Tuyo**: el DNS del campus (el slice no tiene) |
| `DHCP_ROUTER` | `10.0.0.254` | Puerta de enlace que se anuncia | **Tuyo**: no hay router en el slice; es solo informativa y el cliente no la instala (ver abajo) |
| `DHCP_MASCARA` / `DHCP_BROADCAST` | `255.255.255.0` / `10.0.0.255` | La subred `10.0.0.0/24`, la de `policy.yaml` | Mío: sigue a `policy.yaml` |
| `DHCP_SERVIDOR_IP` / `DHCP_SERVIDOR_MAC` | `10.0.0.250` / `02:00:00:00:00:02` | Identidad del servidor DHCP. No puede ser `10.0.0.1` a `.4`: VNRT las usa en las NIC de datos de los hosts | Mío |
| `DHCP_PURGA_MIN` | `2` min | Cada cuánto ONOS libera las concesiones vencidas | Mío |
| `cuarentena.tabla` (`policy.yaml`) | `99` | VLAN/tabla de cuarentena | Mío; quitar la sección desactiva la cuarentena |
| `portal.duracion_s` (`policy.yaml`) | invitado 1800 s, el resto 8 h | Duración de la sesión del portal | **Tuyo** |

Para cambiarlos: edita `R1/deploy/.env` y ejecuta `bash R1/deploy/controller/init-onos.sh` (ONOS recoge la configuración al vuelo; las concesiones vigentes no cambian hasta renovarse). El cambio de `cuarentena.tabla` pide reiniciar el Policy Mapper.

**Límites de la app DHCP de ONOS, que condicionan el diseño:**
- **Un solo rango y unos solos tiempos para todos.** No se pueden dar rangos distintos a invitados, alumnos o IoT. Por eso los invitados no tienen "su" rango: su separación es la **VLAN de cuarentena y después la VLAN 50**, no la subred. Si hiciera falta un rango propio por clase, habría que sustituir la app por otro servidor (Kea o dnsmasq con clases) y un relé; no lo he hecho.
- La app anota en ONOS la IP y la ubicación de cada host que atiende, así que también un invitado queda ubicado (lo usa el portal para ubicar equipos sin sesión).
- No hay límite de DHCP por puerto: un host puede inundar de `DISCOVER` al controlador. Se resolvería con un *meter* en el switch; no está.
- **VNRT trae IP fijas** (`10.0.0.1`, `.2`... en `/etc/netplan/50-cloud-init.yaml`) en las NIC de datos. `dhcp.yml` saca esa interfaz de `systemd-networkd` (archivo `/etc/systemd/network/10-netplan-<iface>.network`) y vacía su dirección para que mande solo el DHCP; el portal conserva su IP fija `10.0.0.60`.
- **AppArmor** impide a `dhclient` ejecutar scripts propios (`execve ... Permission denied` en `journalctl -u r1-dhcp`): `dhcp.yml` lo permite en `/etc/apparmor.d/local/sbin.dhclient`.
- El cliente de los hosts **no instala ruta por defecto ni DNS** (solo la dirección): su interfaz de datos no es la salida del host y una ruta aquí le cortaría el SSH.
- La renovación por unicast necesita ARP de la IP del servidor, y desde un host con sesión ese ARP no llega al controlador; el cliente renueva entonces por difusión en T2 (87,5 %), que sí funciona.

**Comprobarlo:**
```bash
# en un host
ip -4 -o addr show ens4 ; journalctl -u r1-dhcp -n 20 --no-pager           # IP del rango y el DHCPACK
# en el Controller
python3 slice.py estado                                                     # sección "Concesiones DHCP"
curl -s -u onos:rocks http://127.0.0.1:8181/onos/dhcp/dhcp/mappings | python3 -m json.tool
curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/network/configuration/apps/org.onosproject.dhcp | python3 -m json.tool
# en un switch (sw2/sw3)
sudo ovs-ofctl -O OpenFlow13 dump-flows sw2 | grep -E "priority=40500|priority=1000|table=99"
```
Antes de autenticar, h1 debe tener IP y estar en `table=99` (sus paquetes cuentan en el flujo de `priority=1000`); tras `alta`, su flujo de sesión `priority=40001` sustituye a la cuarentena y la IP no cambia.

## 4. VM IDS (R5)

```bash
cd ~/proyecto-sdn/R5/deploy
cp .env.example .env && nano .env     # CONTROLLER_IP, IDS_IFACE (puerto espejo), CORE_DEVICE (deviceId de sw1), PUERTO_UPLINK, PUERTO_IDS, HOME_NET, ESPEJO_*, LISTA_BLANCA
cd ids && ./up.sh
docker compose exec integrador r5ctl estado      # también: bloqueos | alertas -n 20 | bloquear IP | desbloquear IP
```
El `deviceId` de sw1 sale de `python3 slice.py estado` (sección "Switches"). `up.sh` calcula `IDS_MAC` (la red de VNRT solo entrega al IDS tramas dirigidas a su MAC), levanta Suricata y el integrador e instala el espejo en el Core. `SURICATA_ET=1` descarga ET Open (~50 mil reglas, varios GB de RAM).

## 5. Qué puede ser cada host

`python3 slice.py config` muestra esta tabla y asigna una opción a cada host. Las **identidades de equipo** son las únicas con certificado EAP-TLS que llevan las PC (en `R1/shared/mininet/supplicants`) y representan al **equipo**, con rol Alumno; los profesores no llevan certificado.

| N | Usuario / tipo | Qué es | VLAN / tabla | Suplicante | Contraseña de la clave privada |
|---|---|---|---|---|---|
| 1 | `alumno01` | Equipo, rol Alumno | 20 | `wpa_alumno_01.conf` | `student_password` |
| 2 | `alumno02` | Equipo, rol Alumno | 20 | `wpa_alumno_02.conf` | `student_password` |
| 3 | `alumno03` | Equipo, rol Alumno | 20 | `wpa_alumno_03.conf` | `student_password` |
| 4 | `support01` | Equipo de TI, rol Soporte (Administrador) | 40 | `wpa_support_01.conf` | `support_password` |
| 5 | `portal` | Servidor del portal cautivo (sin 802.1X) | puerto de confianza | - | - |
| 6 | `invitado` | Equipo de invitado: entra por el portal con `guest01` (sin 802.1X) | 50 | - | - |
| 7 | `iot` | Dispositivo IoT, se identifica por su MAC (MAB, sin 802.1X) | 30 | - | - |

**El administrador** (`support01`) entra por 802.1X como un equipo más: asígnaselo a un host en `config` (opción 4) y ese host recibe su certificado, y solo ese; el de administrador **no se copia a las PC de aula**. Se autentica con rol Soporte, VLAN 40 (tabla 40), atado a su MAC y puerto como los alumnos (`alta` los fija). No sube por el portal: la tabla `ascenso` solo tiene `alumno: [docente]`.

Solo para probar el rol Docente por 802.1X en un host concreto (hay que **escribir el nombre**; no se ofrece por número): `profesor01`, `profesor02` (VLAN 10). El playbook copia a ese host solo el certificado de esa cuenta y quita los demás.

**Cuentas de personas** (LDAP, contraseña `password123`): `profesor01` y `profesor02` son **personales**: no llevan MAC, switch ni puerto, y suben un equipo de Alumno a Docente desde el portal (sección 3d). `support01` (soporte técnico) es la identidad del equipo de TI y sigue atado a una MAC y un puerto. `guest01` / `guest123` es el invitado del portal.

**Decisiones y límites:** las PC son de uso general, así que solo llevan el certificado de **su equipo** (rol Alumno); quien necesita más permisos sube con su cuenta personal. El rol JP (híbrido) no se usa: quien ejerce de JP entra con la cuenta de profesor. Lo que eso implica para la seguridad y la escalabilidad, y cómo encajaría el administrador, está en [`CERTIFICADOS_Y_ESCALABILIDAD.md`](CERTIFICADOS_Y_ESCALABILIDAD.md).

Una identidad solo puede tener **una sesión a la vez**: no la asignes a dos hosts. Los otros certificados no son del campus: `user@example.org` y `client.pem` son de prueba y no existen en LDAP. Para añadir una identidad nueva hace falta su entrada en el LDIF, su certificado (`regenerar.sh`), su `.conf` en `supplicants/` y una fila en `USUARIOS` dentro de `slice.py` (y en `usuarios_todos` de `hosts.yml`).

## 6. Certificados EAP-TLS

Los certificados están versionados y **caducan**: los originales eran de 60 días (`default_days = 60` de FreeRADIUS). Los actuales valen hasta **8-nov-2027**. Para regenerar la CA, el servidor y los certificados de los usuarios:

```bash
cd ~/proyecto-sdn/R1/radius/raddb/certs && ./regenerar.sh 365      # días de validez; copia también los de los suplicantes
cd ~/proyecto-sdn/R1/deploy/controller && docker compose up -d --build radius    # RADIUS usa la CA y el servidor nuevos
cd ~/proyecto-sdn/R1/deploy && python3 slice.py hosts                    # reparte los certificados nuevos a los hosts
```

## 7. Opcionales (R2)

```bash
export VM_ACCESS_PORTS="of:<deviceId-del-switch>/<puerto>"      # ver python3 slice.py estado
docker compose -f ~/proyecto-sdn/R2/mab_vm/compose.yml up -d --build      # MAB de VM (usa la red `sdn`)
curl -u onos:rocks -X POST -H "Content-Type: application/octet-stream" --data-binary @$HOME/proyecto-sdn/R2/fallback/fallback-mab-1.0.0.oar "http://127.0.0.1:8181/onos/v1/applications?activate=true"
```
El portal cautivo (`R2/portal_cautivo/`) y el panel (`R2/panel_accesos/`) están descritos en sus README.

## 8. Apagar

```bash
cd ~/proyecto-sdn/R1/deploy/controller && docker compose down
```
Para dejar un host sin nada: `ssh -p <puerto> ubuntu@<IP_PUBLICA> 'sudo systemctl disable --now r1-agente r1-eapol-bcast; sudo rm -f /etc/systemd/system/r1-*.service /usr/local/sbin/r1-eapol-bcast.sh; sudo systemctl daemon-reload; sudo tc qdisc del dev <iface> clsact; rm -rf ~/supp'`. No uses `ovs-ofctl del-flows` para limpiar flujos de ONOS: los reinstalaría o quedaría desincronizado.

## 9. Laboratorio de una sola máquina (Mininet, sin VM de switches)

```bash
docker compose -f R1/compose.yml up -d --build
docker exec -it mininet python3 /root/shared-r2/topo_baseR2_userspace.py
docker compose -f R5/lab/docker/compose.yml up -d --build            # R5: Grafana :3000, EveBox :5636, panel :8080
```
Mininet usa los datos del LDIF original (h1 con `00:00:00:00:00:11` en `of:0000000000000001`, puerto 1), distintos de los que `alta` deja para VNRT. Pruebas sin infraestructura:

```bash
pip install -r R1/policy_mapper/requirements.txt pytest requests
python3 -m pytest R1/shared/policy_mapper/tests R2/policy/tests R2/portal_cautivo/tests R2/mab_vm/test_mab_vm.py R5/tests R5/deploy/tests R1/deploy/test_slice.py R1/agente_802_1x/test_agente.py -q
```

## 10. Problemas conocidos

| Síntoma | Causa | Solución |
|---|---|---|
| `Permission denied` al lanzar un `.sh` (`up.sh`, `init-onos.sh`) | El archivo perdió el bit de ejecución al clonar desde Windows, o el repo es de otro usuario (clonado como `root`) | `chmod +x R1/deploy/controller/*.sh R5/deploy/ids/up.sh` y `sudo chown -R $USER:$USER /home/ubuntu/proyecto-sdn` |
| `permission denied ... /var/run/docker.sock` | Tu usuario no está en el grupo `docker` | `sudo usermod -aG docker $USER && newgrp docker` (o cerrar sesión y volver a entrar) |
| `Cannot connect to the Docker daemon. Is the docker daemon running?` | Docker no está instalado o el demonio no está iniciado | `sudo systemctl enable --now docker`; comprobar con `docker ps` (si falta el comando, repetir la sección 1) |
| `no space left on device` al levantar | Poco disco para las imágenes de Docker | Liberar espacio (sección 1) y repetir `python3 slice.py controller` |
| `git pull` aborta: "Your local changes ... would be overwritten" | Editaste a mano un archivo versionado (p. ej. un `inventory.ini.example`) | `git stash && git pull` (el asistente genera los inventarios por su cuenta, no hace falta editarlos) |
| `slice.py` vuelve a pedir los datos | Cambiaste de usuario (`root` ↔ `ubuntu`): `~/slice.json` es de cada uno | Trabajar siempre con el mismo usuario |
| ONOS solo tiene `drivers` y `gui2` (`apps -a -s`), SADIS y AAA `NO INSTALADA`, y los switches no conectan (`is_connected` en `false`) | `init-onos.sh` no terminó. Causa típica: sin salida a internet desde el Controller para descargar SADIS y AAA de `repo1.maven.org` (DNS, red o disco). Sin OpenFlow activo ONOS no escucha el 6653 | `cd ~/proyecto-sdn/R1/deploy/controller && bash init-onos.sh`: ahora activa OpenFlow primero y dice qué falló. Comprobar internet: `curl -I https://repo1.maven.org` y `df -h /`. `python3 slice.py controller` lo repite si faltan apps |
| `no such identity` / `Permission denied (publickey)` en Ansible | No se copió la llave | `python3 slice.py preparar` |
| Los switches muestran "controladores" como `los`, `de`, `DESDE` | Comentario al final de `controller_ip` en un inventario INI (el `#` pasa a formar parte del valor) | Usa `python3 slice.py switches`, que genera el inventario; no escribas comentarios al final de una variable |
| `is_connected` en `false` en un switch | `CONTROLLER_IP` no es la IP del Controller vista desde los switches, o falta `org.onosproject.openflow` | `nc -zv <CONTROLLER_IP> 6653` desde el switch; `ss -tlnp \| grep 6653` en el Controller; `python3 slice.py estado` |
| `EAP-FAILURE` con `bad signature` | Varias instancias de `wpa_supplicant` a la vez | `sudo pkill wpa_supplicant` en el host y repetir `alta`; con la CA caducada también falla (sección 6) |
| Rechazo "MAC no coincide" / "Puerto físico no asignado" (la MAC queda 300 s en lista negra) | LDAP no coincide con lo que ve ONOS | `python3 slice.py alta` (libera, aprende y fija) |
| Flujo instalado en `of:0000000000000en5` | Versión vieja del Policy Mapper, que dedujo la ubicación del nombre del puerto | `git pull` y `docker restart policy_mapper` (ya usa `connectPoint`) |
| `/sessions` vacío justo tras arrancar | El suplicante reintenta cada ~30 s | Esperar; luego mirar la interfaz web del host (sección 3c), `journalctl -u r1-agente` y `docker logs policy_mapper` |
| Un host ya autenticado no puede volver a autenticar ni desconectarse: la página del agente se queda en *Autenticando*, a los 60 s sale `EAP-FAILURE`, y la regla de EAPOL del switch (`ovs-ofctl ... \| grep 888e`) tiene `n_packets=0` aunque `tcpdump` en el host ve el `EAPOL start` | El flujo de sesión (prioridad 40001, `in_port` + `eth_src`) es mayor que la regla de ONOS AAA para el EAPOL (40000): captura también el EAPOL del host autenticado y lo manda a la tabla de rol, que lo descarta. El `Start` de reautenticación y el `Logoff` nunca llegan a ONOS | Corregido: el Policy Mapper instala, junto a la sesión, una regla de EAPOL del puerto a prioridad 50000 (`R5/deploy/prioridades.yaml`, `r1.eapol`). Hay que reiniciar el Policy Mapper con el código nuevo y volver a autenticar (`python3 slice.py limpiar` y `alta`): los hosts autenticados con la versión anterior no tienen esa regla. El agente además envía `wpa_cli logoff` al desconectar |
| `tc` falla al instalar el filtro del host | Faltan `cls_flower` / `act_pedit` | `sudo apt-get install -y linux-modules-extra-$(uname -r)` en ese host |

Otros avisos:
- **LDAP:** el LDIF solo se carga con `R1/shared/ldap/openldap-data` vacío. Para recargarlo: `docker compose down` y vaciar esa carpeta (salvo su README).
- **Credenciales y claves de laboratorio** (`admin`, `password123`, `testing123`, `rocks`, las claves de los certificados) están versionadas: aceptable para el curso, no fuera de él.
- **Imágenes sin versión fija** (`onos:2.7-latest`, `fedora:latest`): si algo cambia entre despliegues, fijar la versión.
- **Políticas de R2 con datos de otro slice:** `R2/policy/policy.yaml` usa `trusted` con dpid de Mininet y servicios en `10.0.0.40/50/60`. `python3 slice.py portal` ya escribe en `R2/policy/policy.local.yaml` (que se mezcla con `policy.yaml` y no se versiona) los puertos de confianza de este slice, pero los servicios (`10.0.0.40/50`) siguen siendo los de Mininet: para que un host autenticado alcance un servicio de verdad hace falta ponerlos en hosts reales del slice.
- Más detalle de la autenticación paso a paso y de cada problema: [`VALIDAR_H1_VNRT.md`](VALIDAR_H1_VNRT.md).

## Estado de validación

- **Validado en el slice original, a mano y con el playbook y `ldap-ubicacion.sh`:** `profesor01` (VLAN 10) y `alumno01` (VLAN 20) en sw2; `alumno02` (VLAN 20) y `support01` (VLAN 40) en sw3.
- **DHCP y cuarentena (sección 3e): sin probar en las VM.** Hay pruebas unitarias de las reglas (`R2/policy/tests`), de las prioridades, de la configuración de ONOS y del script de `dhclient` con un `ip` falso. No he comprobado: el nombre de la clave `dhcp` y la ruta REST `/onos/dhcp/dhcp/mappings` (las tomé del código de ONOS 2.7), que el switch mande el `DISCOVER` al controlador con la regla de 40500, que el perfil de AppArmor de `dhclient` deje ejecutar el script, ni que la cuarentena deje pasar el EAPOL.
- **Sin probar todavía:** `slice.py` (solo con 26 pruebas unitarias en `R1/deploy/test_slice.py`: inventarios, tabla de usuarios, el asistente con respuestas simuladas y las funciones del portal; no se ha ejecutado contra las VM), el playbook `portal.yml`, el servidor del portal en un host y el ascenso a Docente de extremo a extremo (el Policy Mapper sí tiene 24 pruebas en `tests/test_ascenso.py`), los certificados regenerados contra FreeRADIUS real (se comprobó que las cadenas, las claves y los usos son correctos, pero no un EAP-TLS completo), esta guía en un slice limpio, las tablas de R2 con tráfico real y el IDS con este compose.
- **Agente 802.1X (sección 3c):** probado solo con pruebas unitarias (`R1/agente_802_1x/test_agente.py`, 21 pruebas con un `wpa_supplicant` simulado y un servidor HTTP real en local) y viendo la página con datos simulados en un navegador. **No se ha ejecutado en un host de VNRT.** Lo que más depende de la VM real: que `wpa_cli status` devuelva `suppPortStatus` en el modo con cable, que el host alcance el Policy Mapper en el puerto 5000 de la red de gestión y que el EAPOL pase con el `tc`.
