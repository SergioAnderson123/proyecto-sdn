# Guía de validación en vivo de R1 por 802.1X (LAB 7)

Para repetir, con la interfaz web de los hosts, los casos de control de acceso por 802.1X, y dejar registrados los comandos y las evidencias de cada uno. Se hacen **en este orden**: cada caso deja el estado listo para el siguiente (la sección 3 lo restaura si algo se tuerce).

Las **expectativas** salen del código (`R1/shared/policy_mapper/policy_mapper.py`, `_procesar_webhook`) y de las pruebas unitarias; las que no se han visto en vivo están marcadas **(por confirmar)**: se anota lo que salga y se marca PASS o FAIL en la tabla de resultados del final.

## 1. Cadena de decisión del módulo (sirve para el diagrama de flujo del informe)

Un host autentica con EAP-TLS. Estas son las decisiones, en orden, hasta dar o negar el acceso:

| Paso | Dónde | Qué comprueba | Si falla |
|---|---|---|---|
| 1 | RADIUS | El certificado del cliente lo firmó nuestra CA | Access-Reject: el host queda en «Acceso denegado» y el mapper ni se entera |
| 2 | Mapper | ONOS AAA tiene a esa identidad negociando en algún puerto | 403 «Sesión EAP no encontrada en ONOS AAA» |
| 3 | Mapper | La identidad no negocia en dos puertos a la vez; si lo hace, gana la ubicación fijada en LDAP y la otra va a lista negra 600 s | evento `conflicto` «Ubicación no autorizada… Posible clonación; lista negra 600 s» |
| 4 | Mapper | La MAC de LDAP (`description`) es la MAC física | 403 «Device Binding Failed: MAC no coincide», lista negra **300 s**, evento `rechazado` |
| 5 | Mapper | No hay ya una sesión de esa identidad o de esa MAC en otro sitio | 409 «El usuario o MAC ya posee una sesión activa», lista negra **600 s**, evento `conflicto` |
| 6 | Mapper | Sin sesión previa: el switch y el puerto son los de LDAP | 403 «Puerto físico no asignado», evento `rechazado` |
| 7 | Mapper | El rol del grupo LDAP existe en la política (R2) | 403 «Rol sin acceso autorizado» |
| 8 | Mapper | Todo bien | Instala el flujo de sesión (prioridad 40001), registra la sesión y el evento `admitido` |

La **lista negra** es un descarte de prioridad 45000 (`in_port` + `eth_src`) que dura mientras no haya tráfico del equipo en el plazo indicado.

## 2. Preparación (una vez)

Abre estas pestañas y no las cierres:

1. **Túneles a las interfaces de los hosts** (dos pestañas de PowerShell en tu PC) y las páginas `http://localhost:8081` (h1) y `http://localhost:8082` (h2):

```powershell
ssh -L 8081:127.0.0.1:8080 -p 5821 ubuntu@10.20.11.14
```

```powershell
ssh -L 8082:127.0.0.1:8080 -p 5822 ubuntu@10.20.11.14
```

2. **Túnel del panel** (`http://localhost:8480`): pestañas **Sesiones**, **Eventos**, **Flujos** y **Vigilante**.
3. **Sesión SSH al borde** para los comandos de la guía.

Estado inicial esperado: h1 y h2 en «Desconectado», panel sin sesiones y sin avisos (salvo un `FUENTE_CAIDA` pasajero del mapper). Comprueba LDAP (desde el borde):

```bash
ssh -t ubuntu@192.168.201.1 "sudo bash /tmp/ldap-ubicacion.sh ver alumno01"
```

```bash
ssh -t ubuntu@192.168.201.1 "sudo bash /tmp/ldap-ubicacion.sh ver alumno02"
```

| Identidad | MAC en LDAP | Switch | Puerto |
|---|---|---|---|
| alumno01 | `fa:16:3e:71:08:66` (h2) | `of:0000766a0c84c741` (acc-2) | 1 |
| alumno02 | `fa:16:3e:b6:96:08` (h1) | `of:0000e6925d87354b` (acc-1) | 1 |

(Si `/tmp/ldap-ubicacion.sh` no existe en ctrl-1: `scp ~/proyecto-sdn/R1/deploy/controller/ldap-ubicacion.sh ubuntu@192.168.201.1:/tmp/`.)

## 3. Comandos de apoyo (los usan varios casos)

**Ver las listas negras activas** (prioridad 45000):

```bash
curl -s -u onos:rocks http://192.168.201.2:8181/onos/v1/flows | python3 -c 'import sys,json; [print(f["deviceId"], f["id"], [c.get("mac") or c.get("port") for c in f["selector"]["criteria"] if c["type"] in ("ETH_SRC","IN_PORT")]) for f in json.load(sys.stdin)["flows"] if f["priority"]==45000]'
```

**Quitarlas todas** (cada borrado debe contestar `204`):

```bash
curl -s -u onos:rocks http://192.168.201.2:8181/onos/v1/flows | python3 -c 'import sys,json; [print(f["deviceId"], f["id"]) for f in json.load(sys.stdin)["flows"] if f["priority"]==45000]' | while read d i; do curl -s -u onos:rocks -X DELETE "http://192.168.201.2:8181/onos/v1/flows/$d/$i" -o /dev/null -w "$d $i -> %{http_code}\n"; done
```

**Prueba de tráfico al servidor desde h1 o h2** (entra por SSH al host: `ssh ubuntu@192.168.201.21` es h1 y `ssh ubuntu@192.168.201.22` es h2). `refused` o `succeeded` significa que llegó al servidor; `timed out`, que la red lo descarta:

```bash
IP=$(ip -4 -o addr show dev ens4 | awk '{print $4}' | cut -d/ -f1 | grep -E '^10[.]0[.][12][.]' | head -1); nc -zv -w 2 -s $IP 10.0.0.10 8001
```

**Evidencias del panel** (todos los casos): pestaña *Eventos* (el rechazo o la admisión, con su detalle), *Sesiones* (si hay sesión y de quién) y *Flujos* con `switch=acc-1` (o acc-2), `tabla=0` y `prioridad=40001` (la sesión) o `45000` (la lista negra).

**Entre casos, para dejar todo limpio:** pulsa *Desconectar* en la interfaz del host, ejecuta «Quitar las listas negras» y comprueba que *Sesiones* está vacío.

## 4. Casos

Para cada caso, las capturas son: **(a)** la interfaz del host con el estado y el registro del suplicante, **(b)** la pestaña *Eventos* del panel, **(c)** la pestaña *Sesiones* o *Flujos* según se indica, y **(d)** la salida de los comandos que se usen.

### R1-01 · Acceso válido: certificado, MAC y ubicación coinciden

**1.1.1 · alumno02 en h1 (su equipo).**
- Pasos: interfaz de h1 → elegir `alumno02` → *Conectar*.
- Esperado: «Autenticado — Acceso concedido como Alumno (VLAN 20)», con switch `of:0000e6925d87354b` y puerto 1. Evento `admitido alumno02 … VLAN 20, tabla 20`. En *Sesiones* una fila de alumno02 con la MAC de h1. En *Flujos* de acc-1 (tabla 0, prioridad 40001) una regla `in_port=1, eth_src=fa:16:3e:b6:96:08` de `onos.rest` que pone VLAN 20 y salta a la tabla 20.
- Tráfico (opcional): `nc … 8001` desde h1 llega; `8101` no (el alumno no tiene `grade_system`).

**1.1.2 · alumno01 en h2 (su equipo).** Igual que el anterior con la interfaz de h2 y el switch acc-2 (`of:0000766a0c84c741`).

**1.1.3 · profesor01 en h1 (el docente, sin equipo atado).**
- Antes, desde el borde, para que h1 ofrezca a profesor01 (no conecta a nadie; antes desconecta a alumno02 en la interfaz de h1):

```bash
cd ~/proyecto-sdn/R1/deploy/topologia14 && PLAYBOOK=72_suplicante.yml ./lanzar.sh -e identidad=profesor01
```

- Pasos: interfaz de h1 → `profesor01` → *Conectar*.
- Esperado: «Acceso concedido como Docente (VLAN 10)», evento `admitido … VLAN 10, tabla 10`. `nc … 8101` llega al servidor (el docente sí tiene `grade_system`).
- Volver al alumno: desconectar profesor01 y repetir el comando sin `-e identidad`.

### R1-02 · Rechazos (acceso inválido)

**1.2.1 · La MAC no coincide con la de LDAP: alumno02 en h2.**
- Pre: h2 desconectado. alumno02 está atado a la MAC de h1, no a la de h2.
- Pasos: interfaz de h2 → `alumno02` → *Conectar*.
- Esperado: «Acceso denegado» con «MAC registrada fa:16:3e:b6:96:08 distinta de la física; lista negra 300 s». Evento `rechazado alumno02` con la MAC de h2 y switch acc-2. En *Sesiones*, ninguna sesión nueva. En *Flujos* de acc-2 una regla de prioridad 45000 `in_port=1, eth_src=fa:16:3e:71:08:66` que descarta.
- Limpieza: desconectar y quitar las listas negras.

**1.2.2 · Usuario con sesión activa en otro dispositivo (a): alumno01 en h1 y luego en h2** (es el orden que ya probaste).
- Pasos: interfaz de h1 → `alumno01` → *Conectar*; después, interfaz de h2 → `alumno01` → *Conectar*.
- Esperado (lo ya observado): h2 se admite (VLAN 20) y h1 queda rechazado: evento `conflicto alumno01` con la MAC de h1, switch acc-1, «Ubicación no autorizada (…354b:1); se esperaba …c741:1. Posible clonación; lista negra 600 s», y en acc-1 la regla de prioridad 45000 con la MAC de h1.
- Limpieza: desconectar los dos y quitar las listas negras.

**1.2.3 · Usuario con sesión activa en otro dispositivo (b): primero h2 y luego h1. (por confirmar)**
- Pasos: interfaz de h2 → `alumno01` → *Conectar* (queda con sesión); luego interfaz de h1 → `alumno01` → *Conectar*.
- Esperado: la sesión de h2 se conserva y h1 **no obtiene una segunda sesión** de alumno01. Anota qué evento sale (`conflicto` o `rechazado`) y si la MAC de h1 queda en lista negra; es lo que hay que comparar con el caso anterior.
- Limpieza: igual.

**1.2.4 · Suplantación de MAC con el certificado clonado.** Es el caso más invasivo; hazlo el último de R1-02.
- Idea: h1 copia la MAC de h2 y presenta el certificado de alumno01 mientras h2 ya tiene su sesión legítima.
- Pre: `alumno01` conectado en h2 (sesión activa). h1 desconectado.
- Comandos en **h1** (`ssh ubuntu@192.168.201.21`):

```bash
sudo ip link set dev ens4 down; sudo ip link set dev ens4 address fa:16:3e:71:08:66; sudo ip link set dev ens4 up; ip -br link show ens4
```

- Pasos: interfaz de h1 → `alumno01` → *Conectar*.
- Esperado: h1 «Acceso denegado» con «Ubicación no autorizada… Posible clonación; lista negra 600 s» (la ubicación fijada en LDAP decide a favor de acc-2). Evento `conflicto alumno01` con la MAC clonada (`fa:16:3e:71:08:66`) en acc-1. La sesión de h2 **sigue intacta**. En *Flujos* de acc-1, prioridad 45000 con `eth_src=fa:16:3e:71:08:66`.
- **Restaurar la MAC de h1 al terminar (obligatorio):**

```bash
sudo ip link set dev ens4 down; sudo ip link set dev ens4 address fa:16:3e:b6:96:08; sudo ip link set dev ens4 up; ip -br link show ens4
```

- Después: quitar las listas negras. Si h2 pierde conectividad un rato, es el efecto de que ONOS vio su MAC en otro puerto durante la prueba.

**1.2.5 · Certificado que no emitió nuestra CA (identidad falsa).**
- Comandos en **h1** para crear un certificado autofirmado y una configuración de suplicante:

```bash
cd /home/ubuntu/supp && openssl req -x509 -newkey rsa:2048 -nodes -keyout certs/falso.key -out certs/falso.crt -days 1 -subj "/C=FR/O=Example Inc./CN=falso01@example.org"
```

```bash
printf 'network={\n    key_mgmt=IEEE8021X\n    eap=TLS\n    identity="falso01@example.org"\n    ca_cert="/home/ubuntu/supp/certs/ca.pem"\n    client_cert="/home/ubuntu/supp/certs/falso.crt"\n    private_key="/home/ubuntu/supp/certs/falso.key"\n}\n' > wpa_falso.conf
```

- Pasos: interfaz de h1 (la lista muestra `falso01`) → *Conectar*.
- Esperado: «Acceso denegado» (RADIUS rechaza el certificado). **No hay sesión y el mapper no registra evento**, porque el rechazo ocurre antes (paso 1 de la cadena). Capturas: interfaz de h1 y *Eventos* sin ninguna entrada nueva.
- Limpieza en h1: `rm -f /home/ubuntu/supp/wpa_falso.conf /home/ubuntu/supp/certs/falso.key /home/ubuntu/supp/certs/falso.crt`.

### R1-03 · Límite: bloqueo temporal y recuperación

**1.3.1 · Mientras dura la lista negra, ni la identidad correcta tiene tráfico.**
- Pre: repetir **1.2.1** (alumno02 rechazado en h2, MAC de h2 en lista negra 300 s) y **no** quitar la lista negra.
- Pasos: en h2, *Desconectar* y luego `alumno01` → *Conectar* (esta sí es la identidad de h2). Después, la prueba de tráfico desde h2:

```bash
IP=$(ip -4 -o addr show dev ens4 | awk '{print $4}' | cut -d/ -f1 | grep -E '^10[.]0[.]2[.]' | head -1); nc -zv -w 2 -s $IP 10.0.0.10 8001
```

- Esperado: la interfaz dice «Autenticado» y hay sesión, pero **`nc` da `timed out`**: el descarte de prioridad 45000 gana a la sesión (40001). Es el comportamiento diseñado de la lista negra temporal.
- Recuperación: quitar las listas negras y repetir `nc`: debe dar `refused` o `succeeded`. Anota cuántos segundos pasaron entre una cosa y la otra.

**1.3.2 · La lista negra caduca sola.** Con el contador de paquetes parado, a los 300 s (o 600 s) la regla desaparece de *Flujos*. Anota la hora en que se creó y la hora en que dejó de verse. (Si el host sigue mandando tráfico, el plazo se reinicia.)

**1.3.3 · Opcional, portal cautivo:** el bloqueo por intentos fallidos (5 por cuenta y 15 por equipo, 15 minutos) tiene su propia prueba automatizada en `test_portal_intentos.py`.

## 5. Después de los casos

- Quita las listas negras, desconecta h1 y h2 y deja a h1 como alumno02 y a h2 como alumno01 si quieres el estado de siempre.
- Comprueba que no quedó ningún archivo `wpa_falso.conf` ni `falso.*` y que la MAC de h1 es `fa:16:3e:b6:96:08` (`ip -br link show ens4`).
- Con el panel: *Resumen* sin avisos y *Sesiones* con las que esperabas.

## 6. Registro de resultados (rellenar en vivo)

| Caso | Fecha y hora (UTC) | Resultado (PASS / FAIL / NT) | Qué salió | Capturas |
|---|---|---|---|---|
| 1.1.1 alumno02 en h1 | | | | |
| 1.1.2 alumno01 en h2 | | | | |
| 1.1.3 profesor01 en h1 | | | | |
| 1.2.1 MAC no coincide | | | | |
| 1.2.2 sesión en otro dispositivo (h1, luego h2) | | | | |
| 1.2.3 sesión en otro dispositivo (h2, luego h1) | | | | |
| 1.2.4 MAC suplantada con certificado clonado | | | | |
| 1.2.5 certificado de otra CA | | | | |
| 1.3.1 bloqueo con tráfico | | | | |
| 1.3.2 caducidad de la lista negra | | | | |
