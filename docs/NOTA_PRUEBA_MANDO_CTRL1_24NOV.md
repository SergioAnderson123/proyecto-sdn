# Prueba: mover el mando de los switches a ctrl-1 (24-nov-2026)

Pregunta: ¿puede ctrl-1 llevar el mando (maestro) de los switches, o es un nodo que solo sirve de reserva y de 802.1X? Ctrl-1 tiene el disco más lento de los tres (ver `memory`/notas de ONOS del 23-nov: `w_await` 6-10 ms frente a 0,3-0,8 ms y presión de IO `full` 4-5 %).

Cómo se hizo: `device-role <dpid> 192.168.201.1 master` con `consulta_cli_onos.yml` (solo en ctrl-2), un switch cada vez, con el panel (`R2/panel`) y el registrador de ONOS (`registrador_onos.yml`, una ronda cada 20 s en cada controlador) mirando, y `100_medir_acceso.yml` midiendo cada segundo el acceso al servidor desde h1 (8101) y h2 (8001). Horas UTC.

## Resultados

| Hora | Switches en ctrl-1 | Qué pasó |
|---|---|---|
| 05:31 a 05:46 | core-2 (05:31), borde (05:39), acc-2 (05:46) | estable casi todo el rato; **05:45:xx el registrador de ctrl-1 se salta una ronda** y a las 05:46:01 una lectura sale `LENTO` |
| 05:47:19 y 05:47:39 | + core-1 y acc-1 (los 5) | **05:47:50 ctrl-1 deja de contestar** (≈ 14 s): los 5 switches vuelven solos a ctrl-2 (acc-1 pasa ~10 s por ctrl-3), el vigilante ve 2 cables del borde caer y volver (`red_partida`, sin recalcular) |
| 05:55:17 a 06:17 | core-2 y borde | 22 minutos estables, 3 nodos viendo 5 de 5 |
| **06:17:50** | core-2 y borde (se había lanzado acc-2) | **ctrl-1 deja de contestar ≈ 30 s**, el registrador de ctrl-1 se salta la ronda 06:17:59; core-2 y borde vuelven solos a ctrl-2 |
| 06:18 a 13:25 | ninguno (los 5 en ctrl-2, nadie movió nada) | **ninguna ronda del registrador de ctrl-1 incompleta en 7 h**; solo una lectura lenta (11:51:20) |

Tráfico (`100_medir_acceso.yml`):

- 06:17:27 a 06:22:04, que incluye el atasco de las 06:17:50: h1 389/389 OK, h2 394/394 OK.
- 06:25:28 a 13:25:28 (7 horas): h1 25 521/25 521 OK, h2 25 520/25 520 OK. Cero cortes de datos, sin bloqueo de R5 por el ritmo de pruebas.

## Qué se concluye

1. **Mover el mando a ctrl-1 funciona** (cada `device-role` se asienta en segundos, ctrl-1 lee y escribe flujos de sus switches).
2. **Mientras ctrl-1 lleva switches se atasca cada 15-30 minutos** (05:45, 05:47:50, 06:17:50); sin switches, en 7 horas no se atascó ni una vez. Es una correlación con muestras pequeñas (tres atascos), no una causa demostrada: el 05:45 empezó 15 s antes del último `device-role`, así que el comando no es el único disparador.
3. **El fallo es contenido:** ONOS devuelve el mando a ctrl-2/ctrl-3 en segundos, las sesiones y los flujos siguen y el tráfico no se corta (0 `SIN_CAMINO`). Pero durante el atasco el vigilante llegó a ver el borde sin camino a la raíz y hubo `MAESTRO_INVALIDO` mientras acc-1 pasaba por ctrl-3 (que no sirve 802.1X).
4. La causa probable sigue siendo la misma de las notas del 23-nov (disco de ctrl-1 y Raft de Atomix); no se probó. Lo que no se hizo: medir el disco de ctrl-1 **durante** un cambio de mando, ni mover el líder de Raft.

## Decisión práctica

- **Dejar el mando en ctrl-2.** No hay ganancia que justifique atascos de ~30 s cada media hora.
- **ctrl-1 no queda inútil:** es uno de los dos nodos con RADIUS, LDAP y mapper (los únicos que sirven 802.1X) y es miembro del clúster Atomix. Si ctrl-2 cayera, el mando tendría que acabar en ctrl-1 (ctrl-3 no sirve 802.1X); ese caso **no se probó**.
- Si se quiere insistir: medir `iostat`/PSI de ctrl-1 durante un `device-role` de un solo switch, o revisar el líder de las particiones de Atomix (el líder de P3 estaba en ctrl-1).
