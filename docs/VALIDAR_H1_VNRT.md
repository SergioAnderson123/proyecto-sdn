# Validar un host con 802.1X en VNRT (ejemplo: h1)

Objetivo: que h1 envíe su suplicante (EAP-TLS), se autentique contra FreeRADIUS y que el Policy Mapper instale el flujo de sesión en **su switch de acceso** (sw2). Probado con h1 / `profesor01`.

Topología (VNRT): h1 y h2 cuelgan de **sw2**; h3 y h4, de **sw3**; sw1 es el core. Los flujos de sesión van al switch de acceso del host, no a sw1.

| Dato de h1 | Valor |
|---|---|
| Usuario / certificado | `profesor01@example.org` (rol docente, VLAN 10) |
| Interfaz de datos de h1 | `ens4` |
| MAC de h1 | `fa:16:3e:6b:69:bb` |
| Switch de acceso | sw2, `of:00001a749039894a` |
| Puerto de sw2 hacia h1 | 2 (`ens5`) |

Los puertos de sw2 son `1=ens4`, `2=ens5`, `3=ens6`; los de sw3 tienen otro orden (`1=ens6`, `2=ens5`, `3=ens4`). Por eso el switch y el puerto se toman de lo que ve ONOS (`connectPoint`), no del nombre del puerto.

## 0. Requisitos
En el Controller (`/home/ubuntu/proyecto-sdn`), con el repo actualizado (`git pull`):

```bash
cd /home/ubuntu/proyecto-sdn/R1/deploy/controller && ./up.sh      # si falla a medias: bash init-onos.sh
```
- Apps de ONOS activas: `openflow`, `org.opencord.sadis`, `org.opencord.aaa`, `dhcp`, `acl`, `vpls`, `flowanalyzer`. Comprobación en la CLI de ONOS (`ssh -p 8101 onos@127.0.0.1`, clave `rocks`): `apps -a -s`. Sin `openflow` no hay switches; sin `aaa` no hay autenticación.
- Switches conectados: `cd R1/deploy/switches && ansible-playbook -i inventory.ini switches.yml --ask-become-pass`. En ONOS deben salir `available: true`:
  ```bash
  curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/devices | python3 -m json.tool | grep -E '"id"|"available"'
  ```
- Una sola instancia de cada servicio (`docker ps`): si quedó un contenedor `onos` viejo, `docker rm -f onos` y repetir.

## 1. LDAP: MAC, switch y puerto de h1
El LDIF del repo (`R1/shared/ldap/ldif/custom/estructura.ldif`) ya trae estos valores para `profesor01`, **pero OpenLDAP solo carga el LDIF con `openldap-data` vacío**. Con el LDAP ya creado, aplícalos así:

```bash
cat > /tmp/set.ldif <<'EOF'
dn: uid=profesor01,ou=users,dc=example,dc=org
changetype: modify
replace: description
description: fa:16:3e:6b:69:bb
-
replace: physicalDeliveryOfficeName
physicalDeliveryOfficeName: of:00001a749039894a
-
replace: roomNumber
roomNumber: 2
EOF
docker cp /tmp/set.ldif ldap:/tmp/set.ldif
docker exec ldap ldapmodify -x -H ldap://localhost -D cn=admin,dc=example,dc=org -w admin -f /tmp/set.ldif
docker exec ldap ldapsearch -x -H ldap://localhost -D cn=admin,dc=example,dc=org -w admin -LLL -b uid=profesor01,ou=users,dc=example,dc=org description physicalDeliveryOfficeName roomNumber
```
Si los valores no coinciden con lo que ve ONOS, el Policy Mapper rechaza y **mete la MAC en lista negra 300 s**.

Cómo averiguar la MAC, el switch y el puerto de otro host: ver la sección 6.

## 2. h1: suplicante
### Opción A: con Ansible (recomendada, sirve para h1 a h4)
Ver `R1/deploy/hosts/` y la sección 6. Para solo h1: `ansible-playbook -i inventory.ini hosts.yml --ask-become-pass --limit h1 -e arrancar=true`.

### Opción B: a mano en h1
Desde el Controller, copiar certificado y configuración (h1 = puerto 5811):
```bash
cd /home/ubuntu/proyecto-sdn/R1/shared/mininet/supplicants
ssh -p 5811 ubuntu@10.20.12.78 'mkdir -p ~/supp/certs'
scp -P 5811 wpa_profe_01.conf ubuntu@10.20.12.78:~/supp/
scp -P 5811 certs/ca.pem 'certs/profesor01@example.org.pem' ubuntu@10.20.12.78:~/supp/certs/
```
En h1:
```bash
sed -i 's|/root/shared-host/supplicants|/home/ubuntu/supp|' ~/supp/wpa_profe_01.conf
sudo apt-get install -y wpasupplicant tcpdump
ip -br link                                   # la interfaz de datos hacia sw2 (aquí ens4)
```
**EAPOL en VNRT.** `wpa_supplicant` envía el EAPOL a `01:80:c2:00:00:03`, dirección reservada que la red virtual de VNRT descarta: el paquete nunca llega a sw2. Es una limitación de esta red virtual, no del diseño (en una red física el EAPOL llega al switch tal cual). En los hosts de prueba se evita reescribiendo el destino a broadcast al salir; ONOS lo captura igual porque filtra por `eth_type=0x888e`:
```bash
sudo ip link set ens4 up
sudo tc qdisc add dev ens4 clsact
sudo tc filter add dev ens4 egress protocol 0x888e flower dst_mac 01:80:c2:00:00:03 \
  action pedit ex munge eth dst set ff:ff:ff:ff:ff:ff
tc filter show dev ens4 egress
```
Si `tc` no encuentra `cls_flower` o `act_pedit`: `sudo apt-get install -y linux-modules-extra-$(uname -r)`.

Lanzar **una sola** instancia (varias a la vez cruzan las conversaciones EAP y producen `bad signature`):
```bash
sudo pkill wpa_supplicant; sleep 2
sudo wpa_supplicant -D wired -i ens4 -c ~/supp/wpa_profe_01.conf -dd
```

## 3. Comprobar que llegó
Éxito en h1: `CTRL-EVENT-EAP-SUCCESS` y `Supplicant port status: Authorized`.

En el Controller:
```bash
curl -s -u onos:rocks http://127.0.0.1:8181/onos/aaa/app/users | python3 -m json.tool        # connectPoint = of:00001a749039894a/2, AUTHORIZED_STATE
curl -s http://127.0.0.1:5000/sessions | python3 -m json.tool                                  # dpid del switch real, puerto 2, vlan 10, rol Docente
docker logs policy_mapper --tail 20                                                            # "Flujo Prio 40001 inyectado ... en of:00001a749039894a" y "APROBADO"
docker logs radius --tail 40 2>&1 | grep -E "Access-Accept|Access-Reject"
```
En sw2 debe verse el flujo de sesión con el puerto y la MAC de h1, además de las tablas de rol de R2 (10, 20, 40, 50):
```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows sw2 | grep -v 0x888e
# priority=40001,in_port=2,dl_src=fa:16:3e:6b:69:bb actions=push_vlan:0x8100,set_field:4106->vlan_vid,goto_table:10
```
Los flujos de `priority=40000` (LLDP, ARP, DHCP) son de ONOS y no se tocan.

Prueba negativa de la ubicación: pon `roomNumber: 3`, reinicia `policy_mapper` y vuelve a autenticar: debe rechazar con "Puerto físico no asignado". Devuélvelo a `2`.

## 4. Repetir la prueba desde cero
```bash
# Controller
docker restart policy_mapper                                  # las sesiones están en memoria
curl -s -u onos:rocks -X DELETE http://127.0.0.1:8181/onos/v1/flows/application/org.onosproject.rest   # (opcional) quita sesión y tablas de rol
curl -s -X POST http://127.0.0.1:5000/policy/sync            # (opcional) reinstala las tablas de rol
# h1
sudo pkill wpa_supplicant
```
Si una MAC quedó en lista negra (45000), espera 300 s o bórrala con `DELETE /onos/v1/flows/<deviceId>/<flowId>`. No uses `ovs-ofctl del-flows`: ONOS volvería a instalarlos o quedaría desincronizado.

## 5. Problemas encontrados (y su causa)
| Síntoma | Causa | Solución |
|---|---|---|
| `Permission denied` al lanzar `up.sh` | El `.sh` no conserva el bit de ejecución al clonar desde Windows | `bash up.sh` o `chmod +x` |
| Solo hay 2 apps activas en ONOS | No corrió `init-onos.sh` (`up.sh` falló a medias) | `bash init-onos.sh` |
| Los switches no se conectan: aparecen "controladores" como `los`, `de`, `DESDE` | Comentario al final de `controller_ip` en el inventario INI | Quitar el comentario de esa línea |
| `Missing sudo password` | `ubuntu` pide contraseña para `sudo` | `--ask-become-pass` |
| h1 manda EAPOL pero no llega a sw2 | La red virtual descarta `01:80:c2:00:00:03` | Reescribir el destino con `tc` (sección 2) |
| `bad signature` / `EAP-FAILURE` con certificados correctos | Varias instancias de `wpa_supplicant` a la vez | `pkill wpa_supplicant` y lanzar una |
| Flujo instalado en `of:0000000000000en5` | SADIS recibe solo el nombre del puerto (`ens5`) y el código asumía nombres de Mininet | Corregido: el Policy Mapper usa `connectPoint` |
| Rechazo "Puerto físico no asignado" o "MAC no coincide" | LDAP no coincide con lo que ve ONOS | Sección 1 o `ldap-ubicacion.sh` |

## 6. Otros hosts (h2, h3, h4)
Los hosts se preparan con el playbook de `R1/deploy/hosts/` y su ubicación se aprende de ONOS con `R1/deploy/controller/ldap-ubicacion.sh`:

```bash
# 1. Playbook: instala wpa_supplicant, copia certificado y .conf, deja el reescrito de EAPOL y muestra la MAC de cada host
cd /home/ubuntu/proyecto-sdn/R1/deploy/hosts
cp inventory.ini.example inventory.ini && nano inventory.ini       # ansible_port, iface, conf y cert de cada host
for p in 5811 5812 5813 5814; do ssh-copy-id -i ~/.ssh/id_ed25519.pub -p $p ubuntu@10.20.12.78; done
ansible -i inventory.ini hosts -m ping
ansible-playbook -i inventory.ini hosts.yml --ask-become-pass

# 2. Por cada host nuevo (ejemplo: h2 con alumno01): liberar -> autenticar -> fijar -> reiniciar -> autenticar otra vez
cd /home/ubuntu/proyecto-sdn/R1/deploy/controller
./ldap-ubicacion.sh liberar alumno01
cd ../hosts && ansible-playbook -i inventory.ini hosts.yml --ask-become-pass --limit h2 -e arrancar=true
cd ../controller && ./ldap-ubicacion.sh fijar alumno01          # copia MAC, switch y puerto que ONOS vio
docker restart policy_mapper
cd ../hosts && ansible-playbook -i inventory.ini hosts.yml --ask-become-pass --limit h2 -e arrancar=true
```
Para parar un suplicante: `ansible-playbook -i inventory.ini hosts.yml --ask-become-pass --limit h2 -e parar=true`.

Usuarios y roles del LDAP de pruebas: `profesor01/02` (VLAN 10), `alumno01/02/03` (VLAN 20), `support01` (VLAN 40), `guest01` (portal cautivo). Las contraseñas de las claves privadas están en los `wpa_*.conf`.

## 7. Volver a los valores del laboratorio de Mininet
El LDIF del repo ahora lleva los valores de VNRT para `profesor01`. Para el laboratorio de Mininet (`R1/compose.yml`, h1 con `00:00:00:00:00:11` en `of:0000000000000001`, puerto 1) hay que volver a poner esos tres valores en su bloque del LDIF.

## 8. Limitaciones y pendientes
- Probado solo con h1 y `profesor01`; h2 a h4 no se han validado con este procedimiento.
- Las tablas de rol de R2 usan los servicios `10.0.0.40/50/60` y `trusted` con los dpid de Mininet (`policy.yaml`). Para que el tráfico de un host autenticado salga de verdad hacen falta los dpid reales de sw1 y sw2, el puerto troncal marcado como de confianza y los servicios en hosts reales de VNRT.
- El reescrito de EAPOL con `tc` es solo del entorno VNRT; pedir al personal de VNRT que permita `01:80:c2:00:00:03` lo haría innecesario.
