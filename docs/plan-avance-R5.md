# Plan de avance de R5 a partir de las observaciones del jurado

Fuente: hoja "Grupo 4" de `TEL354-2026-2-Ex1.xlsx` (filas 34-39 y 12). Entregables relacionados: SEM-10 (diseño del plan de pruebas de un módulo), SEM-12 (plan de pruebas y topología), SEM-13/14 (implementación y pruebas unitarias), final el 11-dic.

## Cómo funciona R5 (el trasfondo en seis líneas)

1. El Core reenvía el tráfico Norte-Sur con `NORMAL` y, con reglas de prioridad 41000 (30000 en la primera versión), **copia** cada paquete al puerto de Suricata (espejo). Suricata nunca está en el camino: si cae, la red sigue.
2. Suricata compara la copia con ~53 000 reglas ET Open más las locales y escribe una alerta en `eve-alerts.json`.
3. El integrador (M09) lee las alertas nuevas cada 0,05 s, descarta lo que no importa (severidad > 2, lista blanca, interno↔interno, IP ya bloqueada) y decide **dónde** bloquear: Core si el atacante es externo, borde del host si es interno.
4. Pide a ONOS por REST un flujo `ipv4_src = atacante`, sin acciones (= DROP), prioridad 42000, con timeout de inactividad.
5. ONOS lo instala por OpenFlow en el switch. Desde ese momento los paquetes del atacante mueren en el switch.
6. **Por qué algo siempre pasa:** entre el primer paquete malo y el DROP hay tiempo de detección (hay que ver 20 SYN) + lectura + decisión + instalación (≈ 0,3-0,5 s). Lo que el atacante envíe en ese tiempo llega al servidor. R5 corta lo que sigue; no puede evitar la primera ráfaga.

## Las observaciones y qué hacer con cada una

El orden sugerido es por **valor para la nota / esfuerzo**. "Escenario" es el de `R5/lab/docker/attacks.py`.

| # | Observación (nota actual) | Por qué importa | Qué hacer | Escenario / evidencia | Esfuerzo |
|---|---|---|---|---|---|
| 2 | Tiempo de detección: 263,8 ms (informe) vs 0,1-0,2 s (diapositiva); piden registros reproducibles (12/20) | Una cifra sin evidencia reproducible vale poco. Hoy hay dos números para dos tramos distintos. | Repetir la medición con **un solo reloj** y n ≥ 30: t0 = primer SYN (pcap en el atacante), `timestamp` de la alerta, t_lectura/t_post/t_201 del integrador. Adjuntar pcap + `eve-alerts.json` + log. | `scan` ×30 con `--repeat` (a agregar), `/evidencia/*.pcap` y JSON | Bajo |
| 4 | Mitigación: 21,9 ms vs 0,37 s (13,7/20) | Mismo motivo; ya se sabe la causa (revisión cada 0,5 s). | Medir lectura→201 y alerta→DROP en el switch (`dump-flows`) con `POLL_SEC` 0,5 y 0,05; mostrar que ambas cifras son tramos distintos. | `scan` con `POLL_SEC` distinto | Bajo |
| 1 | Umbral sin calibrar; NAT (12,7/20) | Un falso positivo corta a usuarios legítimos: es el riesgo operativo más serio de R5. | Medir tasa de falsos positivos con (a) navegación normal, (b) muchos clientes tras NAT, (c) descarga de muchas conexiones. Probar alternativas: ventana/umbral mayor, lista blanca "solo alerta" para NAT conocidos, regla de puertos distintos. Elegir con datos. | `nat` + tráfico legítimo de fondo | Medio |
| 5 | Solo se probaron escaneos (14/20) | La rúbrica pide "variedad de ataques" y tasa de detección. | Matriz ataque × (detectado, bloqueado, falso positivo): SYN scan, ráfaga, flood SYN, UA de herramienta web, C2 interno, DNS/ET con pcap real (tcpreplay). | `scan rafaga flood web c2` + pcaps públicos | Medio |
| 6 | Un solo atacante; capacidad del integrador (13/20) | Escalabilidad de eventos y TCAM son criterios de la rúbrica. | 1, 4, 10, 50 atacantes simultáneos; alertas/s que procesa el integrador; TCAM con 200-1000 bloqueos; acumulación y retiro (expiración). | `multi` ampliado + `tcam.py` + prueba de carga del integrador | Medio |
| 7 | 10 % de pérdida de captura sin caracterizar; piden generadores y VMs reales (12,3/20) | Un IDS que pierde paquetes puede no ver el ataque. | Barrer la tasa (100 → 5000 pps y luego tcpreplay a 10-500 Mbps) y medir `kernel_drops` y detección en cada escalón. Ajustar `ring-size`, `buffer-size`, hilos. Repetir en VNRT. | `flood` por escalones | Medio-alto |
| 8 | Cobertura de bloqueo hacia destinos y URLs maliciosos (R0 scope) | El enunciado de R5 dice "desde/hacia IPs o URLs maliciosas". | Implementar C9: DROP por `ipv4_dst` desde un feed y desde alertas de dominio (DNS/SNI); documentar los límites (CDN, TLS). | Prueba con un destino de prueba en `hc2` | Medio |
| 3 | "No saben explicar el detalle de lo ejecutado" | Nota de exposición, no técnica. | Guion de cada prueba (qué comando, qué se mira, qué significa) y que cada integrante lo ensaye. Este panel en vivo ayuda a explicarlo. | `docs/` + ensayo | Bajo |

## Orden de trabajo propuesto

| Fase | Qué | Resultado |
|---|---|---|
| 0 (hoy) | Levantar el laboratorio y correr los 7 escenarios una vez | Evidencia base con capturas y JSON; se ven los números reales de este entorno |
| 1 | Repetición n ≥ 30 de `scan` con la metodología única (#2, #4) | Tabla de tiempos con media, p95 y desviación + pcap/logs |
| 2 | Calibración de falsos positivos (#1): tráfico legítimo, NAT y comparación de reglas | Umbral elegido con datos y lista blanca "solo alerta" |
| 3 | Variedad de ataques (#5) con pcaps reales y tcpreplay | Matriz detección/bloqueo |
| 4 | Escalabilidad (#6, #7): N atacantes, TCAM, escalones de carga | Curvas de degradación y punto de saturación |
| 5 | Bloqueo hacia destinos (#8) y escalada por reincidencia (C6), I11 hacia M05 (C8) | Código + pruebas unitarias para SEM-13/14 |
| 6 | Repetir lo esencial en VNRT con VMs reales | Evidencia del requisito del grupo |

Cada fase termina con: pcap + `eve-alerts.json` + log del integrador + JSON de mediciones + captura del panel. Eso es lo que la ficha de autoevaluación pide ("lo que no se documenta es como si no se hubiera hecho").

## Notas de seguridad del laboratorio

Todos los ataques se ejecutan **dentro de la red virtual de Mininet** de un contenedor Docker, contra hosts de prueba propios (`10.0.0.0/24` y `10.0.1.0/24`). Las firmas locales 9000003, 9000010 y 9000011 son reglas de laboratorio para tener pruebas deterministas; no sustituyen a ET Open. No se debe apuntar ninguna de estas herramientas a redes de la universidad ni de terceros.
