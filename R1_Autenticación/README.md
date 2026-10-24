# Arquitectura SDN Zero Trust con Autenticación 802.1X y EAP-TLS

Implementación contenerizada de una arquitectura Zero Trust sobre Redes Definidas por Software (SDN). Combina autenticación de acceso a nivel de puerto (IEEE 802.1X / EAP-TLS), validación de identidad y roles en OpenLDAP, verificación estricta de atributos de dispositivo (MAC binding y switch port) mediante un microservicio en FastAPI (Policy Mapper), e inyección dinámica de reglas de flujo OpenFlow 1.3 en el controlador ONOS.

---

## 📋 Arquitectura de Red y Servicios

Todos los servicios de autenticación y control se ejecutan sobre una red puente personalizada de Docker (`sdn_net`) bajo la subred `172.20.0.0/16`:

| Servicio | Contenedor | Dirección IP | Puertos Expuestos | Rol en la Solución |
| :--- | :--- | :--- | :--- | :--- |
| **ONOS Controller** | `onos_controller` | `172.20.0.2` | `6653`, `8181`, `8101` | Controlador OpenFlow 1.3. Aloja las aplicaciones SADIS (5.6.0) y AAA (2.7.0). |
| **OpenLDAP** | `openldap_server` | `172.20.0.3` | `389`, `636` | Directorio institucional (`example.org`). Almacena usuarios, roles y MACs autorizadas. |
| **Policy Mapper** | `policy_mapper` | `172.20.0.4` | `5000` | Motor de políticas Zero Trust (FastAPI). Gestiona sesiones y resuelve consultas SADIS. |
| **FreeRADIUS** | `freeradius_server` | `172.20.0.5` | `1812/udp`, `1813/udp` | Servidor AAA. Termina el túnel EAP-TLS y despacha webhooks REST post-autenticación. |

---

## 📁 Estructura del Repositorio

```text
R1_Autenticación/
├── docker-compose.yml          # Orquestación de los 4 microservicios en sdn_net
├── init_onos.sh                # Script de aprovisionamiento de apps y netcfg en ONOS
├── topo_base.py                # Topología Mininet (Switch s1 y hosts h1, h2)
├── ldap/
│   └── estructura.ldif         # Esquema de unidades organizativas, usuarios y grupos LDAP
├── policy_mapper/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── policy_mapper.py        # API FastAPI (Webhook REST + Backend dinámico SADIS)
├── freeradius/
│   ├── Dockerfile
│   └── config/                 # Configuraciones personalizadas (eap, ldap, rest, sites)
├── supplicants/
│   ├── wpa_alumno.conf         # Configuración 802.1X para h1 (Docente)
│   ├── wpa_alumno2.conf        # Configuración 802.1X para h2 (Alumno)
│   └── certs/                  # Certificados digitales de prueba (CA y Clientes)
└── README.md
```

---

## ⚙️ Requisitos Previos

En el sistema host (Ubuntu / WSL2):
* Docker Engine y Docker Compose v2 (`docker-compose-linux-x86_64`)
* Mininet y Open vSwitch (`openvswitch-switch`)
* Utilidades de cliente: `wpasupplicant`, `ldap-utils`, `curl`, `openssh-client`

---

## 🚀 Despliegue del Sistema

### 1. Construir y Levantar los Contenedores
Desde el directorio raíz del proyecto:
```bash
cd /home/ubuntu/R1_Autenticación
docker-compose up -d --build
```
Verificar que los cuatro contenedores figuren en estado `Up`:
```bash
docker ps
```

### 2. Cargar la Estructura en OpenLDAP
Si es la primera vez que se inicia el servicio, inyectar el esquema organizativo y los usuarios de prueba:
```bash
docker exec -i openldap_server ldapadd -x -D "cn=admin,dc=example,dc=org" -w admin -H ldap://localhost < ldap/estructura.ldif
```

### 3. Aprovisionar el Controlador ONOS
Ejecutar el script de automatización para descargar módulos, activar OpenFlow e inyectar la configuración de red hacia FreeRADIUS y Policy Mapper:
```bash
chmod +x init_onos.sh
./init_onos.sh
```

### 4. Iniciar la Topología Mininet
En una terminal secundaria, limpiar entornos residuales e iniciar la topología:
```bash
sudo mn -c
sudo python3 topo_base.py
```

### 5. Configurar el Switch Open vSwitch (s1)
En la terminal principal, habilitar el reenvío de tramas reservadas e inyectar la regla trampa para interceptar el tráfico EAPOL (EtherType `0x888e`) hacia el controlador:
```bash
sudo ovs-vsctl set bridge s1 other_config:forward-bpdu=true
sudo ovs-ofctl -O OpenFlow13 add-flow s1 "table=0,priority=50000,dl_type=0x888e,actions=output:CONTROLLER"
```

---

## 🧪 Pruebas de Funcionamiento y Políticas Zero Trust

Todas las pruebas del suplicante 802.1X se lanzan desde la consola interactiva de Mininet (`mininet>`).

### Caso 1: Asignación de Roles Institucionales por VLAN

#### Host 1 (`h1`) - Rol Docente (VLAN 10):
```text
mininet> h1 killall wpa_supplicant
mininet> h1 wpa_supplicant -c /home/ubuntu/R1_Autenticación/supplicants/wpa_alumno.conf -D wired -i h1-eth0 -d
```
* **Resultado:** Handshake EAP-TLS exitoso. FreeRADIUS identifica el grupo LDAP `docente`. Policy Mapper valida identidad y programa en Open vSwitch un flujo en Tabla 0 con `priority=40001`, aplicando tag `vlan_vid=10` y salto a `table=10`.

#### Host 2 (`h2`) - Rol Alumno (VLAN 20):
```text
mininet> h2 killall wpa_supplicant
mininet> h2 wpa_supplicant -c /home/ubuntu/R1_Autenticación/supplicants/wpa_alumno2.conf -D wired -i h2-eth0 -d
```
* **Resultado:** FreeRADIUS procesa al usuario `alumno01`, valida pertenencia al grupo `alumno` y Policy Mapper aplica aislamiento bajo VLAN 20.

---

### Caso 2: Validación de Dispositivo no Autorizado (MAC ≠ LDAP)
Se altera la dirección física de `h1` para simular el uso de un certificado legítimo en una máquina no registrada en el directorio institucional:

```text
mininet> h1 killall wpa_supplicant
mininet> h1 ifconfig h1-eth0 down
mininet> h1 ifconfig h1-eth0 hw ether 00:00:00:00:00:99
mininet> h1 ifconfig h1-eth0 up
mininet> h1 wpa_supplicant -c /home/ubuntu/R1_Autenticación/supplicants/wpa_alumno.conf -D wired -i h1-eth0 -d
```
* **Resultado:** La autenticación TLS culmina, pero el Policy Mapper compara la MAC entrante (`00:00:00:00:00:99`) con la registrada en el atributo `description` de LDAP (`00:00:00:00:00:11`). Al diferir, rechaza la solicitud (`403 Forbidden`) y FreeRADIUS emite un `Access-Reject`.

---

### Caso 3: Mitigación de Suplantación de Identidad (MAC Spoofing)
Se autentica legítimamente `h1`. Luego, un host atacante (`h2`) clona la MAC de `h1` e intenta autenticarse o colarse en la red:

```text
# 1. Autenticar h1 de manera regular
mininet> h1 killall wpa_supplicant
mininet> h1 ifconfig h1-eth0 down
mininet> h1 ifconfig h1-eth0 hw ether 00:00:00:00:00:11
mininet> h1 ifconfig h1-eth0 up
mininet> h1 wpa_supplicant -c /home/ubuntu/R1_Autenticación/supplicants/wpa_alumno.conf -D wired -i h1-eth0 -B

# 2. h2 clona la dirección MAC física de h1 e intenta autenticarse
mininet> h2 killall wpa_supplicant
mininet> h2 ifconfig h2-eth0 down
mininet> h2 ifconfig h2-eth0 hw ether 00:00:00:00:00:11
mininet> h2 ifconfig h2-eth0 up
mininet> h2 wpa_supplicant -c /home/ubuntu/R1_Autenticación/supplicants/wpa_alumno2.conf -D wired -i h2-eth0 -B
```
* **Resultado:** El Policy Mapper detecta que la MAC `00:00:00:00:00:11` ya se encuentra activa en el puerto `s1-eth1`. Al provenir la nueva petición desde `s1-eth2`, el motor Zero Trust bloquea el acceso por usurpación de topología / spoofing.

---

## 📊 Monitoreo y Diagnóstico

* **Consultar sesiones activas en el Policy Mapper:**
  ```bash
  curl http://localhost:5000/sessions
  ```
* **Inspeccionar tablas de flujos en Open vSwitch:**
  ```bash
  sudo ovs-ofctl -O OpenFlow13 dump-flows s1
  ```
* **Seguimiento de trazas en FreeRADIUS:**
  ```bash
  docker logs -f freeradius_server
  ```
* **Seguimiento de eventos en Policy Mapper:**
  ```bash
  docker logs -f policy_mapper
  ```
* **Acceso a la consola Karaf de ONOS:**
  ```bash
  ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedKeyTypes=+ssh-rsa -p 8101 onos@localhost
  # Contraseña: rocks
  ```

---

## 🛠 Solución de Problemas Frecuentes

* **Host key verification failed al ingresar a ONOS:**  
  Limpiar el registro de clave SSH local:
  ```bash
  ssh-keygen -f '/root/.ssh/known_hosts' -R '[localhost]:8101'
  ```
* **FreeRADIUS se detiene al iniciar por fallo de conexión a LDAP:**  
  Ocurre si OpenLDAP tarda unos segundos en inicializar certificados TLS. Arrancar el contenedor manualmente:
  ```bash
  docker start freeradius_server
  ```
* **El suplicante se queda en estado `CONNECTING`:**  
  Verificar que `s1` tenga activo el reenvío de BPDUs y que la regla trampa esté instalada:
  ```bash
  sudo ovs-vsctl get bridge s1 other_config:forward-bpdu
  sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep 0x888e
  ```