# Despliegue en Ubuntu 22.04 (una VM por rol)

Guía para reproducir el proyecto en instancias nuevas. Un repositorio, un despliegue por VM:

| VM | Qué corre | Carpeta |
|---|---|---|
| Controller | ONOS 2.7, OpenLDAP, Policy Mapper, FreeRADIUS (y opcionalmente MAB de VM y portal) | `R1/deploy/controller/` |
| sw1..sw3 | Open vSwitch (sin contenedores) | — |
| IDS | Suricata 7 + integrador R5 | `R5/deploy/ids/` |

Las VM se hablan por la API REST de ONOS (8181), OpenFlow (6653) y el Policy Mapper (5000). Las IP y deviceId del `.env.example` corresponden al slice de VNRT: **ajústalos** a tu red.

## 0. Requisitos en cada VM (Controller e IDS)

```bash
sudo apt-get update && sudo apt-get install -y git curl wget ca-certificates
curl -fsSL https://get.docker.com | sudo sh        # Docker Engine + plugin compose v2
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version                              # debe ser v2.x (se usa `extends`)
git clone <URL-del-repo> proyecto-sdn && cd proyecto-sdn
```

Recursos: ONOS necesita ~1-2 GB de RAM. **No** pongas ONOS y el IDS en la misma VM de 2 vCPU / 2 GB. El Controller necesita salida a internet (descarga SADIS y AAA de Maven Central y las imágenes).

## 1. VM Controller

```bash
cp R1/deploy/.env.example R1/deploy/.env
nano R1/deploy/.env            # CONTROLLER_IP = IP de gestión de esta VM; ONOS_USER/ONOS_PASS
cd R1/deploy/controller && ./up.sh
```

`up.sh` levanta los 4 contenedores y `init-onos.sh` activa OpenFlow, instala SADIS 5.6.0 y AAA 2.7.0 y los apunta a `policy_mapper:5000` y `radius:1812`.

Comprobación:

```bash
docker compose ps
curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/applications | head -c 300
curl -s http://127.0.0.1:5000/events | head -c 200
```

Puertos que deben ser alcanzables: 6653/tcp (switches), 8181/tcp (IDS), 5000/tcp (IDS y portal), 1812-1813/udp (RADIUS), 1389/tcp (LDAP).

### Opcionales (R2)

```bash
# MAB de VM (R2/mab_vm): usa la red `sdn` que crea el compose del Controller.
export VM_ACCESS_PORTS="of:<deviceId-del-switch>/<puerto>"   # tu deviceId; ver GET /onos/v1/devices
docker compose -f R2/mab_vm/compose.yml up -d --build
```

La app de fallback de ONOS (`R2/fallback/fallback-mab-1.0.0.oar`) **no** se instala automáticamente:

```bash
curl -u onos:rocks -X POST -H "Content-Type: application/octet-stream" \
  --data-binary @R2/fallback/fallback-mab-1.0.0.oar \
  "http://127.0.0.1:8181/onos/v1/applications?activate=true"
```

El portal cautivo (`R2/portal_cautivo/`) y el panel (`R2/panel_accesos/`) están descritos en sus README.

## 2. Switches (sw1..sw3)

### Opción A: con Ansible (recomendada)

El playbook está en `R1/deploy/switches/`. Se lanza desde el Controller o desde tu PC, con acceso SSH a los switches:

```bash
sudo apt-get install -y ansible
cd R1/deploy/switches
cp inventory.ini.example inventory.ini
nano inventory.ini        # ansible_host/ansible_port y bridge de cada switch; controller_ip = IP del Controller vista desde los switches
```

Antes del primer `ping`, crea una llave SSH y cópiala a cada switch (el inventario trae `~/.ssh/id_rsa` por defecto, que normalmente no existe; si la llave no existe, Ansible falla con `no such identity` y `Permission denied (publickey,password)`):

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
for p in 5801 5802 5803; do ssh-copy-id -i ~/.ssh/id_ed25519.pub -p $p ubuntu@<IP-de-los-switches>; done   # pide la contraseña de ubuntu una vez por switch
sed -i 's|^ansible_ssh_private_key_file=.*|ansible_ssh_private_key_file=/root/.ssh/id_ed25519|' inventory.ini   # ruta absoluta: usa /home/<usuario>/... si no eres root
grep -n private_key inventory.ini        # debe mostrar la línea; si no, añádela bajo [switches:vars]
echo 'ansible_ssh_common_args=-o StrictHostKeyChecking=accept-new' >> inventory.ini   # opcional: no pregunta por las huellas (bajo [switches:vars], al final del archivo)
ssh -i ~/.ssh/id_ed25519 -p 5801 ubuntu@<IP-de-los-switches> hostname   # debe responder sw1
```

Si cambias la IP del inventario, repite `ssh-copy-id` con la IP nueva. Alternativa sin llaves: `sudo apt-get install -y sshpass`, borrar la línea `ansible_ssh_private_key_file` y usar `--ask-pass --ask-become-pass` en cada comando de Ansible.

```bash
ansible -i inventory.ini switches -m ping
ansible-playbook -i inventory.ini switches.yml --ask-become-pass                 # aplicar
ansible-playbook -i inventory.ini switches.yml --ask-become-pass -e reset=true   # además borra los flujos anteriores
```

`--ask-become-pass` es necesario si `ubuntu` pide contraseña para `sudo` en los switches (si no, falla con `Missing sudo password`). Alternativas: `ansible_become_password=...` bajo `[switches:vars]` del inventario (texto plano; no lo compartas) o `sudo` sin contraseña en cada switch.

Verificar la conexión con ONOS. En cada switch debe salir `tcp:<controller_ip>:6653` con `is_connected` en `true` (la tabla Controller se consulta por `target`, no por nombre de bridge):

```bash
sudo ovs-vsctl --bare --columns=target,is_connected list controller
sudo ovs-vsctl show | grep -A2 Controller
```

En el Controller, los tres deben aparecer con `"available": true`:

```bash
curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/devices | python3 -m json.tool | grep -E '"id"|"available"'
```

No pongas comentarios al final de una variable del inventario (`controller_ip=1.2.3.4   # texto`): en INI de Ansible el `#` forma parte del valor y `set-controller` crea un controlador por cada palabra. Si en `list controller` aparecen targets raros (`los`, `de`, `DESDE`...), quita el comentario en `inventory.ini` y vuelve a lanzar el playbook (reemplaza los controladores). Para limpiarlos a mano: `sudo ovs-vsctl del-controller <bridge>`.

Si `is_connected` es `false`: desde el switch prueba `nc -zv <controller_ip> 6653` y en el Controller `ss -tlnp | grep 6653` y `docker ps | grep onos`. Lo habitual es que `controller_ip` no sea la IP del Controller vista desde los switches.

El playbook instala Open vSwitch si falta, pone fail-mode `secure` y `OpenFlow13`, apunta cada bridge a `tcp:<controller_ip>:6653` y espera a que el switch quede conectado (`is_connected=true`). Comprueba el nombre del bridge de cada switch con `sudo ovs-vsctl list-br`. `inventory.ini` no se versiona.

### Opción B: a mano, en cada switch

```bash
sudo ovs-vsctl set-fail-mode <br> secure
sudo ovs-vsctl set bridge <br> protocols=OpenFlow13
sudo ovs-vsctl set-controller <br> tcp:<CONTROLLER_IP>:6653
```

Obtén el deviceId del Core en el Controller y anótalo para el IDS:

```bash
curl -s -u onos:rocks http://<CONTROLLER_IP>:8181/onos/v1/devices | python3 -m json.tool | grep '"id"'
```

## 3. VM IDS

```bash
cp R5/deploy/.env.example R5/deploy/.env
nano R5/deploy/.env     # CONTROLLER_IP, IDS_IFACE (interfaz del puerto espejo), CORE_DEVICE,
                        # PUERTO_UPLINK, PUERTO_IDS, HOME_NET, ESPEJO_HOME_NET, ESPEJO_RED_EXTERNA, LISTA_BLANCA
cd R5/deploy/ids && ./up.sh
```

`up.sh` calcula `IDS_MAC` (la red de VNRT solo entrega al IDS tramas dirigidas a su MAC), levanta Suricata e integrador e instala el espejo en el Core. Operación posterior:

```bash
docker compose exec integrador r5ctl estado      # también: bloqueos | alertas -n 20 | bloquear IP | desbloquear IP
```

`SURICATA_ET=1` descarga ET Open (~50 mil reglas, varios GB de RAM); por defecto solo `R5/suricata/local.rules`.

## 3b. Hosts con 802.1X (R1/R2)

Para autenticar hosts (h1 a h4) con `wpa_supplicant` y ver los flujos en su switch de acceso: [`VALIDAR_H1_VNRT.md`](VALIDAR_H1_VNRT.md). Playbook de hosts en `R1/deploy/hosts/` y script de LDAP en `R1/deploy/controller/ldap-ubicacion.sh`. Si `up.sh` falla a medias, ejecuta `bash R1/deploy/controller/init-onos.sh` (sin él ONOS solo tiene `drivers` y `gui2`).

## 4. Laboratorio de una sola máquina (sin VM de switches)

```bash
docker compose -f R1/compose.yml up -d --build                      # ONOS, Mininet, LDAP, Policy Mapper, RADIUS
docker exec -it mininet python3 /root/shared-r2/topo_baseR2_userspace.py
docker compose -f R5/lab/docker/compose.yml up -d --build            # R5: Grafana :3000, EveBox :5636, panel :8080
```

Pruebas sin infraestructura:

```bash
pip install -r R1/policy_mapper/requirements.txt pytest requests
python3 -m pytest R1/shared/policy_mapper/tests R2/policy/tests R2/portal_cautivo/tests R2/mab_vm/test_mab_vm.py R5/tests R5/deploy/tests -q
```

## 5. Problemas conocidos al migrar

- **Datos del slice de VNRT:** `CORE_DEVICE`, `IDS_MAC`, `HOME_NET=10.0.0.0/30` y las IP de `.env.example` y `R5/lab/vnrt/r5.env` son de tu entorno original.
- **LDAP:** el LDIF de `R1/shared/ldap/ldif/custom` solo se carga con `shared/ldap/openldap-data` vacío. Para recargarlo: `docker compose down` y vaciar esa carpeta (salvo su README).
- **Certificados EAP-TLS** de `R1/radius/raddb/certs` y los de los supplicants están versionados con claves de laboratorio. Para algo expuesto, regenerarlos (`make` en esa carpeta) y copiar los nuevos a `R1/shared/mininet/supplicants/certs`.
- **Credenciales de laboratorio** (`admin`, `password123`, `testing123`, `rocks`): cambiarlas fuera de un entorno de curso.
- **Imágenes sin versión fija** (`onos:2.7-latest`, `fedora:latest`): si algo cambia entre despliegues, fijar la versión.
- **No validado aún en VNRT:** el stack del Controller completo, R1/R2 sobre switches reales y el IDS con este compose (ver los README de `R1/deploy` y `R5/deploy`).
