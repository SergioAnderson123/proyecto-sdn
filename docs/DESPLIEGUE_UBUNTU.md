# Despliegue en un slice nuevo (Ubuntu 22.04, una VM por rol)

Runbook para reproducir el proyecto en un slice nuevo: ejecuta los comandos **en orden**. Lo que cambia entre slices (IP, puertos SSH, `deviceId`, MAC, interfaces) se define una vez en el paso 0 o se descubre solo (ONOS y `ldap-ubicacion.sh`); no hay que editar archivos a mano.

| VM | Qué corre | Carpeta |
|---|---|---|
| Controller | ONOS 2.7, OpenLDAP, Policy Mapper, FreeRADIUS; desde aquí se lanza Ansible | `R1/deploy/controller/` |
| sw1..sw3 | Open vSwitch (sin contenedores) | `R1/deploy/switches/` |
| h1..h4 | `wpa_supplicant` (suplicantes 802.1X) | `R1/deploy/hosts/` |
| IDS | Suricata 7 + integrador R5 | `R5/deploy/ids/` |

Topología de referencia: h1 y h2 cuelgan de sw2; h3 y h4, de sw3; sw1 es el core y no recibe flujos de sesión. Las VM se hablan por la REST de ONOS (8181), OpenFlow (6653) y el Policy Mapper (5000). Los pasos 1 a 4 se ejecutan en el **Controller**.

## 0. Datos del slice (una sola vez)

Reúne estos datos y guárdalos en `~/slice.env`. En **cada terminal nueva**: `source ~/slice.env`.

| Variable | Qué es | Cómo sacarla |
|---|---|---|
| `REPO` | Carpeta del repositorio en el Controller | Donde clones (paso 1) |
| `IP_PUBLICA` | IP por la que se llega por SSH a switches y hosts (con puertos distintos) | Panel del slice |
| `CONTROLLER_IP` | IP del Controller **vista desde los switches** (red de gestión) | `hostname -I` en el Controller: la de la red de gestión. Si dudas, en un switch `ip -br addr` y elige la del mismo rango |
| `PUERTOS_SW` | Puertos SSH de sw1, sw2, sw3 | Panel del slice |
| `PUERTOS_H` | Puertos SSH de h1, h2, h3, h4 | Panel del slice |
| `IFACE` | Interfaz de datos de los hosts (la que va al switch) | `ssh -p <puerto> ubuntu@$IP_PUBLICA ip -br link` |
| `USUARIOS` | Usuarios de prueba, en el mismo orden que h1..h4 | Por defecto, los del inventario |

```bash
cat > ~/slice.env <<'EOF'
export REPO=/home/ubuntu/proyecto-sdn
export IP_PUBLICA=10.20.12.78
export CONTROLLER_IP=192.168.0.10
export PUERTOS_SW="5801 5802 5803"
export PUERTOS_H="5811 5812 5813 5814"
export IFACE=ens4
export USUARIOS="profesor01 alumno01 alumno02 support01"
EOF
nano ~/slice.env          # ajusta los valores de este slice
source ~/slice.env
```

## 1. Requisitos (Controller; en el IDS solo Docker y git)

```bash
sudo apt-get update && sudo apt-get install -y git curl wget ca-certificates ansible
curl -fsSL https://get.docker.com | sudo sh        # Docker Engine + plugin compose v2
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version                              # debe ser v2.x (se usa `extends`)
git clone <URL-del-repo> "$REPO" && cd "$REPO"
```
ONOS necesita ~1-2 GB de RAM: **no** pongas ONOS y el IDS en la misma VM de 2 vCPU / 2 GB. El Controller necesita salida a internet (SADIS y AAA salen de Maven Central, y las imágenes de Docker).

Llave SSH para que Ansible entre a switches y hosts (la contraseña de `ubuntu` se pide una vez por máquina):

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
for p in $PUERTOS_SW $PUERTOS_H; do ssh-copy-id -i ~/.ssh/id_ed25519.pub -p $p ubuntu@$IP_PUBLICA; done
```

## 2. Controller: ONOS, LDAP, Policy Mapper y RADIUS

```bash
cd "$REPO"
cp R1/deploy/.env.example R1/deploy/.env
sed -i "s|^CONTROLLER_IP=.*|CONTROLLER_IP=$CONTROLLER_IP|" R1/deploy/.env
docker rm -f onos ldap policy_mapper radius 2>/dev/null; docker network rm sdn 2>/dev/null   # restos de un intento anterior
cd R1/deploy/controller && bash up.sh
```
`up.sh` levanta los 4 contenedores y ejecuta `init-onos.sh` (activa OpenFlow, instala SADIS 5.6.0 y AAA 2.7.0 y los apunta a `policy_mapper:5000` y `radius:1812`). Si `up.sh` falla a medias, ONOS queda solo con `drivers` y `gui2`: repite con `bash init-onos.sh`.

Comprobación (las 7 apps deben decir `ACTIVE`):

```bash
docker compose ps
for a in org.onosproject.openflow org.opencord.sadis org.opencord.aaa org.onosproject.dhcp org.onosproject.acl org.onosproject.vpls org.onosproject.flowanalyzer; do
  echo -n "$a: "; curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/applications/$a | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','NO INSTALADA'))"
done
curl -s http://127.0.0.1:5000/events | head -c 200
```
Puertos alcanzables: 6653/tcp (switches), 8181/tcp (IDS), 5000/tcp (IDS y portal), 1812-1813/udp (RADIUS), 1389/tcp (LDAP).

**LDAP: liberar a los usuarios de prueba.** El LDIF del repo trae la MAC, el switch y el puerto de `profesor01` de **otro slice**; con esos datos el Policy Mapper rechazaría a h1 y dejaría su MAC 300 s en lista negra. Se borran ahora y se fijan en el paso 4 con lo que ONOS vea:

```bash
cd "$REPO/R1/deploy/controller"
for u in $USUARIOS; do ./ldap-ubicacion.sh liberar $u; done
```

## 3. Switches (sw1..sw3)

Rellena el inventario con los datos del slice y lanza el playbook:

```bash
cd "$REPO/R1/deploy/switches"
cp inventory.ini.example inventory.ini
sed -i "s|ansible_host=[0-9.]*|ansible_host=$IP_PUBLICA|g; s|^controller_ip=.*|controller_ip=$CONTROLLER_IP|" inventory.ini
set -- $PUERTOS_SW; sed -i "s|ansible_port=5801|ansible_port=$1|; s|ansible_port=5802|ansible_port=$2|; s|ansible_port=5803|ansible_port=$3|" inventory.ini
ansible -i inventory.ini switches -b -m command -a "ovs-vsctl list-br" --ask-become-pass    # nombre del bridge de cada switch
grep -n "bridge=" inventory.ini        # si el nombre del bridge difiere de sw1/sw2/sw3, corrígelo aquí
ansible -i inventory.ini switches -m ping
ansible-playbook -i inventory.ini switches.yml --ask-become-pass
```
El playbook instala Open vSwitch si falta, pone fail-mode `secure` y `OpenFlow13`, apunta cada bridge a `tcp:$CONTROLLER_IP:6653` y espera a que cada switch conecte. Con `-e reset=true` borra además los flujos anteriores.

Verificación, en el Controller: los tres switches con `"available": true` (anota el `deviceId` de cada uno).

```bash
curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/devices | python3 -m json.tool | grep -E '"id"|"available"'
```
Y en un switch: `sudo ovs-vsctl --bare --columns=target,is_connected list controller` debe dar un único `tcp:<CONTROLLER_IP>:6653` con `true`.

## 4. Hosts con 802.1X (R1/R2)

Dos herramientas: el playbook de `R1/deploy/hosts/` prepara los hosts, y `ldap-ubicacion.sh` aprende de ONOS la MAC, el switch y el puerto de cada usuario (lee el `connectPoint`; no hace falta saber en qué puerto de qué switch cuelga cada host).

El playbook, en cada host: instala `wpasupplicant`, copia la CA, el certificado y el `.conf` del usuario con las rutas corregidas, deja un servicio `r1-eapol-bcast` y un servicio `r1-supplicant` que arranca **una sola** instancia. El servicio `r1-eapol-bcast` existe porque la red virtual de VNRT descarta las tramas a `01:80:c2:00:00:03` (donde `wpa_supplicant` manda el EAPOL): es una limitación de esa red, no del diseño. Reescribe el destino a broadcast al salir del host con `tc`, y ONOS lo captura igual porque filtra por `eth_type=0x888e`.

```bash
cd "$REPO/R1/deploy/hosts"
cp inventory.ini.example inventory.ini
sed -i "s|ansible_host=[0-9.]*|ansible_host=$IP_PUBLICA|g; s|iface=[a-z0-9]*|iface=$IFACE|g" inventory.ini
set -- $PUERTOS_H; sed -i "s|ansible_port=5811|ansible_port=$1|; s|ansible_port=5812|ansible_port=$2|; s|ansible_port=5813|ansible_port=$3|; s|ansible_port=5814|ansible_port=$4|" inventory.ini
ansible -i inventory.ini hosts -m ping
```
El inventario asigna h1=`profesor01`, h2=`alumno01`, h3=`alumno02` y h4=`support01`. Si en este slice son otros usuarios, cambia `conf=` y `cert=` de cada host (los archivos están en `R1/shared/mininet/supplicants/`). **No escribas comentarios al final de una variable del inventario:** en INI de Ansible el `#` pasa a formar parte del valor.

**Terminales de apoyo (opcional), para ver lo que ocurre:**
```bash
docker logs -f --tail 5 policy_mapper                                                          # A: APROBADO / RECHAZO y el motivo
docker logs -f --tail 0 radius 2>&1 | grep --line-buffered -E "Access-Accept|Access-Reject|Error"   # B
watch -n 3 'docker exec ldap ldapsearch -x -H ldap://localhost -D cn=admin,dc=example,dc=org -w admin -LLL -b ou=users,dc=example,dc=org "(objectClass=inetOrgPerson)" uid description physicalDeliveryOfficeName roomNumber | grep -v "^$"'   # C
```

**Primera vuelta: preparar y autenticar a los cuatro con el LDAP "liberado".**
```bash
cd "$REPO/R1/deploy/hosts"
ansible-playbook -i inventory.ini hosts.yml --ask-become-pass -e arrancar=true
sleep 60                         # EAP-TLS + reintentos del suplicante (~30 s)
curl -s http://127.0.0.1:5000/sessions | python3 -m json.tool          # esperadas: 4 sesiones
```

**Fijar la ubicación de cada usuario desde lo que vio ONOS, y repetir con la comprobación activa:**
```bash
cd "$REPO/R1/deploy/controller"
for u in $USUARIOS; do ./ldap-ubicacion.sh fijar $u; done      # copia MAC, switch y puerto de ONOS a LDAP
docker restart policy_mapper                                    # las sesiones están en memoria
cd "$REPO/R1/deploy/hosts"
ansible-playbook -i inventory.ini hosts.yml --ask-become-pass -e arrancar=true
sleep 60
curl -s http://127.0.0.1:5000/sessions | python3 -m json.tool
```

**Qué esperar:** una sesión por usuario con VLAN 10 (docente), 20 (alumno), 20 (alumno) y 40 (soporte) y como `dpid` el **switch de acceso** del host (nunca el core). Un puerto distinto de 1 o 2 es normal: el puerto es el de ONOS y el orden de los puertos cambia entre switches. Comprueba los flujos de sesión en cada switch:

```bash
for p in $PUERTOS_SW; do echo "== puerto SSH $p"; ssh -p $p ubuntu@$IP_PUBLICA 'sudo ovs-ofctl -O OpenFlow13 dump-flows $(sudo ovs-vsctl list-br | head -1) | grep 40001'; done
cd "$REPO/R1/deploy/controller"; for u in $USUARIOS; do ./ldap-ubicacion.sh ver $u; done     # LDAP frente a lo que ve ONOS
```
Cada flujo debe tener `in_port=<puerto>,dl_src=<MAC del host>` y `goto_table:10/20/40` según el rol.

Parar el suplicante de un host: `ansible-playbook -i inventory.ini hosts.yml --ask-become-pass --limit h2 -e parar=true`. Para dar de alta **un host nuevo** aislado: `./ldap-ubicacion.sh liberar <uid>` → playbook con `--limit <host> -e arrancar=true` → `./ldap-ubicacion.sh fijar <uid>` → `docker restart policy_mapper` → playbook otra vez. Con `liberar`, el usuario queda sin comprobación de MAC ni ubicación hasta el `fijar`.

Más detalle y tabla de problemas: [`VALIDAR_H1_VNRT.md`](VALIDAR_H1_VNRT.md).

## 5. VM IDS (R5)

```bash
cd "$REPO"
cp R5/deploy/.env.example R5/deploy/.env
nano R5/deploy/.env     # CONTROLLER_IP, IDS_IFACE (interfaz del puerto espejo), CORE_DEVICE (deviceId de sw1 en ONOS),
                        # PUERTO_UPLINK, PUERTO_IDS, HOME_NET, ESPEJO_HOME_NET, ESPEJO_RED_EXTERNA, LISTA_BLANCA
cd R5/deploy/ids && ./up.sh
docker compose exec integrador r5ctl estado      # también: bloqueos | alertas -n 20 | bloquear IP | desbloquear IP
```
`up.sh` calcula `IDS_MAC` (la red de VNRT solo entrega al IDS tramas dirigidas a su MAC), levanta Suricata e integrador e instala el espejo en el Core. `SURICATA_ET=1` descarga ET Open (~50 mil reglas, varios GB de RAM); por defecto solo `R5/suricata/local.rules`.

## 6. Opcionales (R2)

```bash
# MAB de VM (R2/mab_vm): usa la red `sdn` que crea el compose del Controller.
export VM_ACCESS_PORTS="of:<deviceId-del-switch>/<puerto>"      # ver GET /onos/v1/devices
docker compose -f "$REPO/R2/mab_vm/compose.yml" up -d --build
# app de fallback de ONOS (no se instala sola)
curl -u onos:rocks -X POST -H "Content-Type: application/octet-stream" \
  --data-binary @"$REPO/R2/fallback/fallback-mab-1.0.0.oar" "http://127.0.0.1:8181/onos/v1/applications?activate=true"
```
El portal cautivo (`R2/portal_cautivo/`) y el panel (`R2/panel_accesos/`) están descritos en sus README.

## 7. Repetir la prueba y apagar

```bash
# repetir desde cero (Controller)
curl -s -u onos:rocks -X DELETE http://127.0.0.1:8181/onos/v1/flows/application/org.onosproject.rest   # sesiones y tablas de rol
docker restart policy_mapper
curl -s -X POST http://127.0.0.1:5000/policy/sync               # reinstala las tablas de rol
# apagar
cd "$REPO/R1/deploy/controller" && docker compose down
```
No uses `ovs-ofctl del-flows` para limpiar flujos de ONOS: los reinstalaría o quedaría desincronizado. Para dejar un host sin nada: `ssh -p <puerto> ubuntu@$IP_PUBLICA 'sudo systemctl disable --now r1-supplicant r1-eapol-bcast; sudo rm -f /etc/systemd/system/r1-*.service /usr/local/sbin/r1-eapol-bcast.sh; sudo systemctl daemon-reload; sudo tc qdisc del dev '$IFACE' clsact 2>/dev/null; rm -rf ~/supp'`.

## 8. Laboratorio de una sola máquina (sin VM de switches)

```bash
docker compose -f R1/compose.yml up -d --build                      # ONOS, Mininet, LDAP, Policy Mapper, RADIUS
docker exec -it mininet python3 /root/shared-r2/topo_baseR2_userspace.py
docker compose -f R5/lab/docker/compose.yml up -d --build            # R5: Grafana :3000, EveBox :5636, panel :8080
```
Mininet usa los datos del LDIF original (h1 con `00:00:00:00:00:11` en `of:0000000000000001`, puerto 1), distintos de los que `liberar`/`fijar` dejan para VNRT. Pruebas sin infraestructura:

```bash
pip install -r R1/policy_mapper/requirements.txt pytest requests
python3 -m pytest R1/shared/policy_mapper/tests R2/policy/tests R2/portal_cautivo/tests R2/mab_vm/test_mab_vm.py R5/tests R5/deploy/tests -q
```

## 9. Problemas conocidos

| Síntoma | Causa | Solución |
|---|---|---|
| `./up.sh: Permission denied` | El `.sh` perdió el bit de ejecución al clonar desde Windows | Usar `bash up.sh` |
| `Cannot connect to the Docker daemon` | Docker no está instalado o parado | `sudo systemctl enable --now docker` (o instalarlo, paso 1) |
| `container name "/onos" is already in use` | Restos de otro despliegue | `docker rm -f onos ldap policy_mapper radius; docker network rm sdn` |
| ONOS solo tiene `drivers` y `gui2` | `init-onos.sh` no llegó a ejecutarse | `bash R1/deploy/controller/init-onos.sh` |
| `no such identity` / `Permission denied (publickey)` en Ansible | Falta la llave SSH o no se copió | `ssh-keygen` y `ssh-copy-id` (paso 1) |
| `Missing sudo password` | `ubuntu` pide contraseña para `sudo` | `--ask-become-pass` |
| Los switches muestran "controladores" como `los`, `de`, `DESDE` | Comentario al final de `controller_ip` en el inventario INI | Quitar el comentario y repetir el playbook |
| `is_connected` en `false` | `CONTROLLER_IP` no es la IP del Controller vista desde los switches, o falta `org.onosproject.openflow` | `nc -zv $CONTROLLER_IP 6653` desde el switch; `ss -tlnp \| grep 6653` y apps activas en el Controller |
| h1 manda EAPOL pero no llega al switch | La red virtual descarta `01:80:c2:00:00:03` | Lo resuelve `r1-eapol-bcast` (si `tc` falla: `sudo apt-get install -y linux-modules-extra-$(uname -r)` en ese host) |
| `EAP-FAILURE` con `bad signature` | Varias instancias de `wpa_supplicant` a la vez | `sudo pkill wpa_supplicant` en el host y lanzar una sola |
| Rechazo "MAC no coincide" o "Puerto físico no asignado" (y MAC 300 s en lista negra) | LDAP no coincide con lo que ve ONOS | `./ldap-ubicacion.sh liberar <uid>`, autenticar, `fijar` |
| El flujo se instala en `of:0000000000000en5` | Versión vieja del Policy Mapper, que dedujo la ubicación del nombre del puerto | `git pull` y `docker restart policy_mapper` (ya usa `connectPoint`) |
| `/sessions` vacío justo tras arrancar | El suplicante tarda hasta ~60 s en reintentar | Esperar y volver a mirar; luego `journalctl -u r1-supplicant` en el host y `docker logs policy_mapper` |

Otros avisos:
- **LDAP:** el LDIF de `R1/shared/ldap/ldif/custom` solo se carga con `shared/ldap/openldap-data` vacío. Para recargarlo: `docker compose down` y vaciar esa carpeta (salvo su README).
- **Certificados EAP-TLS** (`R1/radius/raddb/certs` y `R1/shared/mininet/supplicants/certs`) y **credenciales de laboratorio** (`admin`, `password123`, `testing123`, `rocks`) están versionados: aceptable para el curso, no fuera de él. Para regenerar los certificados, `make` en esa carpeta y copiar los nuevos a los suplicantes.
- **Imágenes sin versión fija** (`onos:2.7-latest`, `fedora:latest`): si algo cambia entre despliegues, fijar la versión.
- **Políticas de R2 con datos de otro slice:** `R2/policy/policy.yaml` usa `trusted` con los dpid de Mininet y servicios en `10.0.0.40/50/60`. Para que el tráfico de un host autenticado salga de verdad hay que poner los dpid reales, el puerto troncal como de confianza y los servicios en hosts reales del slice.

## Estado de validación

- **Validado en el slice original, con el playbook y `ldap-ubicacion.sh`, con los cuatro hosts:** `profesor01` (VLAN 10) y `alumno01` (VLAN 20) en sw2 `of:00001a749039894a`; `alumno02` (VLAN 20) y `support01` (VLAN 40) en sw3 `of:0000ca272d265744`. El Policy Mapper aprueba cada uno e instala su flujo de sesión (prioridad 40001) en su switch de acceso.
- Sin probar: esta guía de principio a fin en un slice limpio, las tablas de R2 con tráfico real de los hosts autenticados y el IDS con este compose.
