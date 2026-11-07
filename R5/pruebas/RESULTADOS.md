# Resultados del laboratorio R5 (30-oct-2026)

Entorno: Docker en Windows, ONOS 2.7, Mininet con Open vSwitch en modo usuario, Suricata 8.0.7 con ET Open (53 320 reglas activas) más `R5/suricata/local.rules`, integrador `R5/codigo/integrador.py` de la rama `r5/integrador-llsd` (prioridad 42000, `POLL_SEC` 0,05 s). Todos los ataques son entre hosts virtuales del contenedor.

Capturas del panel al final de cada escenario: `capturas/`. Pcap del espejo y JSON de cada medición: `evidencia/`. Reproducir: `correr_escenarios.ps1`.

| # | Escenario | Resultado medido | Captura |
|---|---|---|---|
| 1 | Escaneo SYN sostenido, 100 pps (hext1) | Primera alerta a 0,40-0,49 s del primer SYN; alerta → POST aceptado por ONOS 16-69 ms; DROP en el Core a ≈ 0,46-0,50 s. Con n = 30 (31/10/2026): primera alerta 0,43 s (σ 0,05; p95 0,50); alerta → 201 de ONOS 48,6 ms de media (p95 76,2); lectura → 201 6,1 ms (p95 7,2); primer SYN → DROP 0,48 s (p95 0,55). **Llegaron al servidor 23,8 SYN de media (11 a 28) de ≈ 190 enviados: 12,5 % (σ 1,9; p95 14,4 %)**; el porcentaje depende de cuánto dura nmap (se corta a los 5 s), la cifra estable es el número absoluto. HTTP desde hext1 queda sin respuesta. | `01-scan.png` |
| 2 | Barrido en ráfaga, 5000 puertos a ≈ 2 400 pps (hext2) | **n = 10 (31/10): llegaron al servidor entre 726 y 2 554 de 5000 SYN (14,5 % a 51,1 %, media 33,5 %)** antes del bloqueo. La corrida única de 345 (6,9 %) se retiró. | `02-rafaga.png` |
| 3 | Cuatro atacantes a la vez (hext1-4) | 4 de 4 bloqueados a los 0,63 s (resolución de muestreo 0,2 s), un DROP por IP. | `03-multi.png` |
| 4 | Herramienta web (User-Agent sqlmap, hext3) | Dos firmas: `ET SCAN Sqlmap SQL Injection Scan` (ET Open) y la local 9000010; bloqueado. | `04-web.png` |
| 5 | Host interno comprometido (hint, 10.0.0.21) | Bloqueado en el borde `of:...03` (prio 42000), ubicado con `GET /hosts`. Después **tampoco llega al servidor interno**: el DROP por IP aísla todo su tráfico IPv4. | `05-c2.png` |
| 6 | 40 clientes legítimos tras un NAT (hnat, User-Agent de navegador) | **Falso positivo confirmado:** `LOCAL Port scan SYN` (20 SYN en 5 s) bloqueó la IP del NAT. | `06-nat.png` |
| 7 | SYN flood ≈ 1 000 pps (hext4) | Bloqueado por la regla de escaneo (20 SYN), antes de llegar a los 200 SYN de la regla de flood 9000003, que no se disparó. Descartes de Suricata durante la prueba: 0. | `07-flood.png` |

## Hallazgos que cambian lo dicho en el informe

1. **El barrido rápido no pasa al 100 %.** `resultados_R5.md` dice que un barrido de 5000 puertos pasa completo; aquí pasó entre el 14 % y el 51 % (n = 10, media 33,5 %). El 100 % no se sostiene, pero la fracción es alta y muy variable.
2. **Las firmas ET INFO y HUNTING de severidad 2 bloquean tráfico legítimo.** Con el User-Agent por defecto de `curl`, `ET INFO POSSIBLE Web Crawl using Curl` y `ET HUNTING curl User-Agent to Dotted Quad` bastaron para bloquear a un cliente inofensivo (en el primer intento de los escenarios 5 y 6). El filtro "severidad ≤ 2" no alcanza: hay que filtrar por `classtype`/categoría o por lista de sids. Es la pregunta pendiente de si las reglas "hunting" bloquean o solo registran.
3. **El umbral de 20 SYN en 5 s bloquea un NAT** con 40 usuarios abriendo una página en 3 s (escenario 6). Confirma la observación n.º 1 del jurado con un caso reproducible.
4. **Lo que sí se sostiene:** el tiempo alerta → POST es de decenas de ms y el DROP queda instalado a ≈ 0,5 s del primer SYN; en un ataque sostenido pasan ≈ 24 SYN antes del DROP (n = 30). El 4,9 % anterior se retiró: salía de releer una captura vieja.

## Corrida en VNRT (31/10/2026, ~23:20)

Los 10 escenarios se corrieron en el slice real de VNRT, **una sola vez cada uno (n = 1)**. Entorno: ONOS 2.7 de la imagen del curso con el bootstrap del Lab 5; sw1 (Core), sw2 y sw3 (bordes) con OVS real; Suricata en la VM `controller` escuchando el puerto de espejo del Core; h1 como servidor web (10.0.0.1), h2 como equipo interno comprometido, h3 como atacante interno y h4 con alias 10.0.0.101-104, .110 y .120 como atacantes externos, NAT y C2. Scripts: `correr_vnrt.ps1` y `vnrt_paso*.ps1`; resultados en `evidencia-vnrt/`; capturas en `capturas-vnrt/`.

| # | Escenario | Resultado medido en VNRT | Captura |
|---|---|---|---|
| 1 | Escaneo SYN sostenido (nmap, 40 s) | Primera alerta a 0,82 s del primer SYN; alerta → 201 de ONOS 70 ms (lectura → 201 19,5 ms); primer SYN → DROP 0,89 s. **Llegaron al servidor 27 de 559 SYN: 4,8 %.** DROP en el Core. | `01-scan.png` |
| 2 | Barrido en ráfaga, 5001 SYN en 2,21 s | **Llegaron 624 de 5001: 12,5 %** antes del bloqueo. | `02-rafaga.png` |
| 3 | Cuatro atacantes a la vez | 4 de 4 bloqueados, a 1,33, 1,55, 1,55 y 1,77 s. | `03-multi.png` |
| 4 | Herramienta web (sqlmap) | Dos firmas (`ET SCAN Sqlmap SQL Injection Scan` y la local de User-Agent); bloqueado, sin respuesta posterior. | `04-web.png` |
| 5 | Host interno comprometido (10.0.0.2, C2) | Bloqueado en su borde (sw2) con prioridad 42000; después no responde ni al C2 ni al servidor. 0 descartes de Suricata. | `05-c2.png` |
| 6 | 40 clientes legítimos tras un NAT | **Falso positivo también en VNRT:** `LOCAL Port scan SYN` bloqueó la IP del NAT. | `06-nat.png` |
| 7 | SYN flood | 192 alertas de `LOCAL Port scan SYN`; bloqueado; 356 paquetes capturados y **0 descartes** de Suricata. | `07-flood.png` |
| 8 | R3: escaneo interno | 25 de 407 SYN llegaron (6,1 %); primera alerta 0,85 s; primer SYN → DROP 0,91 s; lectura → 201 37 ms; DROP en el borde sw3. | `08-r3.png` |
| 9 | R4: inundación UDP interna | DROP a 1,49 s (primera alerta 1,31 s); 778 alertas; lectura → 201 50 ms; 0 descartes. | `09-r4udp.png` |
| 10 | R4: inundación ICMP interna | DROP a 1,06 s (primera alerta 0,87 s); 1106 alertas; lectura → 201 95,7 ms; 0 descartes. | `10-r4icmp.png` |

Contraste con Docker (sección anterior, escenario 1 con n = 30): la primera alerta tarda **0,82 s en VNRT frente a 0,43 s** y el primer SYN → DROP **0,89 s frente a 0,48 s**; la reacción alerta → 201 sigue en decenas de milisegundos (70 ms frente a 48,6 ms de media). En números absolutos pasan 27 SYN frente a 23,8 de media: del mismo orden. Con n = 1 en VNRT esto es una comparación de órdenes de magnitud, no estadística. Los demás hallazgos (NAT bloqueado, ráfaga que pasa en parte) se repiten en VNRT.

## Límites de estas mediciones

- Un solo experimento por escenario salvo el escenario 1, repetido n = 30 (`evidencia/resultados-scan_n.json`); los demás siguen en una corrida.
- Los tiempos "primer SYN → DROP" se calculan con el reloj del contenedor y la hora de lectura/respuesta del integrador; no se sondeó `dump-flows` cada 10 ms.
- OVS en modo usuario sobre Docker/WSL2: sirve para comparar escenarios, no como rendimiento de hardware.
- Las firmas 9000003, 9000010 y 9000011 son de laboratorio; no se probó todavía tráfico de PCAPs públicos.
- VNRT: una sola corrida por escenario (n = 1). Pendiente repetir con n = 30 y probar R1/R2 en VNRT.
