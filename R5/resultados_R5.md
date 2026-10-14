# Resultados de R5 (laboratorio, 13-oct-2026)

Entorno: ONOS (imagen `onosproject/onos`, 3.0.0-SNAPSHOT) y Mininet en Docker; OVS en modo usuario; Suricata 8.0.7 en `hids` con ET Open (53 155 reglas) más `local.rules`; integrador M09 corriendo en Windows contra la REST de ONOS. Topología: s1 Core, s2 y s3 bordes; `hext` 10.0.1.10 (atacante), `hsrv` 10.0.0.10 (víctima), `hids` 10.0.0.99 (sensor). `HOME_NET = 10.0.0.0/24`.

## 1. Espejo selectivo (Bloque 1)

Script: [`lab/espejo.py`](lab/espejo.py). Dos reglas permanentes en el Core, prioridad 30000, acción `OUTPUT NORMAL` + puerto 4 (`hids`):

- entrante: `in_port=3` (uplink de `hext`), `ipv4_dst=10.0.0.0/24`;
- saliente: `ipv4_src=10.0.0.0/24`, `ipv4_dst=10.0.1.0/24`.

Verificación: tras 8 pings de `hext` a `hsrv`, ambos contadores marcaban `n_packets=8` y no hubo pérdida en el tráfico normal (el espejo no interfiere con el reenvío).

## 2. Detección

- Regla propia `LOCAL Port scan SYN` (sid 9000002): 20 SYN en 5 s por origen. Un `nmap -sS` sin regla específica no disparó las firmas ET Open; con la regla propia genera alertas en cuanto se cruza el umbral.
- Una firma ET Open (`ET SCAN Suspicious inbound to mySQL port 3306`) también detectó el barrido a 100 pps, de modo que el escaneo lo ven dos reglas distintas.
- El ruido de LLDP (`Ethertype unknown`, severidad 3) se descarta por el filtro de severidad <= 2.

## 3. Mitigación y tiempos

Corrida limpia (sin DROP previo, `eve.json` vacío, integrador recién arrancado), `nmap -n -sS --max-rate 100 --max-retries 0 -p 1-1000`:

| Evento | Hora (UTC) |
|---|---|
| Inicio del escaneo (`date` en `hext`) | 04:42:26.644 |
| Primera alerta `9000002` en `eve.json` | 04:42:31.791 |
| DROP instalado en s1 (hora del `dump-flows` menos `duration`) | 04:42:32.163 |
| `BLOQUEO` registrado por el integrador (reloj de Windows) | 04:42:32.048 |

- **Alerta -> DROP en el switch: 0.37 s**, medido con el reloj del contenedor.
- El desfase entre el reloj de Windows y el del contenedor es de unos 0.1 s (comparando el `BLOQUEO` del integrador con la instalación vista desde el switch).
- El tiempo hasta la alerta es el de enviar los 20 SYN del umbral: 0.2 s a 100 pps. La ventana de exposición total (primer SYN a DROP efectivo) es del orden de 0.5 s.

## 4. Tasa de detección y fracción que pasa

| Prueba | Resultado |
|---|---|
| 1000 SYN a 100 pps, sostenido | 48 `closed` (pasaron) y 952 `filtered`: **4.8 % pasa** |
| Contador del DROP en s1 | `n_packets=1061` (incluye sondeos de descubrimiento y reintentos de nmap) |
| Barrido en ráfaga: 5000 puertos con `-T4 --min-rate 200` | 5000 `closed`: **100 % pasa** antes del bloqueo |
| Tras el bloqueo: 200 y 5000 puertos | 200 y 5000 `filtered`: 0 % pasa |

Lectura: un ataque sostenido queda cortado tras la primera ráfaga (unas decenas de paquetes), pero un barrido que envía todos sus paquetes en menos de la latencia de detección y mitigación (0.5 s) pasa completo. El IDS fuera de banda no puede evitarlo; solo cortar lo que sigue.

## 5. Bloqueo, expiración y reincidencia

- El DROP se instala en el Core (prioridad 50000, `ipv4_src` del atacante, sin acciones) y se confirma en el switch con `dump-flows`.
- Timeout de 300 s: el integrador volvió a bloquear a la misma IP a los 300 s (23:09:59 -> 23:14:59 -> 23:21:24 hora local) cuando el atacante seguía activo.
- El timeout del flujo es **de inactividad**: un DROP instalado a las ~04:35:06 seguía vivo a los 371 s porque los paquetes bloqueados lo renovaban. Mientras el atacante insista no caduca; se libera tras 300 s de silencio.

## 6. Escalabilidad y TCAM

| Bloqueos activos | Flujos en s1 |
|---|---|
| 0 | 6 (4 base + 2 espejo) |
| 1 | 7 |
| 200 | 207 |

Crecimiento lineal: una entrada de TCAM por IP o prefijo bloqueado. La instalación por lote vía REST (`lab/tcam.py`) tardó 0.06 s para 50 reglas y 0.04 s para 200 (tiempo de respuesta de ONOS, no incluye la propagación al switch).

Suricata: en toda la sesión de pruebas (desde el arranque, 03:56 UTC) `capture.kernel_packets` = 80 719 y `capture.kernel_drops` = 8074, es decir **10.0 % de pérdida en la captura**. El contador es acumulado y mezcla escaneos, pings y el LLDP de ONOS, y el entorno (OVS en modo usuario sobre WSL2) es más lento que uno real. Aun con esa pérdida las detecciones se produjeron, porque el umbral solo necesita 20 SYN, pero el IDS no es fiable para reglas que dependan de ver cada paquete. Mejora posible: aumentar el buffer de captura de AF_PACKET (`ring-size`, `buffer-size`) y los hilos de Suricata. No se ejecutaron pruebas con `tcpreplay`; queda como trabajo futuro.

## 7. Límites declarados

- Un solo atacante y un solo tipo de ataque (escaneo SYN); no se midió tasa de falsos positivos con tráfico legítimo.
- El feed de reputación (Bloque 5) y la reconciliación periódica (Bloque 6) son diseño, no están implementados.
- La escalada de timeouts por reincidencia (Bloque 3) es diseño; el código usa un timeout fijo de 300 s.
- OVS en modo usuario sobre Docker/WSL2: los tiempos absolutos serían menores con el datapath del kernel.
