# Puesta en vivo del equipo `superadmin` (opción A: namespace en h1) — runbook y bitácora

Requisito **P11** de `docs/DISENO_EXCEPCIONES_POR_SESION.md` (sección 4.7 y 4.7.1). **Decisión del equipo (Dani, 24-nov-2026): opción A**, un equipo dedicado `superadmin01` que corre como un *namespace de red con una macvlan* sobre la interfaz de datos de **h1**. Este documento dice qué se hace, en qué orden, cómo se comprueba cada paso y cómo se vuelve atrás, y lleva la bitácora de lo que se ejecutó de verdad. **Cada cambio en vivo se anota aquí con su hora y su evidencia.**

> **Cambio que afecta al 802.1X de siempre: el webhook del mapper.** El código del webhook (`/webhook` de `R1/shared/policy_mapper/policy_mapper.py`) **ya está cambiado en el repositorio** (commit «superadmin (P11)», `1c8d7bd` en el remoto) y **desplegado el 24-nov entre las 16:40 y las 16:41 UTC** en ctrl-3, ctrl-1 y ctrl-2, con el OK explícito de Gabriel (el 16:08, antes de eso, `rol_de_802_1x` aparecía 0 veces en las réplicas). Detalle en la sección 3. **Lo único que falta es verlo trabajar con un login real** (bitácora, paso 2b).

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

**Cómo se desplegó (24-nov, con el OK de Gabriel):** réplica por réplica (ctrl-3, ctrl-1, ctrl-2) con `R1/mapper-ha/deploy/actualizar_replica.py`, con respaldo y vuelta atrás, comprobando salud, sesiones y que h1 y h2 reautentiquen con su VLAN de siempre.

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
| 24-nov 16:2x | Antes de emitir: la CA es la misma en el repositorio, en el checkout del borde, en los tres contenedores RADIUS (`/etc/freeradius/certs/ca.pem`) y en los suplicantes de h1 y h2 (SHA-256 `9B:3B:FB:7E:CD:4E:D9:16…`, vence el 8-nov-2027) | Claude (solo lectura) | `26_superadmin_ca_coherencia.txt` |
| 24-nov 16:25 | **Paso 1 hecho (OK de Gabriel): certificado de `superadmin01` emitido en el borde**, en una carpeta de trabajo temporal (`/dev/shm`, ya borrada), **fuera del repositorio**: `/home/ubuntu/superadmin-certs/` (0700) con `superadmin01@example.org.{pem,p12,pass}` (0600). Serie **09**, CN `superadmin01@example.org`, válido del 24-nov-2026 al 24-nov-2027, uso `TLS Web Client Authentication`, SHA-256 `DA:9F:7E:47:6A:64:8B:D5…`, verifica contra la CA. La contraseña de la clave es aleatoria y no se mostró. El checkout del borde y la CA quedaron intactos (huellas iguales antes y después). No se tocó LDAP, RADIUS, el mapper ni h1 | Claude | `27_superadmin_certificado_emitido.txt` |
| 24-nov 16:3x | La parte **pública** de la base de la CA se trajo al repositorio (`index.txt` con la entrada nueva, `serial` en `0A`, `09.pem`), para que la próxima emisión no repita la serie 09. `09.pem` no contiene clave privada; la clave de `superadmin01` no está en el repositorio | Claude | commit de este cambio |
| 24-nov 16:3x | Dani aclara: el `admin_panel` vive en la VM de servidores (la VM `server`, 10.0.0.10, donde ya corre `panel-red` en 127.0.0.1:8480) y las pruebas en vivo de superadmin se omiten por ahora; después van las pruebas de R1 con el portal y los casos que se tengan | Dani (vía Gabriel) | — |
| 24-nov 16:3x | **Paso 2, preparación (sin cambiar nada en las réplicas):** lectura de la política real (`policy.yaml` `28100f61846f` y `policy.local.yaml` `cc67da72d757`, iguales en las tres réplicas, montada solo lectura en `/r2_policy`); paquete y `policy.yaml` nuevos construidos del commit; `R1/mapper-ha/deploy/actualizar_politica.py` (respaldo, validación previa y vuelta atrás); `validar` ejecutado en ctrl-1, ctrl-2 y ctrl-3 **en una copia temporal**: carga bien con el código nuevo, roles de siempre intactos (10, 20, 40, 50), `superadmin` en la 45, `admin_panel` en `10.0.0.10:8480`, 50 reglas, todos los servicios en la IP del server, sin excepciones activas. Se subieron a `/root` de los controladores `app-HEAD.tgz`, `policy_nuevo.yaml` y los dos scripts (sin claves) | Claude | `29_politica_replicas_previo.txt`, `30_politica_validacion_previa.txt` |
| 24-nov 16:3x | **Paso 2, primer intento de desplegar: bloqueado.** El control de permisos lo detuvo antes de empezar: cambia el 802.1X de siempre y faltaba el OK explícito de Gabriel (lo pegado de Dani no bastaba). Nada se aplicó | — | — |
| 24-nov 16:39 | **OK explícito de Gabriel** para desplegar el webhook, nodo por nodo, documentando cada decisión con sus alternativas descartadas (sección 8) | Gabriel | — |
| 24-nov 16:40 | **Paso 2 hecho: ctrl-3.** `actualizar_politica.py aplicar` (respaldo en `prod-r2-policy-backups/20261124c`; `policy.yaml` `28100f61846f`→`30b9c7e26865`, `policy.local.yaml` `cc67da72d757`→`44f1a50fb4cb`) y `actualizar_replica.py desplegar` (contenedor anterior guardado como `mapper-ha-prod-rollback-20261124c`; carpeta de código `.rollback-20261124c`). `ready` a los 2 s, token correcto, gateway 200, sin errores en los logs | Claude | `31_despliegue_webhook_ctrl-3.txt` |
| 24-nov 16:41 | **Paso 2 hecho: ctrl-1 y ctrl-2**, igual, uno tras otro (cada uno `ready` a los 2 s, sin errores en los logs) | Claude | `31_despliegue_webhook_ctrl-1.txt`, `..._ctrl-2.txt` |
| 24-nov 16:4x | Verificación final de solo lectura: las tres réplicas con la **misma** política (`30b9c7e26865`, local `44f1a50fb4cb`) y el mismo `policy_mapper.py` (`40728ad784bd`, `rol_de_802_1x` presente), contenedor `running` con 0 reinicios, token `9f0a63c24e8a`, `95` sin tormenta. El respaldo confirma el cambio exacto: `policy.local.yaml` +3 líneas (`admin_panel` en `10.0.0.10:8480`), `policy.yaml` +23 y 0 quitadas | Claude | `32_webhook_verificacion_final.txt` |
| 24-nov 16:25 | (Contexto, no lo hizo este trabajo) el agente avisó el cierre de `alumno02` (16:25:01) y `alumno01` (16:26:07): alguien desconectó h1 y h2 desde su interfaz. Por eso había **0 sesiones** antes y después del despliegue y no se interrumpió a nadie | Dani o Gabriel | `/events` del mapper |
| _pendiente_ | **Paso 2b: la prueba real del webhook nuevo.** Un login 802.1X de h2 (`alumno01`) o de h1 (`alumno02`) desde la interfaz del agente: debe entrar con VLAN 20 y tabla 20 como siempre, y aparecer el evento «admitido» y el flujo 40001 con `goto_table:20`. Hasta que ocurra, el webhook nuevo solo está probado con pruebas unitarias | Gabriel o Dani (login); Claude (verificación) | |
| _pendiente_ | Pasos 3 a 7 (LDAP, RADIUS, h1, panel): **las pruebas de superadmin en vivo se omiten por ahora** (Dani, 24-nov) | | cada uno con OK |

## 8. Decisiones tomadas y alternativas descartadas

**Regla de trabajo (pedida por Gabriel el 24-nov):** cada decisión de este trabajo se registra aquí con *por qué se eligió* y *por qué no se eligieron las otras*. Las de más abajo son las del puesto en vivo de `superadmin` y de su despliegue; las nuevas se añaden a esta lista.

### 8.1 Despliegue del webhook y de la política (24-nov)

| # | Decisión | Por qué | Alternativas descartadas y por qué |
|---|---|---|---|
| D1 | **Réplica por réplica** (ctrl-3 → ctrl-1 → ctrl-2) | El gateway reparte cada login entre las tres: con una sola actualizada, el mismo equipo recibiría un rol distinto según la réplica que lo atienda. De una en una siempre quedan dos sanas con quórum. ctrl-3 primero porque no manda en los switches de acceso (como en el procedimiento de Mariana) | **Las tres a la vez:** una ventana sin servicio de autenticación. **Solo una réplica (canario) y esperar:** dejaría el 802.1X inconsistente durante horas y rompería la regla de la misma política en las tres (P10) |
| D2 | **Validar en una copia temporal** antes de reemplazar (`actualizar_politica.py validar`) | La política real tiene IP y puertos propios que no están en el repositorio (`policy.local.yaml`): hay que probar el archivo combinado con el código nuevo, en el nodo, sin tocar lo que sirve | **Probar solo en mi equipo:** no ve la política local de producción. **Aplicar y mirar si arranca:** un archivo roto dejaría a la réplica sin política al reiniciar |
| D3 | **Respaldo triple**: política (`prod-r2-policy-backups/<stamp>`), carpeta de código (`.rollback-<stamp>`) y contenedor anterior (`mapper-ha-prod-rollback-<stamp>`) | Es el procedimiento ya usado el 22 y el 24-nov; cada una de las tres piezas se puede restaurar sola | **Solo el contenedor anterior:** el código vive en una carpeta montada, no dentro de la imagen, y la política en otra |
| D4 | **Añadir `admin_panel` a `policy.local.yaml` insertando 3 líneas** al final del bloque `services` | Es el cambio mínimo y verificable: el respaldo muestra exactamente +3 líneas y 0 quitadas. La política local se mezcla clave por clave con la base; sin esto `admin_panel` conservaría la IP de ejemplo 10.0.0.50 | **Regenerar con `generar_politica_local.py`:** pide enlaces y hosts reales y puede cambiar otras claves. **Cargar y volver a volcar con PyYAML:** reordena y reformatea todo el archivo, y el cambio ya no se puede revisar a ojo |
| D5 | **Política y código a la vez en cada nodo** (política primero, luego el contenedor) | La política se lee al arrancar; el contenedor en marcha no la ve hasta recrearse, así que no hay ventana con política nueva y código viejo ejecutándose | **Política en las tres y luego código:** daría una ventana más larga con archivos nuevos en disco y procesos viejos. **Código primero:** el código nuevo arrancaría con la política vieja, sin `superadmin` |
| D6 | **Desplegar con h1 y h2 desconectados** (a las 16:25 y 16:26 el agente avisó su cierre; no lo causó este trabajo) | Con 0 sesiones no se interrumpe a nadie y el despliegue no depende del estado de las sesiones | **Esperar a otra hora:** no aportaba nada y retrasaba la prueba real. **Desconectar yo los hosts:** habría molestado las pruebas de Dani, que usan el login manual |
| D7 | **No hacer yo el login de prueba** (lo hace Gabriel o Dani desde la interfaz del agente) | Es el único camino que ejercita de verdad `rol_de_802_1x`: la reautenticación de una sesión idéntica devuelve antes de asignar rol, y un webhook sintético falla porque ONOS AAA no conoce la sesión EAP. Una persona conectando desde la interfaz es la prueba real y no deja a h1 y h2 sin servicio por mi cuenta | **`wpa_cli reauthenticate`:** no pasa por la asignación de rol. **Llamar al webhook con un cuerpo inventado:** responde «Sesión EAP no encontrada en ONOS AAA». **Forzar yo logoff y logon con el agente:** interrumpe a h1 o h2 mientras Dani prueba |
| D8 | **Dejar LDAP, RADIUS, h1 y el panel sin tocar** | Dani omitió las pruebas de superadmin en vivo; el webhook nuevo es compatible con la política de siempre sin ellos, y un equipo con rol `superadmin` simplemente recibiría 403 hasta que existan | **Hacer todo el puesto en vivo de una vez:** mezcla un cambio que afecta a todos (el webhook) con otros de autenticación (RADIUS, LDAP) en una ventana difícil de diagnosticar |

### 8.2 Diseño del rol `superadmin` (24-nov)

| # | Decisión | Por qué | Alternativas descartadas y por qué |
|---|---|---|---|
| D9 | **El webhook saca la tabla y el rol de `policy.yaml`, con coincidencia exacta** (`rol_de_802_1x`) | Un rol nuevo se rechazaba aunque estuviera en LDAP, RADIUS y la política (cuarto sitio del diseño 4.7). Exacta porque `superadmin` no debe casar con un nombre que lo contenga. El nombre visible sale de la política o de un diccionario (`soporte` → «Administrador») para no cambiar lo que ya muestra `/sessions` | **Añadir un cuarto `if "superadmin" in rol`:** repite el problema para cada rol futuro y la subcadena casa con cualquier nombre que lo contenga. **Regla por MAC en R2 sin rol nuevo (ya evaluada en el diseño):** el panel y la bitácora no distinguirían al superadmin del resto de administradores |
| D10 | **Tabla 45** para el rol | Un `goto` solo va hacia delante: debe ser menor que la 60 (reenvío); distinta de la 30 (IoT/MAB) y de la 99 (cuarentena); entre la 40 (soporte) y la 50 (invitado) | **Una tabla mayor que 60:** el `goto` a la tabla 60 sería hacia atrás y OVS lo rechaza. **La 30:** reservada a IoT. **Compartir la 40:** los administradores heredarían el panel |
| D11 | **Matriz propia `superadmin`** con lo del administrador más `admin_panel` | Decisión 15 del diseño: no necesitar otro equipo; y solo el superadmin llega al panel, ni siquiera el administrador de soporte | **Compartir la matriz `administrator`:** el administrador heredaría el panel. **Solo el panel:** obligaría a mantener un segundo equipo de soporte aparte |
| D12 | **Panel en el puerto 8480** | Es donde ya escucha `panel-red` en la VM `server`; el diseño decía 8444 por un supuesto que no se cumplió | **8444:** habría que reinstalar el panel o abrir un puerto que nadie escucha |
| D13 | **Rama de RADIUS `superadmin` la última** | Un equipo que por error esté en dos grupos recibe el rol de menos privilegio (gana el otro) | **Primera:** un error de grupo daría el máximo privilegio. **Prohibir pertenecer a dos grupos:** RADIUS no lo hace cumplir, hay que aceptarlo y reducir el daño |
| D14 | **El grupo y el equipo en el LDIF solo como plantilla comentada** | El LDIF solo se carga con el directorio vacío, y un `groupOfNames` sin miembros es inválido: descomentarlo sin elegir el equipo rompería la carga | **Dejar el grupo vacío:** inválido. **Inventar un equipo de relleno:** una identidad falsa en el LDAP |

### 8.3 Equipo, certificado y namespace (24-nov)

| # | Decisión | Por qué | Alternativas descartadas y por qué |
|---|---|---|---|
| D15 | **Opción A: namespace con macvlan sobre h1** (aprobada por Dani) | h1 y h2 son las únicas VM con 802.1X y un puerto de acceso; la macvlan tiene su propia MAC y no toca la identidad de h1; las pruebas de DHCP ya usan macvlan sobre `ens4` | **B, lo mismo sobre h2:** choca más con las pruebas de alumno y de ascenso. **C, una VM nueva:** los recursos del slice están repartidos. **Usar h1 entero como superadmin:** rompería las identidades `alumno02`/`profesor01` que ya se usan |
| D16 | **Certificado emitido en el borde, fuera del repositorio**, con clave aleatoria y 0600 | El repositorio ya versiona claves privadas de laboratorio (incluida `ca.key`): no se suma una más; la identidad que administra la red no debe llevar la clave de laboratorio de los demás | **Emitirlo en mi equipo:** la clave quedaría en un PC personal. **Con la contraseña de laboratorio y dentro del repositorio:** igual que los demás, pero con el rol más sensible |
| D17 | **Script nuevo `emitir_cliente_nuevo.sh`** que trabaja en una copia de la CA | `regenerar.sh` borra y rehace la CA y todos los certificados: rompería a todos los equipos | **`regenerar.sh`:** destruye la CA. **`make client`:** emite solo el certificado de prueba y no deja elegir el CN. **Editar `index.txt` y `serial` a mano:** un error invalida la CA |
| D18 | **Traer al repositorio solo la parte pública de la base de la CA** (`index.txt`, `serial` 0A, `09.pem`) | Si no, la próxima emisión repetiría la serie 09 con otro certificado | **No tocar el repositorio:** serie duplicada. **Subir también la clave:** nunca |
| D19 | **La IP del namespace se pone después del EAP** y estática (`10.0.1.220`) | El ARP de un equipo sin sesión se copia al controlador (fallback MAB); antes del EAP lo admitiría como hijo de h1. Estática porque cada puerto admite solo 3 concesiones DHCP y h1 ya usa una | **IP primero:** dispara el MAB. **DHCP:** consume una de las 3 concesiones del puerto y obliga a manejar la renovación |
| D20 | **Certificado en `/etc/r1-superadmin`**, no en `/home/ubuntu/supp` | `hosts.yml` borra de `supp` los certificados que no son de las identidades del host | **`supp`:** el siguiente `72_suplicante.yml` lo borraría |
| D21 | **Pedir el OK de Gabriel antes de cada cambio en vivo** que toque autenticación, y registrar cada paso aquí | Lo pidió Gabriel; además, el control de permisos bloqueó el primer intento de desplegar el webhook por no tener ese OK explícito | **Avanzar con lo que dijo Dani por mensaje:** es una opinión del equipo, la decisión de ejecutar es de Gabriel |
