# Despliegue en un slice nuevo (Ubuntu 22.04, una VM por rol)

Todo se lanza desde la **VM Controller** con un asistente en Python, `R1/deploy/slice.py` (solo biblioteca estándar; Python 3.10 viene en Ubuntu 22.04). Va pidiendo los datos que cambian entre slices (IP, puertos SSH, interfaz, usuarios de cada host); el resto (switches, MAC, puertos, `deviceId`) se descubre solo.

| VM | Qué corre | Lo gestiona |
|---|---|---|
| Controller | ONOS 2.7, OpenLDAP, Policy Mapper, FreeRADIUS (y Ansible) | `slice.py controller` |
| sw1..sw3 | Open vSwitch (sin contenedores) | `slice.py switches` |
| h1..h4 | `wpa_supplicant` (suplicantes 802.1X) | `slice.py hosts` y `slice.py alta` |
| IDS | Suricata 7 + integrador R5 | `R5/deploy/ids/` (sección 4) |

Topología de referencia: h1 y h2 cuelgan de sw2; h3 y h4, de sw3; sw1 es el core y no recibe flujos de sesión.

## 1. Preparar el Controller (una vez)

```bash
sudo apt-get update && sudo apt-get install -y git curl wget ca-certificates
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker "$USER" && newgrp docker
git clone <URL-del-repo> ~/proyecto-sdn && cd ~/proyecto-sdn/R1/deploy
```
ONOS necesita ~1-2 GB de RAM: no lo pongas junto al IDS en una VM de 2 vCPU / 2 GB. El Controller necesita internet (SADIS y AAA salen de Maven Central, y las imágenes de Docker).

## 2. Desplegar y validar (lo normal)

```bash
python3 slice.py             # menú interactivo: elige un paso por número y va pidiendo lo que falte
```
El menú tiene las mismas opciones que los comandos de abajo; sin argumentos, o con un paso, así:

```bash
python3 slice.py config      # pregunta IP pública, IP del Controller, cuántos switches y hosts, puertos SSH, interfaz y usuario de cada host; guarda ~/slice.json
python3 slice.py preparar    # instala Ansible y copia tu llave SSH a switches y hosts (pide sus contraseñas una vez)
python3 slice.py todo        # controller + switches + hosts + alta (pide una vez la contraseña de sudo de ubuntu)
python3 slice.py estado      # resumen: apps, switches, sesiones, flujos y LDAP
```
En `config`, el asistente enseña la tabla de usuarios de la sección 5 y para cada host pregunta el **puerto SSH** y el **usuario** (por número o por nombre); avisa si repites un usuario en dos hosts (solo puede tener una sesión a la vez). Al final de `estado` debes ver una sesión por host y, por switch de acceso, un flujo de sesión por host. Con el reparto por defecto (el ya validado):

| Host | Usuario | Rol / VLAN | Switch de acceso |
|---|---|---|---|
| h1 | `profesor01` | Docente / 10 | sw2 |
| h2 | `alumno01` | Alumno / 20 | sw2 |
| h3 | `alumno02` | Alumno / 20 | sw3 |
| h4 | `support01` | Soporte / 40 | sw3 |

Para repetir la prueba desde cero: `python3 slice.py limpiar` y luego `python3 slice.py alta`.

## 3. Qué hace cada paso

| Comando | Qué hace |
|---|---|
| `config` | Pregunta los datos del slice (Enter acepta el valor por defecto, que es el de la configuración anterior si existe) y los guarda en `~/slice.json`. Los puertos de switches y hosts son los SSH |
| `preparar` | Instala Ansible, crea `~/.ssh/id_ed25519` y la copia con `ssh-copy-id` a cada switch y host |
| `controller` | Rellena `R1/deploy/.env`, quita contenedores de otro despliegue con el mismo nombre, ejecuta `controller/up.sh` (ONOS, LDAP, Policy Mapper, RADIUS e `init-onos.sh`) y comprueba que las 7 apps de ONOS estén `ACTIVE`; si falta alguna, repite `init-onos.sh` |
| `switches` | Genera el inventario y lanza el playbook `R1/deploy/switches/switches.yml`: instala Open vSwitch si falta, fail-mode `secure`, `OpenFlow13` y controlador `tcp:<CONTROLLER_IP>:6653`, y espera a que cada switch conecte |
| `hosts` | Genera el inventario y lanza `R1/deploy/hosts/hosts.yml`: instala `wpasupplicant`, copia la CA, el certificado y el `.conf` del usuario, y deja los servicios `r1-eapol-bcast` y `r1-supplicant`. No arranca el suplicante |
| `alta` | (1) Libera en LDAP la MAC, el switch y el puerto de los usuarios; (2) arranca los suplicantes; (3) espera a que ONOS vea las autenticaciones y **fija en LDAP lo que ONOS vio**; (4) los reautentica ya con la comprobación de MAC y ubicación activa |
| `estado` / `limpiar` | Resumen del estado / borra sesiones y flujos de R2 (sin tocar los flujos base de ONOS) y reinstala las tablas de rol |

Dos detalles de `alta`:
- **Por qué se libera primero.** El LDIF del repo (`R1/shared/ldap/ldif/custom/estructura.ldif`) trae la MAC, el switch y el puerto de `profesor01` de otro slice. Con datos que no coinciden, el Policy Mapper rechaza y deja la MAC 300 s en lista negra. Mientras un usuario está "liberado" no se comprueba su MAC ni su ubicación: no lo dejes así en una prueba de seguridad (el segundo paso de `alta` ya la reactiva).
- **Por qué existe `r1-eapol-bcast`.** La red virtual de VNRT descarta las tramas a `01:80:c2:00:00:03`, donde `wpa_supplicant` manda el EAPOL, antes de que lleguen al switch. Es una limitación de esa red, no del diseño. El servicio cambia el destino a broadcast al salir del host (`tc`), y ONOS lo captura igual porque filtra por `eth_type=0x888e`. Pedir al personal de VNRT que permita esa dirección lo haría innecesario.

## 4. VM IDS (R5)

```bash
cd ~/proyecto-sdn/R5/deploy
cp .env.example .env && nano .env     # CONTROLLER_IP, IDS_IFACE (puerto espejo), CORE_DEVICE (deviceId de sw1), PUERTO_UPLINK, PUERTO_IDS, HOME_NET, ESPEJO_*, LISTA_BLANCA
cd ids && ./up.sh
docker compose exec integrador r5ctl estado      # también: bloqueos | alertas -n 20 | bloquear IP | desbloquear IP
```
El `deviceId` de sw1 sale de `python3 slice.py estado` (sección "Switches"). `up.sh` calcula `IDS_MAC` (la red de VNRT solo entrega al IDS tramas dirigidas a su MAC), levanta Suricata y el integrador e instala el espejo en el Core. `SURICATA_ET=1` descarga ET Open (~50 mil reglas, varios GB de RAM).

## 5. Usuarios que puedes configurar

Son los usuarios con certificado EAP-TLS (en `R1/shared/mininet/supplicants`) y con cuenta en LDAP. `python3 slice.py config` los muestra y los asigna a los hosts; puedes usar los seis, uno por host:

| N | Usuario | Rol | VLAN / tabla | Suplicante | Contraseña de la clave privada |
|---|---|---|---|---|---|
| 1 | `profesor01` | Docente | 10 | `wpa_profe_01.conf` | `professor_password` |
| 2 | `profesor02` | Docente | 10 | `wpa_profe_02.conf` | `professor_password` |
| 3 | `alumno01` | Alumno | 20 | `wpa_alumno_01.conf` | `student_password` |
| 4 | `alumno02` | Alumno | 20 | `wpa_alumno_02.conf` | `student_password` |
| 5 | `alumno03` | Alumno | 20 | `wpa_alumno_03.conf` | `student_password` |
| 6 | `support01` | Soporte (Administrador) | 40 | `wpa_support_01.conf` | `support_password` |

Un usuario solo puede tener **una sesión a la vez**: no lo asignes a dos hosts. Los otros certificados no son del campus: `user@example.org` y `client.pem` son de prueba y no existen en LDAP. `guest01` (invitado, clave `guest123`) entra por el portal cautivo con usuario y contraseña, no por certificado. Para añadir un usuario nuevo hace falta su entrada en el LDIF, su certificado (`regenerar.sh`), su `.conf` en `supplicants/` y una fila en `USUARIOS` dentro de `slice.py`.

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
Para dejar un host sin nada: `ssh -p <puerto> ubuntu@<IP_PUBLICA> 'sudo systemctl disable --now r1-supplicant r1-eapol-bcast; sudo rm -f /etc/systemd/system/r1-*.service /usr/local/sbin/r1-eapol-bcast.sh; sudo systemctl daemon-reload; sudo tc qdisc del dev <iface> clsact; rm -rf ~/supp'`. No uses `ovs-ofctl del-flows` para limpiar flujos de ONOS: los reinstalaría o quedaría desincronizado.

## 9. Laboratorio de una sola máquina (Mininet, sin VM de switches)

```bash
docker compose -f R1/compose.yml up -d --build
docker exec -it mininet python3 /root/shared-r2/topo_baseR2_userspace.py
docker compose -f R5/lab/docker/compose.yml up -d --build            # R5: Grafana :3000, EveBox :5636, panel :8080
```
Mininet usa los datos del LDIF original (h1 con `00:00:00:00:00:11` en `of:0000000000000001`, puerto 1), distintos de los que `alta` deja para VNRT. Pruebas sin infraestructura:

```bash
pip install -r R1/policy_mapper/requirements.txt pytest requests
python3 -m pytest R1/shared/policy_mapper/tests R2/policy/tests R2/portal_cautivo/tests R2/mab_vm/test_mab_vm.py R5/tests R5/deploy/tests R1/deploy/test_slice.py -q
```

## 10. Problemas conocidos

| Síntoma | Causa | Solución |
|---|---|---|
| `Permission denied` al lanzar un `.sh` | El archivo perdió el bit de ejecución al clonar desde Windows | `bash <script>` o `chmod +x` |
| `Cannot connect to the Docker daemon` | Docker no está instalado o parado | `sudo systemctl enable --now docker` (o repetir la sección 1) |
| ONOS solo tiene `drivers` y `gui2` | `init-onos.sh` no llegó a ejecutarse | `python3 slice.py controller` lo repite si faltan apps |
| `no such identity` / `Permission denied (publickey)` en Ansible | No se copió la llave | `python3 slice.py preparar` |
| Los switches muestran "controladores" como `los`, `de`, `DESDE` | Comentario al final de `controller_ip` en un inventario INI (el `#` pasa a formar parte del valor) | Usa `python3 slice.py switches`, que genera el inventario; no escribas comentarios al final de una variable |
| `is_connected` en `false` en un switch | `CONTROLLER_IP` no es la IP del Controller vista desde los switches, o falta `org.onosproject.openflow` | `nc -zv <CONTROLLER_IP> 6653` desde el switch; `ss -tlnp \| grep 6653` en el Controller; `python3 slice.py estado` |
| `EAP-FAILURE` con `bad signature` | Varias instancias de `wpa_supplicant` a la vez | `sudo pkill wpa_supplicant` en el host y repetir `alta`; con la CA caducada también falla (sección 6) |
| Rechazo "MAC no coincide" / "Puerto físico no asignado" (la MAC queda 300 s en lista negra) | LDAP no coincide con lo que ve ONOS | `python3 slice.py alta` (libera, aprende y fija) |
| Flujo instalado en `of:0000000000000en5` | Versión vieja del Policy Mapper, que dedujo la ubicación del nombre del puerto | `git pull` y `docker restart policy_mapper` (ya usa `connectPoint`) |
| `/sessions` vacío justo tras arrancar | El suplicante reintenta cada ~30 s | Esperar; luego `journalctl -u r1-supplicant` en el host y `docker logs policy_mapper` |
| `tc` falla al instalar el filtro del host | Faltan `cls_flower` / `act_pedit` | `sudo apt-get install -y linux-modules-extra-$(uname -r)` en ese host |

Otros avisos:
- **LDAP:** el LDIF solo se carga con `R1/shared/ldap/openldap-data` vacío. Para recargarlo: `docker compose down` y vaciar esa carpeta (salvo su README).
- **Credenciales y claves de laboratorio** (`admin`, `password123`, `testing123`, `rocks`, las claves de los certificados) están versionadas: aceptable para el curso, no fuera de él.
- **Imágenes sin versión fija** (`onos:2.7-latest`, `fedora:latest`): si algo cambia entre despliegues, fijar la versión.
- **Políticas de R2 con datos de otro slice:** `R2/policy/policy.yaml` usa `trusted` con dpid de Mininet y servicios en `10.0.0.40/50/60`. Para que el tráfico de un host autenticado salga de verdad hay que poner los dpid reales, el puerto troncal como de confianza y los servicios en hosts reales del slice.
- Más detalle de la autenticación paso a paso y de cada problema: [`VALIDAR_H1_VNRT.md`](VALIDAR_H1_VNRT.md).

## Estado de validación

- **Validado en el slice original, a mano y con el playbook y `ldap-ubicacion.sh`:** `profesor01` (VLAN 10) y `alumno01` (VLAN 20) en sw2; `alumno02` (VLAN 20) y `support01` (VLAN 40) en sw3.
- **Sin probar todavía:** `slice.py` (solo con 7 pruebas unitarias en `R1/deploy/test_slice.py`: inventarios, tabla de usuarios y el asistente con respuestas simuladas; no se ha ejecutado contra las VM), los certificados regenerados contra FreeRADIUS real (se comprobó que las cadenas, las claves y los usos son correctos, pero no un EAP-TLS completo), esta guía en un slice limpio, las tablas de R2 con tráfico real y el IDS con este compose.
