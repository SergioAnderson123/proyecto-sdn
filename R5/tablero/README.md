# Tablero en vivo de R5

Página única que reúne lo que hoy se mira en varias terminales SSH: las alertas de Suricata de
`node-sec-1` y `node-sec-2` en dos columnas, y la tabla de `r5ctl bloqueos` (DROP activos en los
tres switches, con sus paquetes descartados), todo en vivo por Server-Sent Events.

**Solo lee, salvo una única acción de escritura: desbloquear.** No instala ninguna regla nunca. El
botón "Desbloquear todo" (o "Quitar" por fila) retira bloqueos, y solo acepta IP que el propio
tablero ya vio en la tabla: nunca una IP que escriba el navegador. Pensado para no tener que salir
a una terminal SSH solo para limpiar entre pruebas durante la demo.

## Dónde corre y por qué

**En switch-borde, nunca en un nodo del clúster ONOS (`nodo-ctrl-*`).** Dos motivos:

1. **Conectividad ya probada.** `medicion_r5.py` y `repeticiones.py` ya le hacen SSH desde ahí a
   los dos sensores; el tablero reutiliza esas mismas credenciales y esa misma red.
2. **No acopla la demo con el plano de control.** `nodo-ctrl-*` aloja el clúster ONOS (Raft) y los
   servicios de R1/R2, compartidos con tus compañeros; un servicio propio ahí competiría por CPU y
   red justo con lo que decide la red, y además tocarías una VM que no es solo tuya.

## Arranque manual, NO como servicio systemd

A diferencia del portal de R1/R2 (que debe estar siempre arriba porque intercepta tráfico real),
este tablero solo existe para la demo. Se prende a mano antes de la sustentación y se apaga al
terminar:

```bash
cd ~/proyecto-sdn
python3 R5/tablero/server.py
```

Para apagarlo: `Ctrl+C`. Después abre `http://<IP de switch-borde>:8090/` desde tu navegador (misma
red que usas para SSH a las VM).

**No lo corras a la vez que mides recursos o escalabilidad** (`medicion_r5.py recursos` /
`escala`): el tráfico SSH propio del tablero sesgaría esas mediciones. Apágalo antes de repetirlas.

## Variables de entorno

| Variable | Por defecto | Qué hace |
|---|---|---|
| `R5_TABLERO_PUERTO` | `8090` | Puerto donde escucha la página |
| `R5_TABLERO_INTERVALO_BLOQUEOS` | `3.0` | Segundos entre consultas a `r5ctl bloqueos` |
| `R5_PASS` / `~/.r5_pass` | — | Contraseña de `ubuntu`, igual que `repeticiones.py` |

Sin `sshpass` instalado, intenta con claves SSH (mismo comportamiento que las demás herramientas
de `R5/deploy`).

## Qué muestra

- **Dos columnas de alertas** (node-sec-1 / node-sec-2), con un punto verde/rojo que indica si la
  conexión SSH de ese sensor está arriba.
- **Tabla de bloqueos activos**: IP, switch, prioridad, paquetes descartados, vida y cuándo vence.
  Sale vacía cuando no hay ningún DROP de R5 (estado inicial).
- Botón "Limpiar" por columna (solo limpia la pantalla, no toca nada en las VM).
- Botón "Desbloquear todo" (y "Quitar" por fila) en la tabla de bloqueos: pide confirmación antes
  de ejecutar, y manda `r5ctl desbloquear <ip>` por SSH a un sensor. Solo actúa sobre IP que el
  tablero ya tiene en su tabla; una IP que no esté ahí se rechaza (HTTP 400) sin tocar nada.

## Si el punto parpadea entre verde y rojo

Es que el usuario `ubuntu` no está en el grupo `docker`, así que `docker exec` sin `sudo` falla con
`permission denied while trying to connect to the docker API`. El tablero ya envuelve sus comandos
remotos en `sudo` (la contraseña va por stdin, nunca como argumento), así que si vuelve a parpadear,
el error real queda impreso en la consola donde corre `server.py`.

## Pruebas

`R5/tablero/tests/test_parseo.py` prueba, sin red ni VM, el parseo de `r5ctl bloqueos`, el backoff
y la difusión a los navegadores conectados:

```bash
cd R5 && python3 -m pytest tablero/tests -q
```
