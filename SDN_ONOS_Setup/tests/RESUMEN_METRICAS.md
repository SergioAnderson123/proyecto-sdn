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

## 3b. Admisiones simultáneas (rúbrica: "escalabilidad con respecto al número de sesiones por segundo")
K usuarios reales inician sesión a la vez (5 repeticiones por nivel). Tiempos desde que se lanzan los K `wpa_supplicant`:

| K simultáneos | Primera sesión | Última sesión (media / p95) | Sesiones/s | Fallos |
|---|---|---|---|---|
| 1 | 2 269 ms | 2 269 / 2 304 ms | 0,44 | 0 |
| 2 | 2 285 ms | 2 388 / 2 454 ms | 0,84 | 0 |
| 3 | 2 261 ms | 2 441 / 2 567 ms | 1,23 | 0 |

La latencia casi no crece con la concurrencia (+170 ms para el tercero) y la tasa escala de forma lineal; con 3 usuarios el cuello
de botella sigue siendo el cliente (≈ 2,2 s), no el controlador. **No se pudo pasar de K = 3**: con 5 usuarios (dos por puerto,
usando interfaces macvlan en h1 y h2) solo se admite uno por ráfaga. La causa probable es que el AAA de ONOS asocia la sesión al
puerto (connect point), de modo que dos supplicants simultáneos en el mismo puerto chocan; con K ≤ 3 hay un usuario por puerto.
Para medir más concurrencia hacen falta más puertos de acceso en la topología. Además, `alumno03` no autentica: su certificado se
resuelve en LDAP a la MAC de `alumno02` (…:22) y el mapper lo rechaza por device binding (certificado mal emitido).

## 4. Matriz de aceptación
24 de 24 casos (3 roles × 8 servicios) coinciden con `policy.yaml`.

## Límites que hay que declarar
- OVS en modo usuario sobre Docker: los tiempos absolutos no representan hardware real; en VNRT serán distintos.
- La carga de la sección 2 mide solo ONOS → switch, con lotes; no incluye RADIUS ni LDAP bajo concurrencia.
- La sección 3 mide una admisión a la vez; la 3b llega solo a 3 simultáneas (un usuario por puerto). No hay medición de cientos de sesiones EAP-TLS concurrentes.
- El barrido de la sección 5 usa una carga pequeña: no encontró un umbral de degradación, y los fallos de algunas configuraciones no son atribuibles con certeza a un recurso.
- Con el monitor en vivo abierto (docker stats cada 2 s) la medición se ensucia: las cifras de este resumen se tomaron con una
  corrida limpia, y las primeras corridas con el panel abierto dieron 2 a 22 s por ruido de CPU en un portátil con poca batería.

## 5. Barrido de CPU y RAM (insumo del dimensionamiento del Laboratorio 6)
Carga fija por configuración: 3 admisiones simultáneas reales (3 repeticiones) y 500 flujos de sesión instalados de golpe.
Los límites se aplicaron en caliente con `docker update` y se restablecieron al terminar.

| Configuración | Última admisión (ms) | 500 flujos visibles | Fallos (de 9) |
|---|---|---|---|
| ONOS 8 CPU (referencia) | 2 344 | 274 ms | 0 |
| ONOS 4 CPU | 3 282 | 183 ms | 0 |
| ONOS 2 CPU | 2 626 | 165 ms | 0 |
| ONOS 1 CPU | 2 676 | 268 ms | 2 |
| ONOS 0,5 CPU | 2 394 | 147 ms | 0 |
| ONOS 0,25 CPU | 2 548 | 326 ms | 4 |
| ONOS 3 GB RAM | 2 487 | 142 ms | 4 |
| ONOS 2 GB RAM | 2 379 | 156 ms | 6 |
| ONOS 1,7 GB RAM | 2 455 | 125 ms | 0 |
| Policy Mapper 0,5 CPU | 2 542 | 174 ms | 0 |
| Policy Mapper 0,1 CPU | 3 430 | 141 ms | 4 |

**Lectura (con cautela):**
- Con esta carga pequeña (3 usuarios y 500 flujos) los tiempos de las admisiones exitosas casi no cambian al recortar recursos;
  no se encontró un umbral claro de degradación. Los valores aislados (3 282 ms con 4 CPU) son ruido de un portátil con poca batería.
- Los fallos aparecen desde ONOS 1 CPU y con el Policy Mapper a 0,1 CPU. Los de 3 GB y 2 GB de RAM no tienen relación con la memoria:
  son probablemente arrastre del estado del AAA tras la prueba de 0,25 CPU (se recuperó en la siguiente configuración). Es una hipótesis.
- **ONOS ocupa ≈ 1,45 GB de RSS** en reposo y no cambia con la carga. El Policy Mapper ocupa ≈ 66 MB, RADIUS ≈ 16 MB y LDAP ≈ 24 MB.
- Propuesta para la VM del Controller (ONOS, RADIUS, LDAP y Policy Mapper): **≥ 2 vCPU** para ONOS, **≈ 3 GB de RAM** como mínimo
  (ONOS 2 GB + servicios 0,2 GB + sistema), y **4 vCPU y 4 a 6 GB** si se quiere margen para más usuarios. Es una estimación de
  laboratorio: con tráfico y usuarios reales (cientos de sesiones, EAP-TLS concurrente) habría que repetir la medición en VNRT.

