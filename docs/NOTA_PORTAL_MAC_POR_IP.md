# Nota para R1 (Mariana): el portal cautivo necesita que el mapper resuelva la MAC por IP (23-nov-2026)

Para quien mantiene `R1/shared/policy_mapper/`. Pedido: un cambio pequeño en `/portal/login`, `/portal/logout` y `/portal/estado` para que el portal pueda identificar a los clientes de **otra subred** (capa 3). El parche está hecho, **probado sin red y aplica limpio sobre `topologia14-deploy`** (`git apply --check`), pero no lo he aplicado en el repo porque el archivo es tuyo y lo despliegas tú con tu procedimiento. Lo de R2 (el portal y `portal_auth.py`) ya está hecho.

## 1. Qué pasa hoy (datos en vivo del 23-nov, 21:19 UTC)

- El portal corre en el `server` (`r1-portal`, `https://10.0.0.10:8443`, activo desde las 14:48) y llega al mapper (`192.168.201.254:5000/health/ha` → 200).
- El portal saca la MAC del cliente de **su tabla ARP** (`/proc/net/arp`, con la IP de origen de la conexión). Con la capa 3, h1 (`10.0.1.100`) y h2 (`10.0.2.100`) están detrás del gateway y esa tabla **no los tiene**: el ARP del server solo traía las IP viejas de h1/h2 (`10.0.0.201`, `10.0.0.202`) y los gateways (`10.0.0.254`, `10.99.0.254`).
- Resultado: el portal no puede decir quién entra. Un equipo con sesión 802.1X (h1, `profesor01`) **ve el formulario** en vez de su sesión (el portal ni llega a preguntar `/portal/estado`), y un invitado no puede iniciar sesión (`No se pudo identificar su equipo; reintente`, sin llamar al mapper). En los logs del portal solo hay `GET /` desde `10.0.1.100`, ningún `POST`.
- ONOS sí tiene lo necesario, porque la app DHCP lo registra: `GET /onos/v1/hosts` → `FA:16:3E:B6:96:08 ['10.0.1.100'] of:…354b/1` y `FA:16:3E:71:08:66 ['10.0.2.100'] of:…c741/1`.

## 2. Qué se hizo ya (R2, commit `aa76c56`)

- `R2/policy/portal_auth.py`: `elegir_mac(hosts, ip)` (pura: la MAC del **único** host con esa IP; si hay más de uno distinto no adivina) y `mac_de_ip(ip, onos_url, auth)` (valida que sea IPv4, lee `GET {onos}/hosts` con `HaAsyncClient` si hay HA, guarda la lista 5 s, también un fallo). 11 pruebas en `R2/policy/tests/test_portal_auth_ip.py`.
- `R2/portal_cautivo/server.py`: manda al mapper la **IP de origen de la conexión** (la del socket, no una que declare el cliente) junto con la MAC si su ARP la tiene (vacía si no) en los tres endpoints. 9 pruebas nuevas en `R2/portal_cautivo/tests/test_portal.py` (portal real contra un mapper de mentira). El portal no necesita credenciales de ONOS.
- `R1/deploy/topologia14/portal_actualizar.yml`: copia el `server.py` nuevo al `server` (con copia de seguridad, `-e accion=revertir` vuelve atrás) y reinicia `r1-portal`.

## 3. Qué se pide a R1: el parche del mapper

Archivo: [`docs/parche_mapper_portal_ip.diff`](parche_mapper_portal_ip.diff) (`git apply docs/parche_mapper_portal_ip.diff`). Toca `policy_mapper.py` y añade `tests/test_portal_ip.py` (16 pruebas). No toca `session_manager`, `ha_*` ni la política.

| Endpoint | Antes | Después |
|---|---|---|
| `POST /portal/login` | `{usuario, clave, mac}` (mac obligatoria) | `{usuario, clave, mac?, ip?}`. Si viene `mac`, manda (como hoy). Si no, se resuelve con `portal_auth.mac_de_ip(ip)`. Si no se identifica: `409 No se pudo identificar el equipo; reintente` y evento `rechazado`, **antes de consultar LDAP** (un equipo sin identificar no sirve para probar contraseñas). |
| `POST /portal/logout` | `{mac}` | `{mac?, ip?}`; sin identificar → `{"status": "sin_sesion"}`. Sigue sin tocar sesiones 802.1X. |
| `GET /portal/estado` | `?mac=` (sin token) | `?mac=` igual que antes (sin token); `?ip=` **exige `X-Portal-Token`** (revela qué sesión tiene una IP) y, si no se identifica, devuelve `{"sesion": null}`. |

Pruebas: antes del parche `R1/shared/policy_mapper/tests + R1/mapper-ha/tests + R2/policy/tests` dan **464 passed, 10 skipped**; con el parche, **480 passed, 10 skipped** (las 16 nuevas). Comando:

```sh
PYTHONPATH=R1/shared/policy_mapper:R2/policy python3 -m pytest R1/shared/policy_mapper/tests R1/mapper-ha/tests R2/policy/tests -q
```

## 4. Compatibilidad y orden de despliegue

| | portal anterior (manda solo `mac`) | portal nuevo (manda `mac` vacía + `ip`) |
|---|---|---|
| **mapper anterior** | como hoy | en capa 2 igual que hoy; en capa 3 sigue sin identificar (el campo `ip` se ignora; con `mac` vacía responde `409 No se pudo ubicar el equipo`, y no se instala nada) |
| **mapper nuevo** | como hoy | **funciona en capa 3** |

Orden: **primero el mapper** (con tu procedimiento, una réplica cada vez, verificando sesiones y acceso al gateway), **después el portal** (`PLAYBOOK=portal_actualizar.yml ./lanzar.sh` desde `switch-borde`). Ningún orden rompe nada; solo uno da el resultado.

## 5. Seguridad

- La IP la ve el **socket del portal**; el cliente no la declara. Para que un tercero se hiciera pasar por otro equipo tendría que completar un apretón de manos TCP desde la IP de la víctima (la respuesta va a la víctima).
- Es la misma confianza que ya existe con la MAC: el portal (con el token) dice quién es el cliente. `/portal/estado` por MAC sigue sin token como antes; por IP lo exige.
- Dos hosts con la misma IP en ONOS → no se identifica (no se adivina). IP que no es IPv4 válida → no llega a ONOS.
- Los datos que viajan (usuario, MAC, IP, sesión) son personales: el token y el HTTPS del portal siguen siendo obligatorios fuera del laboratorio.

## 6. Prerrequisito que no pude comprobar: el token del portal

`R1/deploy/.env` de ctrl-1 **no define `PORTAL_TOKEN`** (0 coincidencias), pero el portal del `server` lleva uno en su unidad. No sé de dónde lee el suyo `mapper-ha-prod`. Si el mapper tiene un token distinto del del portal, **todo login daría 403**, con o sin este parche. Para comparar sin mostrarlos (huella de los primeros 12 caracteres):

```sh
# en el server
sudo grep -o 'PORTAL_TOKEN=[^"]*' /etc/systemd/system/r1-portal.service | cut -d= -f2- | tr -d '\n' | sha256sum | cut -c1-12
# en un ctrl (el que corra mapper-ha-prod)
sudo docker exec mapper-ha-prod printenv PORTAL_TOKEN | tr -d '\n' | sha256sum | cut -c1-12
```

Si el segundo está vacío, el mapper no exige token (y entonces `?ip=` queda abierto; conviene definirlo).

## 7. Validación en vivo (cuando estén los dos desplegados)

1. **Datos del usuario en el portal:** desde h1 (802.1X, `profesor01`): `curl -sk https://10.0.0.10:8443/` debe mostrar su sesión («ya inició sesión», rol Docente) y no el formulario.
2. **Login de invitado (capa 3):** parar el agente de h2 (`72_suplicante.yml -e parar=true -e objetivo_host=h2-alumno`), entrar con `guest01` / `guest123` en el portal desde h2: `/sessions` debe mostrar la sesión `Portal` de la MAC de h2 (tabla 50, caduca a los 1800 s), con su flujo 40001 en `of:…c741` puerto 1, y `/events` un `admitido`. Cerrar sesión por el portal debe borrar el flujo.
3. **Rechazos:** una IP que ONOS no conoce → `409 No se pudo identificar el equipo` y evento `rechazado`; contraseña mala → 401.
4. **Alcance:** con la sesión de invitado, `nc -zv -w 2 -s <IP de h2> 10.0.0.10 8101` debe seguir el catálogo de R2 (el invitado solo llega a `university_portal`).

## 8. Riesgos y cosas a vigilar

- `mac_de_ip` lee **toda** la lista `/onos/v1/hosts` (ONOS no filtra por IP) y la guarda 5 s. Con unos cientos de hosts es poco; en una red grande habría que indexar por IP o pedir a ONOS un filtro.
- La lista de hosts de ONOS puede traer una IP de una concesión ya vencida hasta que el vigilante o la app DHCP la olviden (la app libera al bajar el puerto; el vigilante olvida a los 900 s). En ese hueco dos hosts podrían figurar con la misma IP: el parche lo trata como ambiguo y rechaza.
- Con `mac` y `ip` a la vez manda la MAC: si el portal tuviera una entrada ARP vieja para esa IP, usaría esa MAC. En la slice de capa 3 no pasa porque el cliente no es vecino de capa 2 del portal.

## 9. Marcha atrás

Mapper: revertir el parche (`git apply -R docs/parche_mapper_portal_ip.diff`) y tu procedimiento de despliegue. Portal: `PLAYBOOK=portal_actualizar.yml ./lanzar.sh -e accion=revertir`. Ninguno de los dos depende del otro para volver atrás.
