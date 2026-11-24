# Cómo levantar el laboratorio R5 y mostrar el avance

Todos los comandos son para **PowerShell**. Usa dos rutas:

- `REPO` = `<raíz del repositorio>` (compose, código, pruebas unitarias)
- `PRUEBAS` = `<raíz del repositorio>\R5\pruebas` (scripts de ataque y capturas)

Rama del trabajo: `r5/integrador-llsd`.

---

## 0. Antes de empezar (una vez por sesión)

1. Abrir **Docker Desktop** y esperar a que diga *Engine running*.

```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

2. Comprobar que Docker responde:

```powershell
docker version --format '{{.Server.Version}}'
```

Si imprime una versión (por ejemplo `29.1.3`), sigue. Si da error, espera 30 s y repite.

---

## 1. Levantar todo

```powershell
cd (git rev-parse --show-toplevel)
git switch r5/integrador-llsd
docker compose -f R5/lab/docker/compose.yml down
docker compose -f R5/lab/docker/compose.yml up -d --build
```

El `down` previo es importante si los contenedores ya existían (por ejemplo después de reiniciar Docker Desktop): así el contenedor `r5-lab` se crea limpio y no arrastra una topología vieja. Los volúmenes (reglas de Suricata, historial de EveBox, evidencias) se conservan.

Arranca 5 contenedores: `r5-onos`, `r5-lab` (Mininet + Open vSwitch + Suricata + integrador), `r5-evebox`, `r5-prometheus` y `r5-grafana`.

**Esperar a que el laboratorio esté listo** (2 a 4 minutos la primera vez; el integrador, la topología y Suricata se levantan solos):

```powershell
docker logs -f r5-lab
```

Está listo cuando aparece esta línea (salir con `Ctrl+C`):

```
[r5-up] LISTO: Grafana http://localhost:3000 · EveBox http://localhost:5636 · escenarios http://localhost:8080
```

Comprobación rápida de que todo está arriba:

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
docker exec r5-lab r5ctl estado
```

`r5ctl estado` debe mostrar `Espejo al IDS: entrante ... saliente ...` y `Bloqueos activos de R5: 0`.

---

## 2. Abrir las vistas

| Vista | Dirección | Para qué |
|---|---|---|
| **Grafana** (tablero "R5 · Seguridad perimetral") | http://localhost:3000/d/r5-perimetral/r5-seguridad-perimetral?kiosk&refresh=2s&from=now-15m&to=now | Métricas en vivo. La primera carga tarda unos 10 s. |
| **EveBox** | http://localhost:5636 | Alertas de Suricata (bandeja con colores por severidad) |
| **Panel de escenarios** | http://localhost:8080 | Narración paso a paso de cada ataque |
| Prometheus | http://localhost:9090 | Solo si hay que depurar métricas |

Dejar Grafana y EveBox abiertos en pestañas distintas antes de lanzar los ataques.

---

## 3. Lanzar los ataques (todos dentro de la red virtual del laboratorio)

Los scripts `.ps1` están en la carpeta `R5\pruebas`, **no** en la del repo. Si PowerShell dice que no reconoce `.\correr_escenarios.ps1`, estás en otra carpeta: ejecuta primero este `cd`.

```powershell
cd "$(git rev-parse --show-toplevel)\R5\pruebas"
```

Un escenario cada vez (cada uno reinicia el estado y deja una captura en `capturas\`):

```powershell
.\correr_escenarios.ps1 -Escenarios scan
.\correr_escenarios.ps1 -Escenarios rafaga
.\correr_escenarios.ps1 -Escenarios multi
.\correr_escenarios.ps1 -Escenarios web
.\correr_escenarios.ps1 -Escenarios c2
.\correr_escenarios.ps1 -Escenarios nat
.\correr_escenarios.ps1 -Escenarios flood
.\correr_escenarios.ps1 -Escenarios r3      # R3: escaneo interno
.\correr_escenarios.ps1 -Escenarios r4udp    # R4: inundación UDP interna
.\correr_escenarios.ps1 -Escenarios r4icmp   # R4: inundación ICMP interna
```

O varios seguidos:

```powershell
.\correr_escenarios.ps1 -Escenarios scan,multi,web,c2
```

O directamente, sin capturas:

```powershell
docker exec r5-lab python3 /opt/r5/lab/docker/attacks.py scan
```

| Escenario | Qué muestra | Duración aprox. |
|---|---|---|
| `scan` | Escaneo SYN sostenido: alerta, DROP en el Core y fracción que pasa | 50 s |
| `rafaga` | Barrido muy rápido: lo que alcanza al servidor antes del bloqueo | 20 s |
| `multi` | Cuatro atacantes a la vez: un DROP por IP | 30 s |
| `web` | Herramienta web (User-Agent sqlmap): firma ET y firma local | 20 s |
| `c2` | Host interno comprometido: bloqueo en su switch de borde | 20 s |
| `nat` | **Falso positivo**: 40 clientes legítimos tras un NAT quedan bloqueados | 20 s |
| `flood` | SYN flood y pérdida de captura de Suricata | 30 s |

Repetición para estadísticas (30 corridas con media, desviación y p95, unos 11 minutos):

```powershell
docker exec r5-lab python3 /opt/r5/lab/docker/attacks.py scan_n 30
```

El resultado queda en `/evidencia/resultados-scan_n.json` dentro del contenedor. Para traerlo a la carpeta del proyecto:

```powershell
docker cp r5-lab:/evidencia/. "$(git rev-parse --show-toplevel)\R5\pruebas\evidencia"
```

---

## 4. Demostrar la CLI de administración (`r5ctl`)

Demo guiada de desbloquear, ignorar durante la reconciliación y volver a bloquear:

```powershell
cd "$(git rev-parse --show-toplevel)\R5\pruebas"
.\demo_cli.ps1
```

Comandos sueltos:

```powershell
docker exec r5-lab r5ctl estado
docker exec r5-lab r5ctl bloqueos
docker exec r5-lab r5ctl alertas -n 10
docker exec r5-lab r5ctl bloquear 10.0.1.200 --seg 120
docker exec r5-lab r5ctl desbloquear 10.0.1.200
```

---

## 5. Pruebas unitarias del integrador (no necesitan el laboratorio)

```powershell
cd (git rev-parse --show-toplevel)
python -m pip install pytest
python -m pytest R5/tests -v
```

Resultado esperado: `42 passed`.

---

## 6. Orden sugerido para mostrar el avance (unos 8 minutos)

1. Mostrar Grafana y EveBox vacíos (sin bloqueos, sin alertas accionables).
2. `.\correr_escenarios.ps1 -Escenarios scan` y señalar en Grafana: sube **Bloqueos activos**, aparece la alerta por firma y el tiempo alerta → bloqueo (decenas de ms).
3. Abrir EveBox y mostrar las alertas de esa IP.
4. `.\correr_escenarios.ps1 -Escenarios c2`: el atacante es interno y se bloquea en su switch de borde.
5. `.\correr_escenarios.ps1 -Escenarios nat`: mostrar el **falso positivo** y decir que es el hallazgo que motiva calibrar el umbral.
6. `.\demo_cli.ps1`: mostrar `r5ctl` y la reconciliación.
7. `python -m pytest R5/tests -v`: 42 de 42.

---

## 7. Apagar

Detener sin perder datos (las reglas de Suricata y el historial se conservan):

```powershell
cd (git rev-parse --show-toplevel)
docker compose -f R5/lab/docker/compose.yml down
```

> No uses `down -v` salvo que quieras empezar de cero: borra los volúmenes y obliga a volver a descargar las reglas ET Open (1 a 2 minutos) y a perder el historial de EveBox.

---

## 8. Si algo falla

| Síntoma | Qué hacer |
|---|---|
| PowerShell dice que no reconoce `.\correr_escenarios.ps1` o `.\demo_cli.ps1` | Estás en la carpeta equivocada: `cd "$(git rev-parse --show-toplevel)\R5\pruebas"` y repite. Si dice que la ejecución de scripts está deshabilitada: `powershell -ExecutionPolicy Bypass -File .\correr_escenarios.ps1 -Escenarios scan`. |
| `docker` dice que no puede conectar con el motor | Docker Desktop no está abierto: ejecutar el paso 0 y esperar. |
| Grafana se ve en blanco o con "Loading plugin panel..." | Esperar 10 a 20 s. Si sigue: `docker restart r5-grafana` y recargar. |
| Grafana no refleja un cambio del tablero | `docker restart r5-grafana` |
| `r5ctl estado` dice `Espejo al IDS: NO instalado` o ONOS no ve los switches | Recrear el laboratorio: `docker compose -f R5/lab/docker/compose.yml up -d --force-recreate lab` y esperar la línea `LISTO` en `docker logs -f r5-lab`. |
| Un escenario no encuentra el host (`host hext1 no existe`) | La topología no terminó de levantar: esperar a `LISTO`; si no aparece, recrear `lab` como en la fila anterior. |
| `curl` desde un host del laboratorio no responde pero `ping` sí | El *offload* de checksum no se desactivó: recrear `lab` (la topología lo desactiva al crearse). |
| Quedó un bloqueo viejo y molesta | `docker exec r5-lab r5ctl bloqueos` y `docker exec r5-lab r5ctl desbloquear IP` |
| Ver el log del integrador | `docker exec r5-lab tail -f /var/log/r5/integrador.log` |
| Ver errores de Suricata | `docker exec r5-lab tail -f /var/log/r5/suricata.log` |

---

## 9. Dónde está cada cosa

| Qué | Ruta |
|---|---|
| Integrador M09 | `R5/codigo/integrador.py` |
| CLI de administración | `R5/cli/r5ctl.py` |
| Pruebas unitarias | `R5/tests/test_integrador.py` |
| Laboratorio (compose, topología, escenarios) | `R5/lab/docker/` |
| Exportador de métricas y tablero de Grafana | `R5/lab/docker/monitoring/` |
| Reglas de Suricata | `R5/suricata/local.rules` |
| Resultados y capturas | `R5/pruebas/RESULTADOS.md`, `capturas/`, `capturas-profesional/`, `evidencia/` |
| Documentos entregables | `SEM-09_LLSD_R5_Seguridad_Perimetral.docx`, `SEM-10_Plan_de_pruebas_M09_R5.docx` |
