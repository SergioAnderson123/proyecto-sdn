# Portal cautivo (invitados y equipos sin 802.1X)

Página web con usuario y contraseña. Valida contra **LDAP** (a través del Policy Mapper) y el Policy Mapper instala la **misma sesión de R2** (flujo 40001 + tabla de rol) que crea 802.1X. Diseño: [Tabler](https://github.com/tabler/tabler) (MIT, CSS local).

```
cliente sin sesión ──HTTP a 10.0.0.40:8003──▶ s1 reescribe (MAC/IP/puerto) hacia el portal ──▶ 302 al formulario
cliente ──POST /login──▶ portal ──▶ Policy Mapper /portal/login ──▶ LDAP (bind) + grupos ──▶ ONOS (flujo de sesión)
```

| Pieza | Dónde |
|---|---|
| Servidor del portal (`server.py`, solo biblioteca estándar) | contenedor `mininet`, IP 10.0.0.60 unida a s2 por un veth (`portal-up.sh`) |
| Flujos de desvío/ARP/retorno | `policy_tables.flujos_portal`, configurados en `policy.yaml` → `portal:` |
| Validación LDAP, rol por grupo, ubicación del host | `policy_mapper/portal_auth.py`, endpoints `/portal/login`, `/portal/logout`, `/portal/estado` |
| Rol del invitado | grupo LDAP `invitado` → rol `invitado` → tabla 50 (solo `university_portal`) |

## Cómo funciona el desvío
Un usuario **sin sesión** solo puede: resolver ARP, abrir TCP al portal (10.0.0.60:8080) y abrir `10.0.0.40:8003`, que el switch reescribe hacia el portal; la respuesta se reescribe de vuelta, así que el navegador cree haber hablado con el destino original y recibe una redirección al formulario. Todo lo demás se descarta (no hay flujo). Con sesión, el flujo de R2 (40001) gana al desvío (35000): nunca se redirige a quien ya inició sesión. Cada rol puede seguir alcanzando el portal para ver su estado y **cerrar sesión**.

Solo se desvía el HTTP hacia los destinos de `portal.desvios` (hoy el portal universitario): el HTTP hacia cualquier destino no se puede redirigir sin un proxy/DNS, y HTTPS no se puede redirigir sin avisos del navegador.

## Probar
```bash
docker compose -f R1/compose.yml up -d --build policy_mapper     # incluye ldap3
docker cp R2/portal_cautivo mininet:/opt/portal
docker exec mininet bash /opt/portal/portal-up.sh
# desde un host sin sesión (mininet> h1 ...)
curl -i http://10.0.0.40:8003/                      # 302 -> http://10.0.0.60:8080/?destino=...
```
Usuarios de prueba: `guest01` / `guest123` (invitado), y cualquiera de LDAP (`profesor01` / `password123`…). El rol sale del grupo LDAP (`portal.grupos`).

## Duración de la sesión
Las sesiones del portal vencen: `portal.duracion_s` en `policy.yaml` fija los segundos por rol (invitado 30 min; el resto 8 h). Un lazo del Policy Mapper revisa cada 10 s, borra el flujo 40001 y registra «sesión vencida por tiempo»; el equipo vuelve a quedar solo con acceso al portal. Las sesiones de 802.1X no vencen: terminan con el Accounting-Stop. El panel muestra «vence en N min».

## Seguridad
- La **MAC** la lee el portal de su tabla ARP (mismo segmento), nunca del cliente; el Policy Mapper exige `X-Portal-Token` si `PORTAL_TOKEN` está definido (definirlo fuera del laboratorio).
- LDAP caído ⇒ se rechaza. Un bind sin contraseña (anónimo) se rechaza explícitamente.
- Una sesión por usuario y por MAC (409 si ya existe, igual que en 802.1X). Los bloqueos de R1/R5 (45000/42000) siguen por encima del portal.
- Límites: el portal envía la contraseña en HTTP plano (laboratorio; en producción, HTTPS) y el ARP sin sesión es abierto porque el REST de ONOS 2.7 no empareja `ARP_TPA`.
