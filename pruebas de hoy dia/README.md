# Pruebas de hoy (23-nov-2026): capturas, evidencias y comandos

Grupo 4, TEL354 (PUCP 2026-2). Slice de 14 VM de VNRT, rama `topologia14-deploy`.

- **Comandos usados, en orden:** [`COMANDOS.md`](COMANDOS.md)
- **Capturas:** [`capturas/`](capturas) (imágenes generadas con la salida real; no son capturas de pantalla)
- **Evidencias de texto, CSV y métricas:** [`evidencias/`](evidencias)
- **Scripts:** [`scripts/`](scripts)

## Capturas del Lab 7 (`capturas/lab7_pruebas_comunes/`)

Una imagen por prueba, con encabezado (nodo, fecha, commit, criterio, resultado esperado) y cada caso con su resultado.

| Prueba | Qué valida | Casos | Equipo | Resultado |
|---|---|---|---|---|
| R1-01 | credenciales válidas, rol por grupo LDAP, sesión | 5 | PC (necesita ldap3 y fastapi) | pasa |
| R1-02 | credenciales inválidas e inyecciones, mismo mensaje de rechazo | 31 | PC | pasa |
| R1-03 | bloqueo por N intentos fallidos | 11 | node-sec-1 | pasa |
| R2-01 | decisión rol -> recurso (Administrador PERMIT, Docente DENY) | 14 | node-sec-1 | pasa |
| R2-02 | rol o recurso desconocido: DENY por defecto | 13 | node-sec-1 | pasa |
| R2-03 | traducción a OpenFlow; si chocan, prevalece DENY | 6 | node-sec-1 | pasa |
| R5-01 | lectura de la alerta del IDS | 8 | node-sec-1 | pasa |
| R5-02 | severidad y dirección del tráfico | 10 | node-sec-1 | pasa |
| R5-03 | lista blanca | 4 | node-sec-1 | pasa |
| R5-04 | regla DROP del perímetro | 3 | node-sec-1 | pasa |
| R5-05 | sin duplicar y liberación | 4 | node-sec-1 | pasa |
| R5-06 | resiliencia ante fallas de ONOS | 10 | node-sec-1 | pasa |

## Capturas de las pruebas en vivo (`capturas/pruebas_en_vivo/`)

| Imagen | Qué muestra |
|---|---|
| `00_prueba_bloqueo_192.0.2.77` | bloqueo y desbloqueo manual con una IP ficticia (reconstruida con la salida real) |
| `01_atacante_escaneo` | escaneo SYN real desde vm-atacante: 177 puertos `filtered` |
| `02_bloqueo_detectado` | R5 bloquea a 203.0.113.103 en borde, core-1 y core-2 (449 y 123 paquetes descartados) |
| `03_desbloqueo` | liberación manual con `r5ctl desbloquear` |
| `04_trafico_mixto_vigilancia` | solo 203.0.113.150 (origen malicioso) bloqueada; 203.0.113.103 (normal) nunca |
| `05_trafico_mixto_resumen` | 163 eventos: 1 de 1 origen malicioso detectado, 0 falsos positivos |
| `06_metricas_40_repeticiones` | corrida del **19-nov** (no de hoy): 40 de 40 detectados, mitigación de ~10 ms |

## Resultados clave de R5

- Pruebas unitarias existentes: 156 de 156 en node-sec-1 y node-sec-2.
- Tasa de detección: 100 % (40 de 40, corrida del 19-nov). Mitigación (alerta -> DROP aceptado por ONOS): 10 a 11.5 ms de media.
- Escaneo real de hoy: DROP en 1.5 a 5.5 s (resolución de estadísticas de ONOS ~5 s).
- Tráfico mixto de hoy: 0 falsos positivos a nivel de IP de origen (n = 1; las alertas por regla no se pudieron contar porque `eve-alerts.json` está en un volumen de Docker sin acceso).
- Detalle y limitaciones: [`evidencias/r5_metricas_20261123.md`](evidencias/r5_metricas_20261123.md).

## Cambios que quedaron en la red del laboratorio (parches de prueba)

| Dónde | Cambio | Cómo deshacerlo |
|---|---|---|
| `server` | ruta `203.0.113.0/24 via 10.0.0.254 dev ens4` (temporal, se pierde al reiniciar) | `sudo ip route del 203.0.113.0/24` |
| ONOS, borde | 1 flujo del appId `org.campus.r5.lab` para entregar a 203.0.113.103 (caduca en 1 h) | borrar los flujos de ese appId |
| Sensores | `~/lab7.sh`, `~/r5_unit.sh` y los archivos de prueba subidos a `~/proyecto-sdn` | `git checkout`/`git clean` en el repo del nodo |

La causa de fondo es que el plan de capa 3 no contempla la subred externa (`203.0.113.0/24`): el server no tenía ruta de retorno y el borde no tenía
ruta hacia el atacante. El arreglo definitivo es incluirla en `R2/policy/capa3.py` / su plan.

## Pendiente

- 4 reglas de Suricata sin probar: `9000010`, `9000011`, `9000020`, `9000021` (ver `COMANDOS.md`, punto 10).
- `intentos.py` (bloqueo por intentos) existe y está probado, pero no está conectado al login del portal.
- Falta medir escalabilidad de eventos, consumo bajo ataque y ancho de banda del espejo.
