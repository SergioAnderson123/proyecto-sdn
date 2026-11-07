# Evidencias SEM-10 · R5 (capturas del 31/10/2026)

Carpeta: `capturas-sem10/`. Criterios de la rúbrica del parcial para R5.

| Criterio de la rúbrica | Evidencia | Qué se ve |
|---|---|---|
| Tiempo para detectar | `01-scan.png`, `evidencia/resultados-scan_n.json` | Primer SYN → alerta: 0,43 s de media (σ 0,05; p95 0,50), n = 30 |
| Tiempo para mitigar | `01-scan.png`, `02-multi.png`, `06-grafana.png` | M2 (lectura → 201 de ONOS): 6–13 ms; Grafana: 54.6 ms alerta → bloqueo, 5.8 ms integrador → ONOS |
| Variedad de ataques | `01-scan` · `02-multi` · `03-web` · `04-c2` | Escaneo SYN, 4 atacantes, herramienta web (sqlmap), C2 interno |
| Fracción que se deja pasar | `RESULTADOS.md` | Sostenido n = 30: 23,8 SYN pasan de media (11–28), 12,5 % de ≈ 190 enviados, p95 14,4 %. Ráfaga n = 10: 14–51 % (media 33,5 %) |
| Falsos positivos | `05-nat.png` | NAT con clientes legítimos bloqueado |
| Alertas y severidad | `07-evebox.png` | Bandeja con colores por severidad |
| Flujos TCAM y espejo | `06-grafana.png` | Entradas por switch, paquetes espejados |
| Pruebas unitarias | `09-pytest.txt` | 40 passed |
| CLI de administración | `08-r5ctl-estado.txt` | Estado, espejo y bloqueos |

## Pendiente de medir
- Repetir R3, R4 y la ráfaga con n = 30.
- Escalabilidad (10 y 50 atacantes) y volumen de tráfico: no hay medición todavía.

## R3 y R4 (extensión del laboratorio, 31/10/2026)
Según la rúbrica, R3 es "ataques encubiertos en la intranet" y R4 es "DDoS brute-force en la intranet". Ambos son tráfico **interno ↔ interno**, que R5 excluía. Cambios:

- **Espejo interno:** tercera regla (prio 30000) en el Core: `ipv4_src` y `ipv4_dst` en 10.0.0.0/24 → NORMAL + puerto del IDS. Solo ve tráfico que cruza el Core (entre switches de borde distintos).
- **Reglas R4 en Suricata:** 9000020 (UDP, 300 paquetes en 2 s por origen) y 9000021 (ICMP echo, 200 en 2 s). R3 reutiliza 9000002 (escaneo SYN), que ya aceptaba origen interno.
- **Integrador:** alerta interno → interno bloquea al origen en su switch de borde (variable `INTRANET_BLOCK`, activa por defecto); lista blanca intacta. Pruebas unitarias: 42 passed.

| Escenario | Captura | Resultado (una corrida) |
|---|---|---|
| `r3` escaneo interno hint → servidor | `10-r3-intranet.png` | 22 de 388 SYN llegaron (5,7 %); primera alerta 0,42 s; DROP en s3 |
| `r4udp` inundación UDP | `11-r4-udp.png` | DROP a 0,22 s del inicio; lectura → 201 ≈ 13–17 ms; 0 descartes de Suricata |
| `r4icmp` inundación ICMP | `12-r4-icmp.png` | DROP a 0,22 s; lectura → 201 ≈ 14–19 ms |

Límites que hay que decir:
- Son **una corrida** cada uno, sin repetición n = 30.
- R4 se mide como **tasa de paquetes** por origen, no como ancho de banda en bits. `hping3 -i u200` no alcanza los 5000 pps nominales: Suricata capturó entre 4 y 625 paquetes por corrida.
- El tráfico entre hosts del mismo switch de borde no cruza el Core y no se espeja.
- El DROP por IP aísla todo el host (motivo de I11).
- ARP y otros ataques de capa 2 no se ven con un espejo IPv4.
