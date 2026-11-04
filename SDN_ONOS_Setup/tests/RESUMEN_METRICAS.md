# Métricas de R2 · laboratorio Docker (04/11/2026)

Entorno: Docker Desktop (WSL2, 8 vCPU y 7,6 GB), ONOS 2.7, OVS en modo usuario, Mininet con topología de 2 switches y 5 hosts.
Scripts: `SDN_ONOS_Setup/tests/metricas_r2.py` y `shared/mininet/admision_r2.py`. Registro completo: `metricas.log`.

## 1. Entradas de TCAM (rúbrica: "número máximo de entradas TCAM en un switch")
Coinciden ONOS y el switch real (s1: 32 y 32; s2: 6 y 6).

**TCAM(s1) = 5 (base de R1) + 1 (puerto de confianza) + Σ tablas de rol + U**, con U = sesiones activas (1 entrada por usuario).
Tablas de rol (servicios permitidos + ARP + DROP): docente 8, alumno 5, soporte 10 → Σ = 23.

## 2. Escalabilidad por número de sesiones (tramo ONOS → switch)
N flujos de sesión instalados de golpe en s1 (como N usuarios admitidos a la vez):

| Sesiones | ONOS acepta | Visible en el switch | Flujos/s | TCAM s1 | CPU ONOS |
|---|---|---|---|---|---|
| 50 | 177 ms | 292 ms | 171 | 82 | 2,1 % |
| 200 | 32 ms | 149 ms | 1 343 | 232 | 4,6 % |
| 500 | 56 ms | 172 ms | 2 902 | 532 | 1,2 % |
| 1 000 | 83 ms | 218 ms | 4 587 | 1 032 | 11,5 % |

Tras cada prueba el TCAM volvió a 32. ONOS ocupa ≈ 1,43 GB de RAM de base.

## 3. Latencia de admisión real (rúbrica: "latencia para admitir a un usuario válido")
30 admisiones completas (10 por rol): cierre de sesión, nuevo `wpa_supplicant` y medición hasta que la sesión está activa y el primer
servicio permitido responde 200. Medido dentro del contenedor (sin la sobrecarga de `docker exec`), reloj monotónico.

| Rol | OK | Fallos | Sesión activa (media / p95) | Primer servicio responde (media / p95) |
|---|---|---|---|---|
| docente | 10 | 0 | 2 293 / 2 311 ms | 2 313 / 2 331 ms |
| alumno | 10 | 0 | 2 284 / 2 309 ms | 2 302 / 2 328 ms |
| soporte | 10 | 0 | 2 284 / 2 312 ms | 2 301 / 2 327 ms |

**Desglose:** del lado de la red (EAPOL-Start → Access-Accept en ONOS, que incluye RADIUS, LDAP, el webhook del Policy Mapper y la
instalación del flujo) la media es **92 ms** (p95 108 ms, máx. 131 ms). Los ≈ 2,2 s restantes son del lado del cliente
(arranque y temporizadores de `wpa_supplicant`). La latencia atribuible al sistema SDN es, por tanto, ≈ 0,1 s.

## 4. Matriz de aceptación
24 de 24 casos (3 roles × 8 servicios) coinciden con `policy.yaml`.

## Límites que hay que declarar
- OVS en modo usuario sobre Docker: los tiempos absolutos no representan hardware real; en VNRT serán distintos.
- La carga de la sección 2 mide solo ONOS → switch, con lotes; no incluye RADIUS ni LDAP bajo concurrencia.
- La sección 3 mide una admisión a la vez (no hay medición de varias admisiones simultáneas ni de sesiones por segundo en EAP-TLS).
- Con el monitor en vivo abierto (docker stats cada 2 s) la medición se ensucia: las cifras de este resumen se tomaron con una
  corrida limpia, y las primeras corridas con el panel abierto dieron 2 a 22 s por ruido de CPU en un portátil con poca batería.
