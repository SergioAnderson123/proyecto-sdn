# Prueba en vivo de la integración R2 + R5 (laboratorio Docker)

Topología `SDN_ONOS_Setup/shared/mininet/topo_baseR2_userspace.py` (con el host `hids`, 10.0.0.99, en s2 puerto 4):
s1 = acceso (h1 profesor, h2 alumno, h3 soporte; sesiones y tablas de rol de R2), s2 = servidores h4/h5 y **Core de R5**.

## Pasos
1. Stack R1/R2: `docker compose up -d` en `SDN_ONOS_Setup`, `mininet/start-ovs.sh`, `init-onos.sh` (reinicia la app AAA al final) y la topología.
2. Servicios y sesiones: `shared/mininet/servicios_r2.sh` y un `wpa_supplicant` por host (h1, h2, h3).
3. Instalar en el contenedor `mininet` (una vez): `dnf -y install suricata python3-requests`.
4. Copiar a `/root/r5`: `R5/codigo/integrador.py`, `R5/lab/espejo.py`, `R5/suricata/local.rules`, `ids_en_hids.sh` y `tormenta.sh`.
5. `bash /root/r5/ids_en_hids.sh espejo` (espejo en s2, prioridad 41000), `... suricata` (en el namespace de hids) y `... integrador`.
6. `bash /root/r5/tormenta.sh h2 40`: el alumno autenticado abre 40 conexiones a un servicio que SÍ tiene permitido.

## Resultados (04/11/2026)
| Qué se verificó | Resultado |
|---|---|
| Suricata ve el tráfico de usuarios autenticados por R2 | espejo (41000) contó 60 paquetes; Suricata capturó 92 (60 IPv4, 6 flujos HTTP = las consultas de h1 y h3) |
| Por qué el espejo sube a 41000 | A 30000 contó **0** paquetes mientras el flujo 40001 de R2 absorbía el tráfico (+65); a 41000 copió los 30 paquetes del mismo tráfico |
| Detección y bloqueo | regla 9000002 (>20 SYN/5 s); DROP 42000 en s1 aceptado por ONOS a los **61 ms** de la alerta (9 ms hasta la lectura, 52 ms hasta el 201) |
| El DROP de R5 prevalece sobre la sesión de R2 | 236 paquetes de h2 descartados por el flujo 42000 |
| I11: revocación de la sesión | el integrador tomó la MAC de la alerta (`ether.src_mac`) y el mapper respondió `revocada`; desaparecen la sesión y el flujo 40001 y queda la lista negra 45000 |
| Efecto colateral | h2 pierde todo acceso (HTTP 000); h1 y h3 siguen con sus sesiones y servicios (HTTP 200) |

## Hallazgos que se corrigieron en el código
- `init-onos.sh`: la app AAA podía arrancar antes de recibir su configuración (`Remote RADIUS Server: null`, NullPointerException) y dejar sin autenticación a todos los usuarios; ahora se reinicia al final.
- Las alertas necesitan `ethernet: yes` en el eve-log para traer la MAC de origen; I11 usa esa MAC y solo consulta a ONOS si falta.
- La regla de ráfaga marca como ataque a un usuario legítimo que abre muchas conexiones a un servicio permitido (el falso positivo ya conocido del NAT, ahora con un usuario autenticado).
