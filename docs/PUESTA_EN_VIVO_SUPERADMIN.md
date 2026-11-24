# Puesta en vivo del equipo `superadmin` (opción A: namespace en h1) — runbook y bitácora

Requisito **P11** de `docs/DISENO_EXCEPCIONES_POR_SESION.md` (sección 4.7 y 4.7.1). **Decisión del equipo (Dani, 24-nov-2026): opción A**, un equipo dedicado `superadmin01` que corre como un *namespace de red con una macvlan* sobre la interfaz de datos de **h1**. Este documento dice qué se hace, en qué orden, cómo se comprueba cada paso y cómo se vuelve atrás, y lleva la bitácora de lo que se ejecutó de verdad. **Cada cambio en vivo se anota aquí con su hora y su evidencia.**

> **Cambio que afecta al 802.1X de siempre: el webhook del mapper.** El código del webhook (`/webhook` de `R1/shared/policy_mapper/policy_mapper.py`) **ya está cambiado en el repositorio** (commit «superadmin (P11)», `1c8d7bd` en el remoto) y **NO está desplegado**: las tres réplicas siguen con el código anterior (comprobado el 24-nov a las 16:08 UTC, `rol_de_802_1x` aparece 0 veces en `/app/policy_mapper.py` de ctrl-1, ctrl-2 y ctrl-3). Detalle en la sección 3. **No se despliega sin avisar y sin OK.**

## 1. Qué es y qué no toca

- **Qué es.** Una identidad de equipo (`superadmin01@example.org`) con **su propia MAC**, **su propio certificado** y **su propio suplicante** (`wpa_supplicant`), dentro de un namespace `sa` con una macvlan `sa0` sobre `ens4` de h1. Es la única que entra al panel de la red (`admin_panel`, puerto 8480) y da de alta y de baja las excepciones por cuenta. No entra por el portal.
- **Qué no toca.** La identidad de h1 (hoy `alumno02`), su agente `r1-agente` ni el filtro `r1-eapol-bcast`. Las sesiones se distinguen por puerto **y MAC** (flujo 40001 = `in_port` + `eth_src`), así que varias MAC en el puerto 1 de sw-acc-1 coexisten; ya lo usan las pruebas de DHCP (`83_prueba_dhcp.yml`, macvlan sobre `ens4`).
- **Persona y equipo separados.** El equipo se identifica por el certificado, su MAC y su ubicación fija en LDAP, no por una cuenta de persona (decisión del 24-nov, `docs/DISENO_EXCEPCIONES_POR_SESION.md` 1.1).

## 2. Estado verificado antes de empezar (24-nov-2026, 16:08 UTC, solo lectura)

Evidencia: `Evidencias_Mapper_HA_2026-11-23/24_superadmin_estado_previo.txt` y `25_playbook_superadmin_validacion.txt`.

| Qué | Resultado |
|---|---|
| Mapper | ctrl-1, ctrl-2 y ctrl-3 `ready`, revisión 356986, 2 sesiones iguales: `alumno01@example.org` (h2, VLAN 20) y `alumno02@example.org` (h1, VLAN 20, MAC `fa:16:3e:b6:96:08`) |
| Código del mapper en las réplicas | el paquete de `ed9fa57` (límite de intentos presente, `LIMITE_PORTAL_CUENTA` 4 veces); **sin** `rol_de_802_1x` |
| h1 | kernel 6.8.0-38, `ens4` con `10.0.1.100/24` (DHCP) y MAC `fa:16:3e:b6:96:08`; `wpa_supplicant` 2.10; `ip`, `tc`, `dhclient`; módulo `macvlan` cargado; 0 namespaces; `r1-agente` y `r1-eapol-bcast` activos |
| Filtro EAPOL de h1 | `tc` flower en la salida de `ens4`: `dst_mac 01:80:c2:00:00:03`, `eth_type 888e`, acción `pedit` (cambia el destino a broadcast) |
| Cambios de Dani el mismo día (repositorio) | h1 arranca como `alumno02` por defecto; `login_manual=true` (el agente no autentica solo; la identidad se elige en su interfaz web) |

## 3. Impacto en el 802.1X de siempre: el webhook

**Qué cambió** (`rol_de_802_1x`, que llama `auth_webhook`): antes el webhook decidía la VLAN con tres `if` por subcadena sobre la cabecera `X-Rol-LDAP` que manda RADIUS (`"docente" in rol` → 10 «Docente», `"alumno"` → 20 «Alumno», `"soporte"` → 40 «Administrador», si no, 403 «Rol sin acceso autorizado»). Ahora la tabla (= VLAN) y el rol salen de `roles` en `policy.yaml`, con **coincidencia exacta** del nombre.

**Lo que no cambia** (comprobado con pruebas, `R1/shared/policy_mapper/tests/test_rol_8021x.py`): docente → 10 «Docente», alumno → 20 «Alumno», soporte → 40 «Administrador», por el webhook completo y con la `policy.yaml` del repositorio; un rol desconocido, `Sin Rol` o `invitado` sigue dando 403 sin instalar sesión; la respuesta a RADIUS (`Tunnel-Private-Group-Id` = la VLAN) es la misma.

**Lo que podría notarse, para decidirlo con Dani antes de desplegar:**
1. **Coincidencia exacta.** Un valor como `docente-algo` ya no casa. RADIUS manda solo `docente`, `alumno` y `soporte`, así que en la práctica no cambia nada.
2. **Dependencia de `policy.yaml`.** Antes la asignación estaba en el código; ahora depende de que `roles` esté en la política de cada réplica (en producción ya están `docente` 10, `alumno` 20, `soporte` 40, `invitado` 50, iguales a lo escrito antes). Si una réplica tuviera una `policy.yaml` distinta, daría otro resultado (P10: la misma política en las tres).
3. **Rol nuevo.** `superadmin` (tabla 45) solo funciona cuando la política desplegada lo trae y RADIUS lo manda; mientras tanto un equipo con ese rol recibe 403.

**Cómo se desplegaría (cuando haya OK):** réplica por réplica (ctrl-3, ctrl-1, ctrl-2) con `R1/mapper-ha/deploy/actualizar_replica.py`, con respaldo y vuelta atrás, comprobando salud, sesiones y que h1 y h2 reautentiquen con su VLAN de siempre.

## 4. Pasos, en orden

Cada paso que toca LDAP, RADIUS, el mapper o h1 pide el **OK de Gabriel** antes. «Quién» es quien lo ejecuta.

| # | Paso | Cómo se comprueba | Vuelta atrás |
|---|---|---|---|
| 1 | **Emitir el certificado** de `superadmin01` en el borde, **fuera del repositorio** (`/home/ubuntu/superadmin-certs`), con `R1/radius/raddb/certs/emitir_cliente_nuevo.sh superadmin01 <carpeta>`; copiar a `R1/radius/raddb/certs` solo la parte pública de la base de la CA (`index.txt`, `serial`, `NN.pem`) | `openssl verify` contra `ca.pem`; CN `superadmin01@example.org`; el script rechaza un usuario que ya existe | borrar la carpeta; no se tocó la CA |
| 2 | **Mapper**: desplegar el código nuevo (incluye el webhook, sección 3) y la misma `policy.yaml` con `superadmin` en las tres réplicas; añadir `admin_panel` con la IP del server (10.0.0.10) a `policy.local.yaml` de cada réplica | `/health/ha` `ready`, mismas sesiones, h1 y h2 con VLAN 20; `sha256sum` igual de `policy.yaml` y `policy.local.yaml` | `actualizar_replica.py revertir <stamp>` |
| 3 | **LDAP** (ctrl-1, replica a ctrl-2): grupo `superadmin` y entrada `superadmin01` con `description` = `02:5a:d0:00:00:01`, `physicalDeliveryOfficeName` = `of:0000e6925d87354b`, `roomNumber` = 1 | `ldapsearch` solo de esas dos entradas; el resto sin cambios (`entryCSN`) | `ldapdelete` de las dos entradas |
| 4 | **RADIUS**: desplegar la rama `superadmin` de `sites-available/default` en los tres nodos, de uno en uno | RADIUS arranca; un 802.1X de alumno sigue entrando | restaurar el archivo anterior |
| 5 | **h1**: `110_superadmin_equipo.yml -e accion=preparar -e confirmar=true` y después `-e accion=arrancar -e confirmar=true` | el 802.1X termina (`EAP state=SUCCESS`), `/sessions` muestra `superadmin01@example.org` con VLAN 45, el flujo 40001 de `02:5a:d0:00:00:01` está `ADDED` con `goto_table:45`; la IP `10.0.1.220` se pone **después** del EAP | `-e accion=parar` (avisa al mapper, para el suplicante, borra el namespace) |
| 6 | **Panel**: instalarlo con `-e escuchar=10.0.0.10` y comprobar que solo el superadmin llega al 8480 | desde el namespace `nc -zv 10.0.0.10 8480` llega; desde h2 (alumno) y desde h1 no | `panel_instalar.yml -e accion=quitar` o volver a `escuchar=127.0.0.1` |
| 7 | **Diagnóstico y tormentas**: `95_diagnostico_lazos.yml`, salud del mapper, sesiones de h1 y h2 | sin tormenta; h1 y h2 con su sesión y VLAN de siempre | — |

## 5. Riesgos y cómo se evitan

- **ARP antes del 802.1X.** El puerto 1 manda el ARP de un equipo sin sesión al controlador (fallback MAB, prioridad 40004). Si el namespace hiciera ARP antes de autenticar, el mapper lo trataría como un dispositivo sin 802.1X y lo admitiría como hijo de h1. Por eso la interfaz sube **sin IP y sin IPv6**, y la IP se pone solo después de `EAP state=SUCCESS` (`110_superadmin_equipo.yml`).
- **Probar con otra identidad.** No se prueba el EAP del namespace con el certificado de otra identidad (por ejemplo `profesor01`): un segundo `profesor01` en otra MAC es una suplantación y el mapper pone el puerto en lista negra 600 s. Solo se prueba con `superadmin01`, y solo cuando LDAP, RADIUS y el mapper están listos.
- **Dos sesiones en el mismo puerto.** El índice por puerto del `SessionManager` (`get_by_port`) solo guarda la última sesión, pero **ningún código de producción lo usa** (comprobado el 24-nov); las sesiones se buscan por MAC y por usuario.
- **Parche EAPOL.** El filtro `tc` está en la salida de `ens4`; se espera que también alcance a las tramas de la macvlan (salen por `ens4`). **No verificado**: se comprueba en el paso 5 (si el EAP no termina, es lo primero que se mira).
- **Certificado de laboratorio.** El repositorio versiona claves privadas, incluida la de la CA (`ca.key`, contraseña de laboratorio): hallazgo para el equipo. El certificado de `superadmin01` **no** se versiona (`.gitignore`), lleva una clave aleatoria y vive en `/etc/r1-superadmin` (0700, solo root). `regenerar.sh` **no** se usa (rehace toda la CA).
- **Un solo equipo.** Si se pierde o se compromete hay acceso total al panel; root en h1 puede usar el certificado. Mitigación: certificado solo para root, confirmación explícita en cada cambio del panel, auditoría, y el procedimiento de emergencia (editar el archivo por el repositorio). En un campus real sería una máquina aparte.
- **`/home/ubuntu/supp`.** `hosts.yml` borra de ahí los certificados que no son de las identidades del host; por eso el del superadmin está en `/etc/r1-superadmin`.
- **Repositorio del borde.** El checkout del borde (`~/proyecto-sdn`) no coincide con el remoto tras la reescritura de historia del equipo; los scripts de este runbook se copian desde el repositorio local y no dependen de él.

## 6. Archivos nuevos

| Archivo | Para qué |
|---|---|
| `R1/radius/raddb/certs/emitir_cliente_nuevo.sh` | emite UN certificado de cliente con la CA existente, en una copia de trabajo (no toca la CA ni RADIUS); clave aleatoria; rechaza un usuario ya emitido |
| `R1/deploy/topologia14/110_superadmin_equipo.yml` | `estado` (solo lectura), `preparar`, `arrancar`, `parar`, `quitar`; las acciones que cambian algo exigen `-e confirmar=true` |
| `docs/PUESTA_EN_VIVO_SUPERADMIN.md` | este runbook y su bitácora |

## 7. Bitácora (hora UTC; todo lo ejecutado queda aquí con su evidencia)

| Fecha y hora | Qué se hizo | Quién | Resultado y evidencia |
|---|---|---|---|
| 24-nov | Dani aprueba la opción A (namespace) y pide documentar todo | Dani | — |
| 24-nov ~15:00 | Copia local alineada con el remoto reescrito (se verificó que los commits propios están en el remoto con el mismo contenido y se guardó la rama `respaldo-local-antes-de-reescritura-24nov`) | Claude | 0 diferencias de contenido |
| 24-nov 16:08 | Lectura en vivo: salud y sesiones del mapper, versión del webhook en las réplicas, capacidades de h1 | Claude (solo lectura) | `24_superadmin_estado_previo.txt`: réplicas sin `rol_de_802_1x`; h1 con macvlan, `tc`, `wpa_supplicant` |
| 24-nov | `emitir_cliente_nuevo.sh` probado en una carpeta temporal local: serie 09, verifica contra la CA, la CA del repositorio queda intacta, rechaza `profesor01`; el certificado de prueba se borró | Claude (local, sin tocar VNRT) | salida en el chat; no se emitió ningún certificado real |
| 24-nov 16:2x | `110_superadmin_equipo.yml` validado en el borde: `--syntax-check`, `accion=estado` (solo lectura: 0 namespaces, unidades inactivas, agente y filtro de h1 activos) y rechazo de `accion=arrancar` sin `confirmar=true` | Claude (solo lectura) | `25_playbook_superadmin_validacion.txt` |
| _pendiente_ | Paso 1: emitir el certificado real en el borde | | |
| _pendiente_ | Paso 2: desplegar el mapper con el webhook nuevo y `policy.local.yaml` con `admin_panel` | | requiere OK explícito (sección 3) |
| _pendiente_ | Pasos 3 a 7 | | cada uno con OK |
