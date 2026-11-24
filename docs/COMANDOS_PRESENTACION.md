# Comandos de la presentación

Guion de comandos para la demostración en vivo (R1 con 802.1X y R2 con servicios por rol). Los comandos se ejecutan en el nodo de borde, salvo que se indique otro equipo.

## 1. Terminales de apoyo

**RADIUS** (decisiones de acceso):

```bash
docker logs -f --tail 0 radius 2>&1 | grep --line-buffered -E "Access-Accept|Access-Reject|ERROR"
```

**Policy Mapper** (decisiones de admisión):

```bash
docker logs -f --tail 0 policy_mapper
```

**ONOS** (consola):

```bash
ssh -p 8101 onos@127.0.0.1
```

## 2. Túneles (desde la PC)

| Qué | Comando | Dirección |
|---|---|---|
| Panel de la red | `ssh -L 8480:127.0.0.1:8480 -p 5823 ubuntu@10.20.11.14` | http://localhost:8480 |
| Interfaz de h1 | `ssh -L 8081:127.0.0.1:8080 -p 5821 ubuntu@10.20.11.14` | http://localhost:8081 |
| Interfaz de h2 | `ssh -L 8082:127.0.0.1:8080 -p 5822 ubuntu@10.20.11.14` | http://localhost:8082 |

## 3. Preparación y cambio de identidad (en el borde)

**Levantar por primera vez** (h1 y luego h2):

```bash
cd ~/proyecto-sdn && git pull && cd R1/deploy/topologia14 && PLAYBOOK=72_suplicante.yml ./lanzar.sh
```

```bash
PLAYBOOK=72_suplicante.yml ./lanzar.sh -e objetivo_host=h2-alumno
```

**Cambiar de alumno02 a profesor01** en h1 (antes, desconectar a alumno02 en la interfaz):

```bash
PLAYBOOK=72_suplicante.yml ./lanzar.sh -e identidad=profesor01
```

**Volver a alumno02** (antes, desconectar a profesor01 en la interfaz):

```bash
PLAYBOOK=72_suplicante.yml ./lanzar.sh
```

**Borrar las reglas de lista negra** (prioridad 45000):

```bash
curl -s -u onos:rocks http://192.168.201.2:8181/onos/v1/flows | python3 -c 'import sys,json; [print(f["deviceId"], f["id"]) for f in json.load(sys.stdin)["flows"] if f["priority"]==45000]' | while read d i; do curl -s -u onos:rocks -X DELETE "http://192.168.201.2:8181/onos/v1/flows/$d/$i" -o /dev/null -w "$d $i -> %{http_code}\n"; done
```

## 4. R1: admisión por 802.1X

### Prueba 1. Solo loguear

Conectar cada host en su interfaz (h1 con alumno02 y h2 con alumno01) y consultar las sesiones:

```bash
curl -s -m 20 http://192.168.201.254:5000/sessions
```

En el panel: ver Sesiones, Eventos y Flujos (mostrar el flujo de prioridad 40001 en cada switch).

### Prueba 2. alumno02 en h2 (la MAC no coincide con LDAP)

```bash
ssh -t ubuntu@192.168.201.1 "sudo bash /tmp/ldap-ubicacion.sh ver alumno02"
```

Ver el evento «MAC no coincide» y el flujo de descarte en el switch.

### Prueba 3 (no mostrar). La misma identidad en dos dispositivos (alumno01 en h1 y luego en h2)

```bash
ssh -t ubuntu@192.168.201.1 "sudo bash /tmp/ldap-ubicacion.sh ver alumno01"
```

### Prueba 4. La sesión legítima va primero y luego el clon (h2, luego h1)

Conectar h2 con alumno01 (verificar la sesión en el panel) y después h1 con alumno01. Ver el flujo de descarte del switch por suplantación.

### Prueba 5. Certificado de otra CA

En h1:

```bash
ssh ubuntu@192.168.201.21
```

```bash
cd /home/ubuntu/supp && openssl req -x509 -newkey rsa:2048 -nodes -keyout certs/falso.key -out certs/falso.crt -days 1 -subj "/C=FR/O=Example Inc./CN=falso01@example.org"
```

```bash
printf 'network={\n    key_mgmt=IEEE8021X\n    eap=TLS\n    identity="falso01@example.org"\n    ca_cert="/home/ubuntu/supp/certs/ca.pem"\n    client_cert="/home/ubuntu/supp/certs/falso.crt"\n    private_key="/home/ubuntu/supp/certs/falso.key"\n}\n' > wpa_falso.conf
```

Iniciar sesión con esa identidad falsa (falso01) desde la interfaz de h1.

### Prueba 6 (no hacer). Suplantación de MAC con el certificado clonado

Conectar h2 con alumno01 y verificar. Clonar la MAC desde h1:

```bash
sudo ip link set dev ens4 down; sudo ip link set dev ens4 address fa:16:3e:71:08:66; sudo ip link set dev ens4 up; ip -br link show ens4
```

```bash
sudo ip addr add 10.0.1.100/24 dev ens4; sudo ip route replace 10.0.0.0/16 via 10.0.1.254 dev ens4; ip -4 -br addr show ens4
```

Restaurar:

```bash
sudo ip link set dev ens4 down; sudo ip link set dev ens4 address fa:16:3e:b6:96:08; sudo ip link set dev ens4 up; sleep 5; ip -br link show ens4; ip -4 -br addr show ens4
```

### Verificar si los hosts llegan a los servicios

En h1:

```bash
nc -zv -w 2 -s 10.0.1.100 10.0.0.10 8101
```

En h2:

```bash
nc -zv -w 2 -s 10.0.2.100 10.0.0.10 8101
```

## 5. R2: servicios por rol

Definir la función de sondeo en h1 (una vez):

```bash
sondeo(){ echo "h1 (10.0.1.100)"; for p in 8001 8002 8003 8101 8102 8103 8104 8105 8999; do printf "%s  " $p; nc -zv -w 2 -s 10.0.1.100 10.0.0.10 $p 2>&1 | tail -1; done; }
```

Cómo leer el resultado: `refused` o `succeeded` = el paquete llegó (permitido); `timed out` = la red lo descartó (denegado).

### Prueba 1. h1 sin sesión (cuarentena)

```bash
sondeo
```

### Prueba 2. Iniciar sesión en h1 con alumno02

```bash
sondeo
```

### Prueba 3. Iniciar sesión en h1 con profesor01

```bash
sondeo
```
