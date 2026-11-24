# Despliegue del mapper con el parche del portal y el `PORTAL_TOKEN` (24-nov-2026)

Responde a los requisitos **P1** (parche del mapper del portal) y **P3** (`PORTAL_TOKEN`) de `docs/DISENO_EXCEPCIONES_POR_SESION.md` y sigue el procedimiento de R1 (una réplica a la vez, con copia de vuelta atrás). Horas en UTC.

## Qué se desplegó

- Commit `915173c`: aplica `docs/parche_mapper_portal_ip.diff` (`/portal/login`, `/portal/logout` y `/portal/estado` aceptan `ip`; la MAC se resuelve con `portal_auth.mac_de_ip`; `?ip=` exige `X-Portal-Token`).
- Lleva también lo que había en `R2/policy` desde `6726bc4`: `portal_auth.mac_de_ip` (`aa76c56`) y el «deny-wins» de `policy_tables` (`feb6aef`). La política de producción (`/r2_policy/policy.yaml`, 36 reglas, igual en las tres réplicas) no tiene reglas contradictorias, así que las reglas de R2 que instala el mapper no cambian.
- `PORTAL_TOKEN` nuevo, el mismo en las tres réplicas (huella `9f0a63c24e8a`). No está en Git: en cada controlador queda en `/home/ubuntu/sdn-mapper-ha/portal-token.secret` (0600).
- Suite local con el parche: **666 passed, 10 skipped** (mapper, HA, R2 y portal).

## Antes del cambio

- Las tres réplicas `ready`, revisión 239607, 2 sesiones iguales (`profesor01`, `alumno01`), 0 reinicios. Código desplegado = paquete de `6726bc4` (comparado por SHA-256 dentro de cada contenedor). Token vacío en las tres (`e3b0c44298fc`).
- Diagnóstico (`diagnostico_mapper_ha.yml`): esperas de transacción de hasta 11,4 s en ctrl-2 y ~6 s en ctrl-1/ctrl-3 (≈20 % de las últimas 100 por encima de 1 s); avisos de etcd lento sobre todo en ctrl-1; sin `maintenance deferred` en las últimas 2 h.
- Maestro de los switches de acceso: ctrl-2. Orden elegido: ctrl-3 → ctrl-1 → ctrl-2.

## Cómo se actualizó cada réplica

`R1/mapper-ha/deploy/actualizar_replica.py` (como root en el controlador; no muestra secretos):

1. Comprueba que la réplica esté `ready`.
2. Copia la carpeta de código montada en `/app` a `<carpeta>.rollback-<stamp>`; para el contenedor y lo renombra a `mapper-ha-prod-rollback-<stamp>` (sin reinicio automático).
3. Copia los `.py` nuevos en `/app` (conserva `policy.yaml`).
4. Crea `mapper-ha-prod` con la misma configuración que el anterior (montajes, red `sdn` y alias `policy_mapper`, puerto, variables) y el `PORTAL_TOKEN` nuevo.
5. Espera `ready` y comprueba la huella del token dentro del contenedor.

`actualizar_replica.py revertir <stamp>` deja la réplica como estaba. Stamp usado: `20261124a`.

| Réplica | Hora | `ready` tras | Sesiones | `?ip=` sin token / con token |
|---|---|---|---|---|
| ctrl-3 | 04:57 | 6 s | 2, iguales | 403 / 200 |
| ctrl-1 | 04:59 | 2 s | 2, iguales | 403 / 200 |
| ctrl-2 | 04:59 | 6 s | 2, iguales | 403 / 200 |

Después: `POST /sessions/logout` por el gateway con una MAC sin sesión → `{"status":"sin_sesion"}` 200; `95_diagnostico_lazos.yml` → sin tormenta.

## Prueba del flujo 40001 (tras ctrl-3)

Se borró solo el flujo de h1 (`50665496667556007`, `of:0000e6925d87354b`) y se reautenticó h1 con `wpa_cli reauthenticate`. ctrl-3 (código nuevo) atendió el webhook:

- 04:58:42 «Falta flujo 40001… se reinstala antes de confirmar el login».
- 04:58:44 flujo inyectado (la inyección tardó ~1,9 s).
- 04:58:45 «No se confirma login… flujo no verificado (RuntimeError)» → `POST /webhook` **503**.
- El flujo quedó `ADDED` a los 5,9 s y h1 recuperó el servicio (`8101` → `Connection refused`); h2 siguió con servicio.

Es el mismo resultado que con `6726bc4`: la reparación funciona, pero la verificación no cabe en sus 3 s (la inyección consume buena parte) y el webhook devuelve 503 aunque EAP termina en SUCCESS. **El hallazgo sigue abierto** (ver «Trabajo pendiente recomendado» en `NOTA_HALLAZGOS_MAPPER_HA_23NOV.md`).

## Para Dani

h1 está autenticado ahora mismo por 802.1X (`profesor01`, sesión `922bf7cb`, flujo 40001 `ADDED` en sw-acc-1 puerto 1, `8101` → `Connection refused`), así que sirve tal cual para el paso 1 de la validación del portal (`NOTA_PORTAL_MAC_POR_IP.md`, §7): con el token puesto en el portal, `curl -sk https://10.0.0.10:8443/` desde h1 debe mostrar su sesión y no el formulario. El mapper ya resuelve su IP: `GET /portal/estado?ip=10.0.1.100` con el token devuelve `profesor01`, rol Docente, 802.1X.

## Token en el portal y validación (05:10–05:13)

- En el `server` se cambió solo la línea `Environment="PORTAL_TOKEN=…"` de `/etc/systemd/system/r1-portal.service` (copia en `r1-portal.service.bak-20261124a`) y se reinició `r1-portal`. La huella del token en la unidad y en el proceso es `9f0a63c24e8a`, la misma que en el mapper. El `server.py` ya era el nuevo (manda la IP de origen).
- Validación de `NOTA_PORTAL_MAC_POR_IP.md` §7:

| Prueba | Resultado |
|---|---|
| h1 (802.1X, `profesor01`) abre `https://10.0.0.10:8443/` | «Sesión activa · Hola, profesor01 · Rol: Docente · método: 802.1X» (ya no ve el formulario). El mapper resolvió `?ip=10.0.1.100` con el token |
| Contraseña mala desde h1 | «Usuario o contraseña incorrectos», `POST /portal/login` 401 y evento `rechazado` |
| h2 sin agente 802.1X → `guest01` por el portal | «Sesión activa · Rol: Invitado · método: Portal»; `/sessions` con `guest01`, VLAN 50; flujo 40001 en `of:…c741` con VLAN 50; evento `admitido` («Portal cautivo: tabla 50») |
| Alcance del invitado | `8003` (portal universitario) → `Connection refused` (llega); `8101` (notas) → `timed out` (bloqueado) |
| Cerrar sesión por el portal | evento `logout`, la sesión y el flujo 40001 desaparecen |
| Volver a arrancar el agente de h2 | `alumno01` de nuevo en VLAN 20, `8001` → `Connection refused` |

Al terminar: las tres réplicas `ready` con 2 sesiones (`profesor01`, `alumno01`), sus flujos 40001 `ADDED` vistos desde los tres ONOS, y `95` sin tormenta. Justo después de instalarse, ctrl-2 mostró el flujo nuevo de h2 en `PENDING_ADD` unos segundos aunque ya pasaba tráfico.

## Pendiente

- La lentitud de etcd y de las transacciones no cambió con este despliegue; la causa sigue sin conocerse.
