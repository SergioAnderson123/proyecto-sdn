# Comandos utilizados en las pruebas del 23-nov-2026

Orden cronológico. Todo se ejecutó desde la PC del laboratorio por SSH con la llave `vnrt_r5` (sin contraseñas en los comandos).
Las credenciales de ONOS salen del `R5/deploy/.env` de cada nodo (`$ONOS_USER`, `$ONOS_PASS`); aquí no se incluyen.

## 0. Acceso por SSH

Llave dedicada, instalada una sola vez en cada VM (la contraseña de `ubuntu` la escribió la persona, nunca el asistente):

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\vnrt_r5 -N '""'
foreach ($p in 5831,5832) { Get-Content $env:USERPROFILE\.ssh\vnrt_r5.pub -Raw | ssh -p $p -o StrictHostKeyChecking=accept-new ubuntu@10.20.11.14 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && hostname" }
# vm-atacante (10.20.11.203) y server (192.168.201.23): saltando por el switch-borde
Get-Content $env:USERPROFILE\.ssh\vnrt_r5.pub -Raw | ssh -J r5-borde -o StrictHostKeyChecking=accept-new ubuntu@192.168.201.23 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && hostname"
```

Alias en `~/.ssh/config` (bloque `# --- R5 lab ---`): `r5-borde` (10.20.11.14:22), `r5-sec1` (:5831), `r5-sec2` (:5832),
`r5-atacante` (10.20.11.203, `ProxyJump r5-borde`) y `r5-server` (192.168.201.23, `ProxyJump r5-borde`).

| VM | Alias | Acceso |
|---|---|---|
| switch-borde | `r5-borde` | `ssh -p 22 ubuntu@10.20.11.14` |
| node-sec-1 | `r5-sec1` | `ssh -p 5831 ubuntu@10.20.11.14` |
| node-sec-2 | `r5-sec2` | `ssh -p 5832 ubuntu@10.20.11.14` |
| vm-atacante | `r5-atacante` | `ssh r5-atacante` (por el borde) |
| server | `r5-server` | `ssh r5-server` (por el borde) |

## 1. Actualizar los sensores y correr las pruebas unitarias existentes de R5 (156 casos)

```bash
ssh r5-sec1 'cd ~/proyecto-sdn && git status --short'                       # revisar cambios locales antes de actualizar
ssh r5-sec1 'cd ~/proyecto-sdn && git pull --ff-only origin topologia14-deploy'   # igual en r5-sec2
ssh r5-sec1 'bash ~/r5_unit.sh'     # scripts/r5_unit.sh: pytest R5/tests R5/deploy/tests -v y guarda ~/r5_unit_<nodo>_<fecha>.txt
```

En la PC: `python -m pytest R5/tests R5/deploy/tests -v` (necesita `pytest requests pyyaml httpx`). Resultado: 156 de 156 en node-sec-1 y node-sec-2
(un test del compose se corrigió: ahora acepta el servicio opcional `exporter`).

## 2. Visor de 4 paneles (Windows Terminal) para ver los comandos en vivo

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "pruebas de hoy dia\scripts\r5_paneles.ps1"
```

Cada panel hace `ssh <alias> tail -n 30 -F r5_live.log`; los comandos de abajo escriben su salida también en ese `~/r5_live.log` con `| tee -a`.

## 3. Estado de R5 y prueba de bloqueo manual con una IP ficticia (192.0.2.77)

Variables comunes (en node-sec-1): `cd ~/proyecto-sdn/R5 && set -a && . deploy/.env && set +a` y

```bash
export ONOS_URL=http://192.168.201.1:8181/onos/v1,http://192.168.201.2:8181/onos/v1,http://192.168.201.3:8181/onos/v1
export CORE_DEVICES=of:000052ca28945241,of:00007e3401ecd842,of:000076f83813d34e     # borde, sw-core-1, sw-core-2
python3 cli/r5ctl.py estado                       # espejo (6 reglas por core) y bloqueos
python3 cli/r5ctl.py bloqueos
python3 cli/r5ctl.py bloquear 192.0.2.77 --seg 120
curl -s -u "$ONOS_USER:$ONOS_PASS" http://192.168.201.1:8181/onos/v1/flows/application/org.campus.r5    # estado ADDED de cada regla
python3 cli/r5ctl.py desbloquear 192.0.2.77
```

## 4. Métricas de la rúbrica de R5

- **Tasa de detección y tiempos (40 repeticiones del 19-nov):** el CSV ya estaba en switch-borde (`~/r5_repeticiones_20261119_014553.csv`, generado por
  `R5/deploy/repeticiones.py`). Se copió con `scp` y se calcularon medias y p95 por escenario (ver `evidencias/r5_metricas_20261123.md`).
- **Consumo de recursos (sensores en reposo):**
  `ssh r5-sec1 'uptime; free -m; ps -eo comm,%cpu,%mem,rss --sort=-rss | head'` (igual en r5-sec2).

## 5. Diagnóstico de por qué el atacante no llegaba al server (todo de solo lectura hasta el punto 5.4)

```bash
ssh r5-atacante 'nmap -Pn -n -p 22,80,8101,8443 --reason 10.0.0.10'          # todo "filtered" (no-response)
ssh r5-borde 'curl -s -u "$ONOS_USER:$ONOS_PASS" http://192.168.201.1:8181/onos/v1/hosts'  # ONOS solo conoce h1 y h2
ssh r5-borde 'curl -s -u "$ONOS_USER:$ONOS_PASS" http://192.168.201.1:8181/onos/v1/flows/<deviceId>'   # flujos de borde y cores (rutas L3, ETH_DST=02:00:5E:00:00:FE)
ssh r5-server 'ip route; ip route get 203.0.113.103'                           # sin ruta hacia 203.0.113.0/24: salía por la red de gestión
```

Cambios de red que sí se hicieron (parches de laboratorio, ver riesgos en `README.md`):

```bash
# 5.4a  server: ruta de retorno hacia la red del atacante (la ejecutó la persona con su sudo; temporal, se pierde al reiniciar)
ssh -t r5-server "sudo ip route add 203.0.113.0/24 via 10.0.0.254 dev ens4"
# 5.4b  borde: regla temporal en ONOS para entregar al atacante 203.0.113.103 (tabla 60, prioridad 150, appId org.campus.r5.lab, 1 hora)
curl -s -u "$ONOS_USER:$ONOS_PASS" -H "Content-Type: application/json" -X POST \
  "http://192.168.201.1:8181/onos/v1/flows?appId=org.campus.r5.lab" -d @scripts/regla_borde_203.0.113.103.json
```

Para deshacerlas: `sudo ip route del 203.0.113.0/24` en el server y `DELETE /onos/v1/flows/application/org.campus.r5.lab` en ONOS.

## 6. Escaneo SYN real (detección y bloqueo)

Desde vm-atacante (sin `sudo`; `-sT` hace conexiones completas y cruza la regla 9000002):

```bash
T0=$(date +%s.%N)
nmap -Pn -n -sT --max-rate 300 -p 1-300 10.0.0.10
```

Después, en node-sec-1: `r5ctl.py bloqueos`, la antigüedad (`life`) de los flujos en `GET /flows/application/org.campus.r5` para estimar cuándo se instaló
el DROP, y `r5ctl.py desbloquear 203.0.113.103`. Resultado: 177 de 300 puertos quedaron `filtered`; DROP de prioridad 42000 en los 3 switches.

## 7. Tráfico mixto (detección y falsos positivos)

```bash
scp R5/deploy/trafico_mixto.py r5-atacante:trafico_mixto.py
ssh r5-atacante 'timeout 15 python3 ~/trafico_mixto.py --dry-run --duracion 10'     # solo muestra el plan
# En la terminal de vm-atacante (necesita sudo; lo ejecutó la persona con su contraseña):
ssh -t r5-atacante "cd ~ && sudo python3 trafico_mixto.py --destino 10.0.0.10 --duracion 120 --pct-malo 20 --src-malo 203.0.113.150 --semilla 7"
```

Mientras corre, en node-sec-1 se muestreó cada 15 s qué IP estaba bloqueada (flujos de `org.campus.r5` agrupados por `IPV4_SRC`).
Al terminar: se copió el CSV de "verdad" (`evidencias/trafico_mixto_20261123.csv`) y `r5ctl.py desbloquear 203.0.113.150`.

## 8. Pruebas unitarias del Lab 7 (12 pruebas comunes, 119 casos)

Una por una, con encabezado; `scripts/lab7.sh <código>` guarda `~/lab7_<código>_<nodo>_<fecha>.txt`:

```bash
bash ~/lab7.sh R2-01        # igual con R2-02 R2-03 R1-03 R5-01 .. R5-06 (en node-sec-1)
REPO="$PWD" PY=python bash lab7.sh R1-01      # R1-01 y R1-02 en la PC: necesitan ldap3 y fastapi, que los nodos no tienen
```

Todas a la vez en la PC: `python -m pytest R1/shared/policy_mapper/tests/test_lab7_r1.py R2/policy/tests/test_lab7_r2.py R5/tests/test_lab7_r5.py -v`.

| Código | Archivo de pruebas | Casos |
|---|---|---|
| R1-01, R1-02, R1-03 | `R1/shared/policy_mapper/tests/test_lab7_r1.py` | 5 + 31 + 11 |
| R2-01, R2-02, R2-03 | `R2/policy/tests/test_lab7_r2.py` | 14 + 13 + 6 |
| R5-01 .. R5-06 | `R5/tests/test_lab7_r5.py` | 8 + 10 + 4 + 3 + 4 + 10 |

Verificación de que las pruebas detectan fallos (mutaciones: se rompe el código a propósito y se corre la suite). R5: prioridad, lista blanca,
timeout y selector de IP: las 4 se detectaron. R1 y R2: 5 de 6 se detectaron; no se detecta quitar el escape de DN de LDAP porque el simulador de `ldap3` lo neutraliza.

## 9. Cambios de código hechos para poder probar (sin commit al escribir este documento)

| Archivo | Cambio |
|---|---|
| `R1/shared/policy_mapper/intentos.py` (nuevo) | Bloqueo temporal por N intentos fallidos (R1-03). **Aún no está conectado a `/portal/login`.** |
| `R2/policy/policy_tables.py` | `servicios_permitidos`: si dos reglas chocan, prevalece la que deniega. Nueva `evaluar_acceso()` con decisión y motivo |
| `R5/deploy/tests/test_ids.py` | El test del compose acepta el servicio opcional `exporter` |
| `*/test_lab7_*.py` (nuevos) | Las pruebas del punto 8 |

## 10. Pendiente: las 4 reglas de Suricata "sin probar"

`9000010` (sqlmap), `9000011` (C2 interno), `9000020` (UDP flood) y `9000021` (ICMP flood) **no se probaron hoy**. La de sqlmap necesita un servicio HTTP
escuchando en el server y el intento fue bloqueado por el sistema de permisos antes de ejecutarse. Las otras tres requieren un host **interno** como origen
(`HOME_NET -> exterior` o `HOME_NET -> HOME_NET`); no se usa el `server` porque R5 lo bloquearía y cortaría el portal de R1/R2.
