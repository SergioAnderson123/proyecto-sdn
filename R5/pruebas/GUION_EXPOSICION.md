# Guion de exposición de R5 (demo en vivo) — versión detallada

Duración: **12 a 15 minutos** con la demo (si el tiempo es menor, ver la sección 9, "versión corta"). Cada paso tiene: el comando, **qué ataque es y cómo funciona**, qué regla lo detecta, qué decir, qué señalar y **cómo interpretar el resultado**. Los textos "Di:" son para decirlos con tus palabras.

## Preparación (antes de que llegue el jurado)

El laboratorio debe estar levantado (ver `LEVANTAR.md`) y tener abiertas estas pestañas:

1. Grafana: http://localhost:3000/d/r5-perimetral/r5-seguridad-perimetral?kiosk&refresh=2s&from=now-15m&to=now
2. EveBox: http://localhost:5636
3. Una terminal PowerShell **en la carpeta `R5\pruebas`** (`cd "$(git rev-parse --show-toplevel)\R5\pruebas"`): ahí están `correr_escenarios.ps1` y `demo_cli.ps1`. Solo el paso 11 (pytest) se ejecuta desde la carpeta del repo.

Haz una corrida de calentamiento (`.\correr_escenarios.ps1 -Escenarios scan`) y luego `docker exec r5-lab r5ctl desbloquear 10.0.1.11`: Grafana ya cargó (la primera carga tarda unos 10 s) y el tablero queda sin bloqueos.

---

## 1. Marco: qué amenazas cubre R5 y cuáles probamos

R5 protege el **perímetro**: el tráfico Norte-Sur entre el campus (`10.0.0.0/24` en el laboratorio) y el exterior (`10.0.1.0/24`). Probamos cinco familias de ataque. Las referencias MITRE ATT&CK son orientativas.

| # | Ataque | Qué es y por qué importa al campus | Cómo lo generamos | Qué lo detecta | Dónde se bloquea |
|---|---|---|---|---|---|
| 1 | **Escaneo SYN** (reconocimiento, ATT&CK T1595 *Active Scanning*) | Primer paso de casi todo ataque: el atacante busca qué puertos y servicios están abiertos en nuestros equipos. | `nmap -sS --max-rate 100 -p 1-1000` desde `hext1` | Regla local `9000002`: ≥ 20 SYN en 5 s desde la misma IP | Core (s1) |
| 2 | **Barrido en ráfaga** (reconocimiento rápido) | El mismo escaneo, pero tan rápido que termina antes de que el sistema reaccione. Es el límite de cualquier IDS fuera de banda. | `nmap -T5 --min-rate 3000 -p 1-5000` desde `hext2` | La misma regla `9000002` | Core (s1) |
| 3 | **Varios atacantes a la vez** | Un escaneo distribuido o una campaña coordinada: ¿el sistema mantiene un bloqueo por IP sin perder alertas? | 4 `nmap` en paralelo desde `hext1` a `hext4` | `9000002` por cada IP | Core (s1), una entrada por IP |
| 4 | **Herramienta de ataque web** (ATT&CK T1190 *Exploit Public-Facing Application*) | Escáneres como sqlmap prueban inyecciones SQL en aplicaciones web. Se identifican por su User-Agent y su comportamiento. | `curl -A "sqlmap/1.7"` ×3 desde `hext3` | ET Open `ET SCAN Sqlmap SQL Injection Scan` y local `9000010` (User-Agent) | Core (s1) |
| 5 | **Host interno comprometido** (comando y control, ATT&CK T1071.001 *Web Protocols*) | Un equipo del campus infectado "llama a casa" a un servidor del atacante. Aquí el atacante está **adentro**. | `curl -H "X-R5-TEST-C2: beacon"` desde `hint` hacia `hc2` (10.0.1.50) | Regla local `9000011` sobre la cabecera HTTP | **Borde del host (s3)**, no el Core |
| 6 | **Falso positivo: muchos usuarios tras un NAT** | No es un ataque: 40 usuarios legítimos de una misma IP pública. Sirve para medir el daño colateral del umbral. | 40 `curl` con User-Agent de navegador desde `hnat` en ≈ 3 s | `9000002` (se dispara por error) | Core (s1) |
| 7 | **Inundación SYN** (denegación de servicio, ATT&CK T1498.001 *Direct Network Flood*) | Miles de SYN por segundo para llenar la tabla de conexiones del servidor. | `hping3 -S -p 80 -i u1000` (1 000 SYN/s nominales) desde `hext4` | `9000002` primero; `9000003` (≥ 200 SYN en 2 s) es la regla específica de flood | Core (s1) |

**Reglas de laboratorio.** Los sids `9000003`, `9000010` y `9000011` los escribimos nosotros para tener pruebas deterministas; no sustituyen a ET Open (≈ 53 000 reglas activas), que corre en paralelo y también aporta alertas reales.

---

## 2. Anatomía de un bloqueo: la línea de tiempo

Esta es la explicación que vale la pena dominar, porque todos los resultados salen de aquí.

| Momento | Qué ocurre | Lo medido |
|---|---|---|
| **t = 0** | Llega el primer SYN del atacante al switch Core. El Core lo reenvía **y** lo copia al puerto del IDS (regla de espejo, prioridad 30000). | — |
| **t ≈ 0,3 a 0,6 s** | Suricata ya vio 20 SYN de la misma IP y escribe la alerta en `eve-alerts.json`. A 100 paquetes/s, reunir 20 SYN toma 0,2 s; el resto es procesamiento de Suricata y ráfagas de nmap. | Primera alerta: 0,34 a 1,0 s, mediana ≈ 0,45 s (29 corridas) |
| **+ 0 a 50 ms** | El integrador M09 revisa el archivo cada 50 ms y lee la línea. | Espera de lectura ≤ 50 ms (antes: hasta 500 ms) |
| **+ 5 a 20 ms** | M09 filtra (severidad ≤ 2, lista blanca, deduplicación), decide el punto de bloqueo y hace el `POST` REST a ONOS; ONOS responde `201`. | Lectura → `201`: 5 a 20 ms |
| **t ≈ 0,5 s** | ONOS instala el flujo DROP (prioridad 42000, `ipv4_src` = atacante) en el switch. Desde aquí los paquetes del atacante mueren en el Core. | Primer SYN → DROP: ≈ 0,45 a 0,65 s |

Dos consecuencias importantes:

1. **Siempre pasa un primer tramo** (unas decenas de SYN en un escaneo a 100 pps). Un IDS fuera de banda no puede evitarlo; lo que logra es cortar **todo lo que sigue**.
2. **Después del DROP, Suricata deja de ver al atacante**: el DROP está en el Core con prioridad mayor que el espejo, así que ya no hay copia. Por eso la "captura de Suricata" baja de golpe tras el bloqueo.

---

## 3. Guion paso a paso

### Paso 0 · Contexto (45 s, sin comandos)

**Pantalla:** Grafana, todavía sin actividad.

**Di:**
> R5 es la seguridad perimetral. Un IDS, Suricata, mira una **copia** del tráfico que entra y sale del campus. Cuando detecta un ataque, un programa nuestro, el integrador, le pide a ONOS que instale una regla DROP en el switch, y el atacante queda cortado en el plano de datos.
>
> Suricata no está en medio del tráfico: si se cae, la red sigue funcionando. A cambio, el primer tramo del ataque siempre pasa antes del bloqueo. Hoy vamos a ver cinco tipos de ataque, cuánto deja pasar el sistema en cada uno, qué tan rápido corta el resto, y dónde se equivoca.

**Señala:** las seis tarjetas (Bloqueos activos 0, Switches 3) y el panel "Bloqueos activos en ONOS" vacío.

---

### Paso 1 · Estado inicial (45 s)

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
docker exec r5-lab r5ctl estado
```

**Di:**
> Cinco contenedores: ONOS, el laboratorio con Mininet, Open vSwitch y Suricata, y las vistas, que son Grafana, Prometheus y EveBox. La CLI nos confirma que el espejo hacia el IDS está instalado en las dos direcciones y que no hay bloqueos.

**Señala:** `Espejo al IDS: entrante ... saliente ...` y `Bloqueos activos de R5: 0`.

---

### Paso 2 · Ataque 1: escaneo SYN sostenido (2 min 30 s)

```powershell
.\correr_escenarios.ps1 -Escenarios scan
```

**Qué es el ataque.** Un escaneo SYN ("half-open") envía un paquete SYN a cada puerto. Si el puerto está **cerrado**, el servidor responde RST; si está **abierto**, responde SYN-ACK y el atacante corta con un RST sin completar la conexión; si hay un **filtro**, no hay respuesta. Así el atacante mapea los servicios sin dejar una conexión completa en los registros del servidor. Aquí: 1000 puertos a 100 paquetes por segundo desde `hext1` contra el servidor `10.0.0.10`.

**Qué regla lo detecta.** `LOCAL Port scan SYN` (sid 9000002): `flags:S` con `detection_filter: track by_src, count 20, seconds 5`. Es decir, 20 SYN desde la misma IP en 5 segundos. Un cliente normal toca uno o dos puertos de un servidor; un escaneo toca cientos.

**Di mientras corre (≈ 40 s):**
> Un atacante externo recorre los mil primeros puertos del servidor, a cien por segundo. Nuestra regla salta al llegar a veinte SYN desde la misma IP en cinco segundos. Miren Grafana: aparece la alerta, sube **Bloqueos activos** a uno, y la tabla muestra la IP bloqueada en el switch Core con prioridad 42000.
>
> El panel de reacción mide desde que Suricata ve el paquete hasta que ONOS acepta el DROP: **decenas de milisegundos**. Y el panel de tráfico descartado muestra los paquetes del atacante muriendo en el switch.

**Señala, en este orden:** 1) *Alertas por firma*, 2) *Bloqueos activos en ONOS*, 3) las dos barras de tiempos, 4) *Tráfico descartado*.

**Resultados y cómo leerlos.**

| Medida | Resultado | Cómo interpretarlo |
|---|---|---|
| Primera alerta tras el primer SYN | 0,34 a 1,0 s; mediana ≈ 0,45 s (29 corridas) | Incluye los 0,2 s que tarda nmap en enviar 20 SYN a 100 pps. |
| Alerta → DROP aceptado por ONOS | 15 a 98 ms; mediana ≈ 46 ms (29 corridas) | Es el tramo que controla nuestro código y ONOS. |
| Tramo del integrador (lectura → `201`) | 5 a 20 ms | Decisión más el `POST` REST. |
| Primer SYN → DROP | ≈ 0,45 a 0,65 s | La **ventana de exposición** del ataque. |
| SYN que llegaron al servidor antes del bloqueo | **11 a 28 SYN** (cuatro mediciones válidas: 24, 26, 11 y 24) | Es una cifra pequeña y estable en términos absolutos. |
| Después del bloqueo | `curl` desde el atacante: sin respuesta | El DROP está en el Core. |

> **Cuidado con el porcentaje.** Si te piden "qué porcentaje pasa", explica que depende del denominador: si el atacante sigue enviando durante 35 s son cientos de SYN y el porcentaje es bajo; si lo cortamos a los 5 s es mayor. Lo comparable es el **número absoluto que alcanza al servidor** (decenas), no el porcentaje.

**Límite que debes decir:** esas decenas de SYN pasaron; es el costo de un IDS fuera de banda. Lo que se evita es el resto del barrido.

---

### Paso 3 · Qué ve el analista: EveBox (2 min) — la pantalla se explica **una sola vez**

**Pantalla:** pestaña de EveBox, pulsar *Refresh*, y abrir una alerta con un clic en su fila. Lo ideal es abrir la de **comando y control** (`LOCAL Prueba C2 interno`) si ya la ejecutaste en el calentamiento, porque es la que tiene más campos; si no, abre la de `LOCAL Port scan SYN` y explica solo la parte de arriba.

**Di (bandeja):**
> Esta es la consola que usaría un analista. Cada fila es una firma, el color indica la severidad: rojo es severidad 1, amarillo es 2 y azul es 3. Las azules son ruido, como alertas de checksum o el banner del servidor web de pruebas, y nuestro integrador las **ignora a propósito**: solo actúa con severidad 1 o 2.

**Di (detalle de una alerta), recorriendo la pantalla de arriba abajo:**

| Dónde mirar | Qué es | Qué decir |
|---|---|---|
| Banner rojo o amarillo, `N Occurrences` | La firma que saltó y cuántas veces se repitió | "Una fila agrupa todas las repeticiones; en un escaneo son decenas." |
| **Event**: *Source*, *Destination*, *Protocol* | Quién habla con quién y por qué puerto | "Origen `10.0.0.21` hacia `10.0.1.50:8080`: un equipo interno hablando con el exterior." |
| **Event**: *In Interface* = `hids-eth0` | Por qué interfaz vio el paquete Suricata | "Es la interfaz del espejo: prueba de que Suricata mira una **copia** y no está en medio del tráfico." |
| **ALERT**: *Signature*, *Category*, *Severity*, *Signature ID* | Qué regla, de qué clase y con qué severidad | "Categoría *A Network Trojan was detected*, severidad 1, sid 9000011: es una de nuestras reglas de laboratorio. Las de ET Open tienen otros números." |
| **Flow**: *pkts_toserver / pkts_toclient*, *bytes* | Cuánto tráfico hubo en esa conexión | "Cuatro paquetes en cada sentido: el intercambio inicial **se completó** antes del bloqueo. Eso es el primer tramo que un IDS fuera de banda no puede evitar." |
| **HTTP**: *hostname*, *http_method*, *url*, *http_user_agent*, *status* | Contenido de la petición (solo en HTTP sin cifrar) | "Es la evidencia: método, URL, User-Agent y que el servidor respondió 200." |
| **Alert**: *action* = `allowed` | Qué hizo Suricata con el paquete | "`allowed`: Suricata en modo IDS **no bloquea**. Quien corta el tráfico es nuestro integrador junto con ONOS." |
| Recuadro **JSON** (abajo) | El evento tal como queda en `eve-alerts.json` | "De aquí saca el integrador todo lo que necesita: `src_ip`, `dest_ip`, `direction`, `alert.severity` y `alert.signature_id`." |

**Botones** *Archive* y *Escalate*: son el flujo de trabajo del analista (marcar como revisada o subirla de nivel). No los uses en la demo; solo menciónalos.

**Detalle de zona horaria.** EveBox muestra la hora en tu zona local, y el JSON la trae en UTC (`...T21:01:23Z` equivale a las 16:01 hora de Perú). Si te preguntan por la diferencia de 5 horas, es solo eso.

**Qué significa `N Occurrences`.** EveBox agrupa por firma y **acumula todo el historial**, no solo la última demo. Si ves `2 Occurrences` en la alerta de comando y control es porque ese escenario se ejecutó dos veces desde que se levantó el laboratorio. Para una demo limpia, haz el calentamiento antes y no vuelvas a repetir el mismo escenario sin necesidad; si te preguntan, explica que el contador es acumulado.

**Marca de la copia.** El campo *In Interface = `hids-eth0`* aparece en **todas** las alertas. Es la forma más simple de demostrar que el IDS trabaja sobre el espejo: si estuviera en línea, la interfaz sería la de paso del tráfico.

**Para los demás ataques no repitas esta explicación.** Vuelve a EveBox solo si hace falta y señala estos campos:

| Ataque | Campos que conviene señalar en EveBox |
|---|---|
| Escaneo y ráfaga | *Occurrences* (muchas), y que el **puerto de destino cambia** en cada alerta: eso es lo que distingue un escaneo. |
| Varios atacantes | Cuatro orígenes distintos (`10.0.1.11` a `.14`) con la misma firma. |
| Herramienta web | Bloque **HTTP**: `http_user_agent` = `sqlmap/1.7` y la `url`. Hay **dos** filas: la firma de ET Open y la local. |
| Host interno (C2) | *Source* **interno**, *Destination* externo y *direction* = `to_server`. |
| NAT | Mismo puerto de destino (80) en todas las conexiones. Es el contraste con el escaneo y el argumento para contar **puertos distintos** en vez de SYN. |
| Inundación SYN | *Occurrences* altas con un solo origen. |

---

### Paso 4 · Ataque 2: barrido en ráfaga (1 min 30 s)

```powershell
.\correr_escenarios.ps1 -Escenarios rafaga
```

**Qué es el ataque.** El mismo escaneo, pero a una tasa de unos 2 400 paquetes por segundo (nmap `-T5 --min-rate 3000`): 5 000 puertos en ≈ 2 s. Representa a un atacante que quiere terminar antes de que alguien reaccione.

**Di:**
> Aquí probamos el peor caso: un barrido de cinco mil puertos en dos segundos. En diez repeticiones, el servidor recibió entre **726 y 2 554 de los 5 000 SYN** (de 14 % a 51 %, media 33,5 %) antes de que el bloqueo entrara. La dispersión es grande porque el barrido dura menos de dos segundos y el bloqueo llega a ≈ 0,5 s: lo que pasa depende de en qué momento exacto del barrido cae el DROP.
>
> Lo importante es la lectura honesta: el sistema **no evita** ese primer tramo, y cuanto más rápido ataca el adversario, más paquetes absolutos pasan en esos décimas de segundo. Pero después del bloqueo, un segundo barrido idéntico recibió cero de doscientos.

**Resultado (n = 10, 31/10):** pasan entre 726 y 2 554 SYN de 5 000 (14,5 % a 51,1 %, media 33,5 %). La cifra de 345 (6,9 %) era de una sola corrida y **ya no se usa**.

**Nota para el jurado:** esto corrige una afirmación del informe anterior, que decía que un barrido de ese tipo pasaba completo. Con captura de paquetes en el atacante y en el servidor, no pasa completo.

---

### Paso 5 · Ataque 3: cuatro atacantes a la vez (1 min)

```powershell
.\correr_escenarios.ps1 -Escenarios multi
```

**Qué es el ataque.** Cuatro IP externas (`hext1` a `hext4`) escanean el mismo servidor al mismo tiempo. Prueba que el integrador mantiene un bloqueo **por IP** y no pierde alertas cuando llegan en paralelo.

**Di:**
> Ahora son cuatro atacantes simultáneos. Esperamos un DROP por cada IP, o sea una entrada de TCAM por atacante. En las dos corridas que hicimos los cuatro quedaron bloqueados, entre 0,6 y 1,3 segundos desde que empezaron todos a la vez.

**Resultado:** 4 de 4 bloqueados (resolución de la medición: 0,2 s). La tabla de Grafana muestra cuatro filas y la tarjeta **Bloqueos activos** marca 4. **Pendiente:** 10 y 50 atacantes.

---

### Paso 6 · Ataque 4: herramienta de ataque web (1 min)

```powershell
.\correr_escenarios.ps1 -Escenarios web
```

**Qué es el ataque.** `sqlmap` es una herramienta que automatiza inyecciones SQL. Se identifica por el User-Agent de sus peticiones HTTP. Aquí generamos tres peticiones con `-A "sqlmap/1.7"` hacia el servidor web.

**Di:**
> Aquí demostramos que R5 no solo detecta escaneos de puertos: cualquier firma de severidad uno o dos activa el bloqueo. Esta petición dispara **dos** firmas: una real de ET Open, *ET SCAN Sqlmap SQL Injection Scan*, y la nuestra de laboratorio, que mira el User-Agent. Una vez que el origen está bloqueado, sus peticiones HTTP dejan de recibir respuesta.

**Resultado:** dos firmas distintas (una de ET Open y la local `9000010`); origen bloqueado; HTTP posterior: sin respuesta.

---

### Paso 7 · Ataque 5: host interno comprometido (2 min)

```powershell
.\correr_escenarios.ps1 -Escenarios c2
```

**Qué es el ataque.** Un equipo del campus (`hint`, 10.0.0.21) está infectado y se comunica con su servidor de comando y control (`hc2`, 10.0.1.50). Usamos una petición HTTP con la cabecera `X-R5-TEST-C2`, que detecta la regla `9000011` solo cuando el origen es **interno** y el destino externo (`$HOME_NET → $EXTERNAL_NET`).

**Qué cambia respecto a los casos anteriores.** El integrador decide el punto de bloqueo según la dirección: si el atacante es externo, bloquea en el **Core**; si es interno, le pregunta a ONOS en qué switch está (`GET /hosts`) y bloquea en **su borde**, aquí el switch s3. Así se corta al host sin afectar al resto del campus.

**Di:**
> Ahora el atacante es interno: un equipo del campus que llama a un servidor de comando y control. Fíjense en la tabla: el bloqueo no está en el Core sino en `s3-borde`, el switch donde está conectado ese equipo, porque el integrador lo ubicó con ONOS.
>
> Y encontramos algo que nos interesa para el diseño: el DROP por IP aísla **todo** el tráfico del equipo, también el que iba al servidor interno. Por eso diseñamos una interfaz, que llamamos I11, para que además se revoque su sesión de autenticación en R1 y R2. Esa interfaz está en el diseño; todavía no está implementada.

**Qué mostrar en EveBox para este ataque** (la explicación completa de la pantalla ya se dio en el Paso 3):
- *Source* `10.0.0.21` (interno) y *Destination* `10.0.1.50:8080` (externo), con *direction* = `to_server`: es lo que activa la regla `9000011`, que solo salta de adentro hacia afuera.
- En **Flow**, 4 paquetes en cada sentido y en **HTTP** `status 200`: la petición de comando y control **recibió respuesta** antes de que el bloqueo entrara. Es el primer tramo que no se puede evitar.
- *Category*: *A Network Trojan was detected*, severidad 1.

**Resultado:** bloqueado en `of:0000000000000003` con prioridad 42000. Antes del ataque, `hint` alcanzaba al servidor interno y al C2 (HTTP 200 los dos); después, **ninguno de los dos** responde.

---

### Paso 8 · El hallazgo incómodo: falso positivo con NAT (2 min)

```powershell
.\correr_escenarios.ps1 -Escenarios nat
```

**Qué es.** No es un ataque. Una sede o un laboratorio detrás de un NAT sale a Internet con una sola IP pública; si 40 usuarios abren una página casi a la vez, desde fuera se ven como **una IP con muchos SYN**. Lanzamos 40 `curl` con User-Agent de navegador desde `hnat` en unos 3 segundos.

**Por qué ocurre.** La regla `9000002` cuenta SYN **por IP de origen**, sin distinguir si son 20 puertos distintos (escaneo) o 20 conexiones al mismo puerto 80 (navegación). Es exactamente lo que nos señaló el jurado.

**Di:**
> Aquí mostramos un problema, no un éxito. Estos cuarenta usuarios son tráfico legítimo, pero la regla de veinte SYN en cinco segundos no puede distinguirlos de un escaneo, y los bloquea. Ahora tenemos el caso **reproducido**. El siguiente paso es calibrar el umbral con tráfico real etiquetado, y evaluar contar **puertos distintos** en lugar de SYN, además de una lista blanca en modo "solo alerta" para NAT conocidos.
>
> Encontramos otro caso del mismo tipo: con el User-Agent por defecto de `curl`, dos firmas informativas de ET Open, *POSSIBLE Web Crawl using Curl* y *curl User-Agent to Dotted Quad*, tienen severidad 2 y también bloqueaban a un cliente inofensivo. Eso nos dice que filtrar solo por "severidad menor o igual a 2" no basta: hay que filtrar por categoría de firma o por una lista de identificadores.

**Resultado:** la IP del NAT (`10.0.1.100`) quedó bloqueada por `LOCAL Port scan SYN`; sin respuesta HTTP después. **Criterio de aceptación: 0 bloqueos de tráfico legítimo → no cumple.**

---

### Paso 9 · Ataque 7 (opcional): inundación SYN (1 min)

```powershell
.\correr_escenarios.ps1 -Escenarios flood
```

**Qué es el ataque.** Una inundación SYN envía SYN a gran velocidad sin completar el saludo, para llenar la cola de conexiones pendientes del servidor. Aquí `hping3 -S -p 80 -i u1000` pide 1 000 SYN por segundo (nominales) desde `hext4` al puerto 80; en el laboratorio la tasa efectiva es menor, porque Suricata llegó a ver solo entre 118 y 230 paquetes antes del bloqueo.

**Di:**
> Probamos una inundación SYN, pidiendo mil paquetes por segundo. Hay una regla específica de flood, la de doscientos SYN en dos segundos, pero **no llegó a dispararse**: la regla de escaneo necesita solo veinte SYN, así que bloquea al atacante antes de que se acumulen doscientos. Suricata llegó a ver entre ciento y doscientos treinta paquetes antes de que el bloqueo cortara la copia, y no descartó ninguno.

**Resultado:** entre 15 y 52 alertas de `LOCAL Port scan SYN` según la corrida; atacante bloqueado; **0 descartes de captura** en Suricata; la regla `9000003` no se disparó. Esto **no** mide la capacidad máxima de Suricata: con el bloqueo, el tráfico que llega al IDS es poco. Esa caracterización (escalones de tasa con `tcpreplay`) sigue pendiente. **Límite:** esto no es un DDoS volumétrico (R4): una inundación que sature el enlace de entrada no se resuelve con un DROP en el switch del campus y requiere ayuda del proveedor.

---

### Paso 10 · CLI y reconciliación (1 min 30 s)

```powershell
.\demo_cli.ps1
```

**Qué muestra.** Cuando un bloqueo resulta ser un falso positivo, un administrador lo libera con `r5ctl desbloquear`. Pero el integrador **todavía cree** que esa IP está bloqueada y la ignora, hasta que **reconcilia** con ONOS (cada 10 s): si el flujo ya no existe, la olvida y, si reincide, la vuelve a bloquear.

**Di:**
> Para liberar un falso positivo necesitamos una interfaz de administración. `r5ctl` lista los bloqueos, desbloquea y permite bloquear a mano. El detalle de diseño está en la reconciliación: tras desbloquear, si el atacante vuelve de inmediato el integrador lo ignora unos segundos; cuando compara con ONOS y ve que el flujo ya no existe, lo olvida, y si reincide lo vuelve a bloquear. Lo probamos en vivo.

**Señala:** en el log, `LIBERADA 10.0.1.13: su flujo ya no existe en ONOS` y el segundo bloqueo.

---

### Paso 11 · Pruebas unitarias (1 min)

```powershell
cd (git rev-parse --show-toplevel)
python -m pytest R5/tests -v
```

**Requisito:** `pytest` no viene con Python. Instálalo **antes de la demo** con `python -m pip install pytest`; si no, el comando falla con `No module named pytest`.

**Qué cubren las 40 pruebas.** La función que decide a quién bloquear (13 casos: externo, interno, lista blanca, IPv6, firma sobre la respuesta), el contenido exacto de la regla DROP, el procesamiento (deduplicación, severidad, errores), la clasificación de errores de ONOS (reintentar o descartar), la reconciliación y el estado en disco. No necesitan ONOS ni Suricata: sustituimos ONOS por un doble.

**Di:**
> Escribimos 40 pruebas unitarias para el integrador. Para comprobar que las pruebas sirven, rompimos el integrador a propósito de tres maneras: sin deduplicación, sin lista blanca y con una reconciliación que no olvida. Cada fallo hizo fallar al menos una prueba, y al restaurar el código volvieron a pasar las cuarenta.

**Señala:** la línea final `40 passed`.

---

### Paso 12 · Cierre (1 min)

**Di:**
> En resumen: el lazo de R5 funciona de extremo a extremo y corta un ataque en aproximadamente medio segundo, con una reacción de decenas de milisegundos entre la alerta y el bloqueo, tanto para atacantes externos como para un equipo interno comprometido. Lo que dejamos claro es qué **no** resuelve: no evita el primer tramo del ataque, y hoy bloquea a usuarios legítimos detrás de un NAT.
>
> Lo que sigue: calibrar el umbral con tráfico etiquetado, repetir las mediciones con al menos treinta corridas por escenario, medir escalabilidad con diez y cincuenta atacantes y bajo carga, implementar la escalada por reincidencia, el bloqueo hacia destinos maliciosos y la revocación de sesión en R1 y R2, y repetir las mediciones en VNRT con n = 30 (hoy hay una corrida por escenario) y con R1 y R2 integrados.

---

## 4. Resumen de resultados (para tener a mano)

| Escenario | Resultado | Validez | Lectura |
|---|---|---|---|
| Escaneo SYN sostenido | Primera alerta ≈ 0,45 s; alerta → DROP mediana ≈ 46 ms; primer SYN → DROP ≈ 0,45 a 0,65 s; 11 a 28 SYN alcanzan al servidor | Tiempos: 29 corridas. SYN que pasan: 4 mediciones válidas | Corta el resto del escaneo; pasa un primer tramo. |
| Barrido en ráfaga | 726 a 2 554 de 5 000 SYN (14–51 %, media 33,5 %) alcanzan al servidor | **n = 10** | No pasa completo, pero cuanto más rápido ataca, más paquetes absolutos pasan. |
| Cuatro atacantes | 4 de 4 bloqueados, 0,6 a 1,3 s | Dos corridas | Un DROP por IP sin perder alertas. |
| Herramienta web (sqlmap) | Firma ET Open + local; bloqueado | Varias corridas | Detecta más que escaneos. |
| Host interno C2 | Bloqueo en `s3-borde`; deja de alcanzar al C2 y al servidor interno | Varias corridas | El DROP por IP aísla todo el host. Motiva I11. |
| NAT de 40 clientes | Bloqueado por `9000002` | Reproducible | **Falso positivo: no cumple.** |
| Inundación SYN | Bloqueado por `9000002`; `9000003` no se dispara; 0 descartes de captura (Suricata vio solo 118 a 230 paquetes) | Varias corridas | La regla de escaneo actúa antes que la de flood. |
| Pruebas unitarias | 40 de 40; 3 fallos deliberados detectados | Reproducible | Cobertura de la lógica de decisión y de errores. |

---

## 5. Cómo leer cada panel de Grafana

| Panel | Qué mide | Qué verás en una demo |
|---|---|---|
| **Bloqueos activos** | IPs con un DROP vigente de R5 en ONOS | 0 → 1 (o 4 en `multi`) |
| **Alertas** | Alertas de severidad 1 o 2 acumuladas | Sube con cada ataque |
| **Ruido (sev 3)** | Alertas de severidad 3 que el integrador ignora | Crece sola (checksum, banner del servidor) |
| **Bloqueados** | Paquetes que murieron en un DROP de R5 | Sube mientras el atacante insiste |
| **Descartes IDS** | Paquetes que Suricata no alcanzó a inspeccionar | Debe quedarse en 0 |
| **Switches** | Dispositivos OpenFlow conectados a ONOS | Siempre 3 |
| **Alertas por firma (60 s)** | Qué reglas disparan y con qué intensidad | Una banda por firma |
| **Tráfico descartado (paquetes/s)** | Lo que el atacante sigue enviando y muere en el switch | Un pico justo después del bloqueo |
| **Bloqueos activos en ONOS** | Tabla: IP, switch, paquetes descartados, vida | `s1-core`; en el caso interno, `s3-borde` |
| **Reacción: alerta → bloqueo** | Desde que Suricata ve el paquete hasta que ONOS acepta el DROP | Decenas de ms |
| **Del integrador a ONOS** | Solo el tramo de M09 (decisión + `POST`) | Pocos ms |
| **Espejo hacia Suricata** | Paquetes copiados al IDS por dirección | Se detiene tras el DROP |
| **Captura de Suricata** | Paquetes inspeccionados y descartados | Baja tras el bloqueo (ya no hay copia) |
| **Entradas de flujo por switch** | Uso de TCAM | Una entrada más por IP bloqueada |

---

## 6. Preguntas probables y respuestas

| Pregunta | Respuesta |
|---|---|
| ¿Por qué un IDS fuera de banda y no un IPS en línea? | Para que la red no dependa del sensor: si Suricata cae o se satura, el tráfico sigue. El costo es que el primer tramo del ataque pasa, y lo medimos y declaramos. |
| ¿Qué porcentaje del ataque pasa? | Depende del ataque y del denominador. En números absolutos, decenas de SYN en un escaneo a 100 paquetes/s y unos cientos en un barrido a 2 400 paquetes/s. Lo que cortamos es todo lo posterior. |
| ¿Por qué un barrido más rápido deja pasar más paquetes? | Porque en la ventana de reacción (≈ 0,15 a 0,5 s) el atacante envía más paquetes. El tiempo de reacción es casi el mismo; la tasa del atacante multiplica lo que pasa. |
| ¿Por qué 20 SYN en 5 s? | Un cliente normal toca uno o dos puertos; un escaneo toca cientos. El umbral queda entre ambos y a 100 paquetes/s se cruza en 0,2 s. No está calibrado con tráfico real: es una limitación reconocida. |
| ¿Cómo evitarían el falso positivo del NAT? | Calibrar con tráfico etiquetado (CIC-IDS2017, CTU-13), contar **puertos distintos** en vez de SYN, y lista blanca en modo "solo alerta" para NAT conocidos. |
| ¿Por qué prioridad 42000? | Debe ganarle a las reglas de sesión de R2 (40001) y quedar por debajo del bloqueo temporal de R1 (45000). Reservamos la banda 41000 a 44999 para R5. |
| ¿Por qué el integrador lee cada 0,05 s? | Con 0,5 s la espera de lectura era de unos 250 ms de media; con 0,05 s la reacción bajó de ~240 ms a ~50 ms (medido contra un ONOS simulado). Con ONOS real, la reacción alerta → DROP es de decenas de ms. |
| ¿Por qué el timeout es de inactividad? | Lo medimos: un bloqueo de 300 s seguía vivo a los 371 s porque el tráfico bloqueado lo renovaba. Mientras el atacante insiste no caduca. |
| ¿Cuánta TCAM consume? | Una entrada por IP bloqueada. Medimos 200 reglas el 13 de octubre; el presupuesto propuesto es 1 000 entradas y falta medir ese tramo. |
| ¿Qué pasa si ONOS se cae? | El integrador reintenta con espera creciente de 1 a 30 s y no avanza su posición en el archivo, así que no pierde la alerta. Está probado en las pruebas unitarias; en el laboratorio real queda pendiente. |
| ¿Se probó en VNRT? | Sí: el 31/10 corrimos los 10 escenarios en el slice real (ONOS 2.7 del curso, OVS real, Suricata en el controller), **una sola corrida por escenario**. La detección tarda más que en Docker (primera alerta 0,82 s frente a 0,43 s) y la reacción alerta → bloqueo sigue en decenas de milisegundos (70 ms). Faltan las repeticiones n = 30 en VNRT y R1/R2 en VNRT. |
| ¿Cómo se bloquea un equipo interno sin afectar a los demás? | El integrador lo ubica con ONOS (`GET /hosts`) y pone el DROP en su switch de borde. Falta además revocar su sesión (I11), que está diseñada y no implementada. |
| ¿Y los destinos o URLs maliciosas? | Está en el diseño: DROP por `ipv4_dst` desde un feed de reputación y desde alertas de dominio, con sus límites (CDN, TLS). Todavía no está implementado. |
| ¿Qué pasa con DDoS volumétrico? | Es R4 y no corresponde a nuestro grupo; además un DROP en el campus no ayuda si el enlace de entrada ya está saturado. |
| ¿Por qué no usaron portal cautivo? | Es de R1: los equipos ya traen 802.1X, el portal exige abrir más tráfico antes de autenticar y no sirve para IoT. |

---

## 7. Qué NO decir (cifras no validadas)

- **"4,9 % de los paquetes pasa"** y **"14,4 %"**: salieron de mediciones en las que se releía una captura vieja. Retira esas dos cifras. Usa los números absolutos de la sección 3.
- **"La ráfaga pasa al 100 %"**: era una afirmación del informe anterior; la medición actual dio ≈ 7 % en una sola corrida.
- **n = 30 ya corregido (31/10)**: pasan 23,8 SYN de media (11–28) de ≈ 190 enviados (12,5 %, p95 14,4 %). Di el número absoluto; el porcentaje depende de los 5 s de nmap.
- **"Detección de 0,1 a 0,2 s"**: es el mínimo teórico a 100 paquetes/s. Lo medido desde el primer paquete hasta la alerta es ≈ 0,45 s de mediana.
- **"Validamos en VNRT"** con estadística: hay una sola corrida por escenario (n = 1); no cites percentiles de VNRT. **"Escala a cientos de atacantes"**: no se ha hecho.

Si insisten en un número exacto: *"los tiempos están en el tablero en vivo; las repeticiones n ≥ 30 forman parte del plan de pruebas de SEM-10"*.

---

## 8. Si algo sale mal en vivo

| Problema | Qué hacer y qué decir |
|---|---|
| PowerShell no reconoce `.\correr_escenarios.ps1` | Estás en la carpeta equivocada: `cd` a `R5\pruebas`. |
| Grafana en blanco | Esperar 10 a 20 s ("la primera carga del tablero es lenta") y recargar. |
| Un escenario no bloquea | `docker exec r5-lab r5ctl estado`; si no hay espejo, recrear el laboratorio (ver `LEVANTAR.md`) y repetir. No improvises números. |
| `docker` no responde | Docker Desktop no está abierto; mientras abre, explica la sección 2 (anatomía de un bloqueo). |
| Un bloqueo viejo molesta | `docker exec r5-lab r5ctl desbloquear 10.0.1.11` (cambia la IP por la del bloqueo). |

---

## 9. Todos los comandos, en orden (para copiar y pegar)

Cada bloque dice **desde qué carpeta** se ejecuta. Las rutas completas están en `LEVANTAR.md`.

### A. Antes de la demo (15 a 20 minutos antes)

```powershell
# 1. Abrir Docker Desktop y esperar a que responda
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
docker version --format '{{.Server.Version}}'

# 2. Levantar el laboratorio (desde la carpeta del repo)
cd (git rev-parse --show-toplevel)
docker compose -f R5/lab/docker/compose.yml down
docker compose -f R5/lab/docker/compose.yml up -d --build

# 3. Esperar la línea LISTO (salir con Ctrl+C)
docker logs -f r5-lab

# 4. Instalar pytest (una sola vez)
python -m pip install pytest
```

```powershell
# 5. Calentamiento (desde la carpeta R5\pruebas) y dejar el tablero limpio
cd "$(git rev-parse --show-toplevel)\R5\pruebas"
.\correr_escenarios.ps1 -Escenarios scan
docker exec r5-lab r5ctl desbloquear 10.0.1.11
```

Abrir las dos pestañas: Grafana y EveBox (direcciones en "Preparación").

### B. Durante la demo (desde la carpeta `R5\pruebas`)

```powershell
# Paso 1 · Estado inicial
docker ps --format "table {{.Names}}\t{{.Status}}"
docker exec r5-lab r5ctl estado

# Paso 2 · Escaneo SYN sostenido
.\correr_escenarios.ps1 -Escenarios scan

# Paso 3 · EveBox (sin comandos: pestaña del navegador)

# Paso 4 · Barrido en ráfaga
.\correr_escenarios.ps1 -Escenarios rafaga

# Paso 5 · Cuatro atacantes a la vez
.\correr_escenarios.ps1 -Escenarios multi

# Paso 6 · Herramienta de ataque web
.\correr_escenarios.ps1 -Escenarios web

# Paso 7 · Host interno comprometido
.\correr_escenarios.ps1 -Escenarios c2

# Paso 8 · Falso positivo con NAT
.\correr_escenarios.ps1 -Escenarios nat

# Paso 9 · Inundación SYN (opcional)
.\correr_escenarios.ps1 -Escenarios flood

# Paso 10 · CLI y reconciliación
.\demo_cli.ps1
```

```powershell
# Paso 11 · Pruebas unitarias (desde la carpeta del repo)
cd (git rev-parse --show-toplevel)
python -m pytest R5/tests -v
```

### C. Comandos de apoyo para usar mientras hablas

Útiles si quieres mostrar el estado en la terminal en lugar de (o además de) Grafana:

```powershell
docker exec r5-lab r5ctl estado                          # resumen: bloqueos, espejo y últimas alertas
docker exec r5-lab r5ctl bloqueos                        # tabla de IPs bloqueadas, switch y vida
docker exec r5-lab r5ctl alertas -n 10                   # últimas 10 alertas accionables
docker exec r5-lab r5ctl desbloquear 10.0.1.100          # liberar el falso positivo del NAT
docker exec r5-lab tail -n 12 /var/log/r5/integrador.log # log del integrador (cada BLOQUEO con sus tiempos)
```

Si prefieres no cambiar de carpeta entre el paso 10 y el 11, abre una segunda terminal para las pruebas unitarias.

### D. Después de la demo

```powershell
cd (git rev-parse --show-toplevel)
docker compose -f R5/lab/docker/compose.yml down
```

---

## 10. Versión corta (6 a 7 minutos)

Si tienes poco tiempo: Paso 0 → Paso 2 (escaneo) → Paso 7 (host interno) → Paso 8 (NAT) → Paso 11 (pruebas unitarias) → Paso 12 (cierre). Omite EveBox, ráfaga, varios atacantes, herramienta web, flood y CLI, y menciónalos como "también probados" apoyándote en la tabla de la sección 4.
