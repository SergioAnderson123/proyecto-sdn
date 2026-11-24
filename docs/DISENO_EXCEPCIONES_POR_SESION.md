# Diseño: excepciones por sesión (permiso a una cuenta concreta) — borrador para revisar, 23-nov-2026

**Estado: solo diseño. Nada de esto está implementado.** Toca R1 (Policy Mapper, estado HA) y R2 (`policy_tables`, generación de flujos), por eso conviene que lo revisen las dos partes antes de escribir código. Nace de una pregunta del equipo («¿se puede dar un permiso a un usuario concreto o hay que crear un grupo/VLAN?») y de lo que se vio operando la slice el 23-nov. Relacionado: `docs/DISENO_PANEL_ADMIN.md` (sección 13: el panel sería el sitio donde se dan de alta y de baja).

## 0. Antes de empezar: requisitos previos

**Nada de las fases 2 a 5 debe empezar hasta que estos requisitos estén cumplidos o decididos.** La fase 1 (funciones puras de R2, sin red) **sí puede empezar ya**: no depende de nada en vivo.

| # | Requisito | Por qué | Estado (23-nov) | Responsable | Cómo se comprueba |
|---|---|---|---|---|---|
| **P1** | **Parche del mapper del portal** aplicado y desplegado (`docs/NOTA_PORTAL_MAC_POR_IP.md`, `docs/parche_mapper_portal_ip.diff`) | Sin él el portal no identifica a los clientes de capa 3: no hay login ni **ascenso** por el portal, y las excepciones nacen en el ascenso | **Pendiente.** Marianajo3 ya está avisada (23-nov); la nota y el parche están en el repo | R1 | Guion de la nota (§7): h1 ve su sesión en el portal y el `POST /login` de h2 deja de decir «No se pudo identificar su equipo» |
| **P2** | **Portal nuevo** en el `server` (manda la IP de origen) | Es la mitad del arreglo de P1 | **Hecho** (23-nov 21:47 UTC, `portal_actualizar.yml`) | R2 | Evento `rechazado` de `x` con la MAC vacía, ya visto |
| **P3** | **`PORTAL_TOKEN` definido** (el mismo) en el mapper HA y en el portal | Hoy está **vacío** en los dos: `_verificar_portal` está desactivada y `/portal/estado?ip=` quedaría abierto | **Pendiente**; conviene hacerlo en el mismo despliegue que P1 | R1 + R2 | La misma huella en el `server` y en `mapper-ha-prod`, distinta de `e3b0c44298fc` (comando en la nota, §6) |
| **P4** | **Ascenso probado en vivo** en la slice | Nunca se ha ejecutado aquí, y es el punto exacto donde se instalarían las excepciones | **Pendiente**, tras P1 | R2 + R1 | h2 con sesión 802.1X de alumno → `profesor02` en el portal → rol docente (tabla 10) → cerrar sesión → vuelve a alumno. Además: ascenso rechazado desde otro puerto, y que `ONOS AAA` tenga a h2 autorizado (`ascenso_exigir_ubicacion: true`) |
| **P5** | **Límite de intentos fallidos** de contraseña en el portal | Con esta función la cuenta de un profesor abre más; hoy `server.py` no limita | **Pendiente** (decisión 8) | R2 | Tras N fallos seguidos desde una IP o contra una cuenta: bloqueo temporal, mensaje neutro y un evento en la bitácora |
| **P6** | **Refactorización previa:** una sola función `_borrar_flujos_de_sesion` para los siete caminos de cierre (4.3), en su propio cambio | Evita que un camino olvidado deje una regla abierta | **Pendiente** | R1 | Una prueba por camino; la suite del mapper en verde |
| **P7** | **Servicio de prueba en el catálogo de la slice** (por ejemplo `student_records`, puerto 8102) y denegado hoy al docente | Es lo que se abrirá en la validación (fase 5) | **Por comprobar** | R2 | `nc -zv -w 2 -s <IP de h2> 10.0.0.10 8102` da `timed out` antes de la excepción; el servicio figura en `services` del `policy.yaml`/`policy.local.yaml` |
| **P8** | **Mapper HA estable y vistas de ONOS iguales** | Las excepciones se instalan por ONOS y se reconcilian con él | **Hecho** (el mapper con la reposición del flujo 40001, `6726bc4`, está desplegado según la nota de R1; las tres vistas de ONOS en 5 de 5 el 23-nov) | R1 + R2 | El registrador sin `VISTA_DESIGUAL`; `/health/ha` en `ready` |
| **P9** | **Equipos y cuentas de la prueba** | El ascenso necesita un equipo con sesión 802.1X de alumno y una cuenta de profesor en LDAP | **Por confirmar** | R1 | h2 con `72 -e arrancar=true -e objetivo_host=h2-alumno` y sesión Alumno en `/sessions`; `profesor02` (cuenta de persona, sin MAC) en el grupo `profesor` |
| **P10** | **La misma política en las tres réplicas** | Cada réplica del mapper lee su propio `policy.yaml`; con una lista de excepciones, una réplica distinta daría resultados distintos según quién atienda | **Por comprobar** | R1 | `sha256sum` del `policy.yaml` (y del archivo de excepciones) idéntico en las tres réplicas |
| **P11** | **El rol `superadmin` y su equipo dedicado** (sección 4.7): grupo y equipo en LDAP, rama en el `post-auth` de RADIUS, rol y servicio `admin_panel` en `policy.yaml` y, además, **el webhook del mapper**, donde el mapeo rol → VLAN está escrito en el código y un rol nuevo se rechaza | Es quien entra al panel. **No hace falta antes de la fase 6**: hasta que exista el panel, las excepciones se dan de alta editando el archivo por el repositorio | **Por hacer** | R1 (LDAP, RADIUS, mapper) + R2 (`policy.yaml`) | El equipo dedicado autentica y `/sessions` lo muestra con el rol `superadmin` en la tabla elegida; un equipo de otro rol no alcanza el puerto del panel |

**Cuándo se puede pasar a cada fase:**
- **Fase 1:** ya. **Fase 2:** P6 hecho y decisiones 1 a 3 tomadas. **Fase 3:** fase 2 terminada. **Fase 4 (despliegue):** P3, P8 y P10, y la fase 3.
- **Fase 5 (validación en vivo):** P1, P2, P3, P4, P5, P7, P9 y P10.
- **Fase 6 (panel):** P11 (las decisiones 14 a 18 están tomadas).

## 1. Decisiones de alcance (acordadas el 23-nov)

| Quién | Qué pasa | Excepciones |
|---|---|---|
| **Equipos** (alumno y demás, 802.1X con certificado) | Son computadoras, no personas: en LDAP el «alumno» es un equipo. Todos entran con el rol por defecto (alumno) | **No** en la v1 |
| **Profesores** (cuenta del portal, por **ascenso** de un equipo ya autenticado) | Única identidad de persona que se usa para decidir permisos | **Sí: es el alcance de este diseño** |
| **Invitados** (cuenta del portal, login directo) | Acceso mínimo, 30 min | No |
| **Soporte TI** (administrador) | **Se le da un equipo y se autentica por 802.1X**, como hoy (rol `soporte`, tabla 40). No entra por el portal | No. (Que soporte use el portal por ascenso queda **aparcado**: es una opción válida, pero no se necesita.) |
| **Superadmin** (rol nuevo) | **Un equipo dedicado** que se autentica por 802.1X; **no entra por el portal**. Es el único que entra al panel y da de alta y de baja las excepciones (decisión 6, sección 4.7) | No: administra las excepciones, no las recibe |

### 1.1 Persona y equipo se separan (decidido el 24-nov)

**Una excepción se da a una cuenta de persona, nunca a la identidad de un equipo.** En el LDAP en vivo (leído el 24-nov, sin modificarlo) casi todas las cuentas son de equipos: `alumno01` es la MAC, el switch y el puerto de h2; `profesor01` es los de h1 (`fa:16:3e:b6:96:08`, sw-acc-1 puerto 1) y es la identidad 802.1X de ese equipo; `alumno02`, `alumno03` y `support01` llevan MAC de Mininet. Solo `profesor02` y `guest01` no llevan MAC ni puerto. Por eso:

- Las excepciones (ejemplo, pruebas y validación) usan **`profesor02`**, que es una cuenta de persona (grupo `profesor`, sin MAC). `profesor01` no se usa: sería dar un permiso a la identidad de h1.
- La clave `usuario` de `excepciones.yaml` es el uid que se escribe en el portal (`profesor02`), no el CN de una sesión 802.1X (`profesor01@example.org`); no coinciden aunque la persona y el equipo se llamen parecido, así que una excepción nunca se aplica a una sesión 802.1X.
- Las excepciones no se guardan en LDAP y no crean usuarios: viven en `R2/policy/excepciones.yaml`. Crear cuentas de persona nuevas es un cambio de R1 al LDAP; si la validación necesita otro usuario, se crea **`guest02`** (acordado con Dani el 24-nov). Un invitado no puede tener excepciones (`roles: [docente]`) ni ascender: `guest02` sirve para las pruebas negativas, no para recibir permisos.
- La contraseña de `profesor02` no está en el repositorio; para la fase 5 hay que fijarla o dársela a quien valide (no se prueba a adivinarla).

## 2. El problema

La política se decide por **rol**. El grupo del usuario llega por RADIUS como una VLAN; el flujo de sesión (tabla 0, prioridad 40001, `in_port` + `eth_src`) hace `push_vlan` con ese id y salta a la **tabla de su rol** (docente 10, alumno 20, soporte 40, invitado 50). Esa tabla solo compara **VLAN, IP y puerto TCP del destino** (`flujos_tabla_rol`): no mira quién es la persona, y `policy.yaml` no tiene ninguna sección de excepciones. Un permiso individual obliga hoy a crear un rol nuevo (grupo LDAP + RADIUS + VLAN/tabla + todas las filas de `rules` **+ una rama nueva en el webhook del mapper**, donde el mapeo rol → VLAN está escrito en el código), que no escala: un rol por excepción, y cada rol es una tabla en cada switch de acceso.

## 3. Qué se quiere

- Dar a una **cuenta de profesor** permiso a uno o varios servicios que su rol no tiene (por ejemplo `student_records` a `profesor02`), **sin** crear rol, VLAN ni tabla.
- Que valga **mientras dure su sesión de portal** y, opcionalmente, hasta una fecha.
- Que se retire sola en **todos** los casos en que acaba la sesión.
- Que quede **auditada** y que se pueda dar de alta y de baja desde el panel.
- No relajar ninguna de las protecciones actuales (misma MAC y ubicación para el ascenso, lista negra de R5, etc.).

## 4. Diseño

### 4.1 Cómo se define

Una sección nueva de `policy.yaml` (o su propio archivo, a decidir; el panel la mostraría y, con el flujo del repositorio, la editaría). Se define **por cuenta del portal** (el `usuario` tal como lo escribe la persona, el mismo que registra el mapper), nunca por MAC:

```yaml
excepciones:
  roles: [docente]                       # solo las cuentas que entran con este rol pueden tener excepciones
  no_excepcionables: [network_management]  # servicios que nunca se abren por excepción
  lista:
    - {usuario: profesor02, servicios: [student_records], hasta: "2026-12-31", motivo: "Coordinación de matrícula"}
```

`cargar_politica` debe **rechazar** una sección inválida con un mensaje claro: usuario vacío, servicio que no está en `services` o en `no_excepcionables`, `hasta` pasado o mal escrito, rol no permitido, o servicio que el rol ya tiene (redundante). Un archivo mal escrito no puede dejar la política a medias.

### 4.2 Qué reglas se instalan

En la **tabla del rol que tiene la sesión en ese momento** (la 10 mientras dure el ascenso a docente), por cada servicio de la excepción y por cada switch de la sesión, una regla igual a la del rol **más** `ETH_SRC = <MAC del equipo>`:

- **Selector:** `VLAN_VID = tabla`, `ETH_TYPE 0x0800`, `IP_PROTO 6`, `IPV4_DST = <ip del servicio>/32`, `TCP_DST = <puerto>`, `ETH_SRC = <mac>`.
- **Tratamiento:** el mismo `_salida(politica)` que las reglas del rol (`NORMAL`, o `goto` a la tabla 60 con el reenvío proactivo y la capa 3). La excepción solo abre el **permiso**; el reenvío ya existe.
- **Prioridad nueva `PRIO_EXCEPCION = 30500`:** sobre las del rol (30000, `PRIO_ROL`) y sin interferir con la 40001 (que está en la tabla 0). Se registra junto a las demás en `R5/deploy/prioridades.yaml`.
- **appId propio** (por ejemplo `org.campus.excepciones`), **no** el de R2: `dispositivos_incompletos` decide si faltan reglas de R2 **contando** los flujos `ADDED` del appId de R2; si las excepciones usaran el mismo appId, ese contador subiría y un switch al que le faltan reglas del rol podría parecer completo. (Con un appId aparte, además, `/flows/application/<appId>` da de una vez todas las excepciones activas.)

### 4.3 Ciclo de vida: cuándo se instalan y cuándo se retiran

**Instalar:** al terminar con éxito `POST /portal/login` con ascenso a docente (después de `inyectar_flujo_tabla_0`), si la cuenta está en `excepciones.lista` y no ha vencido su `hasta`. No se instalan antes de que el ascenso haya pasado sus comprobaciones (misma MAC que la sesión 802.1X, misma ubicación según ONOS AAA): un tercero no puede reclamarlas con otra MAC.

**Retirar:** hay **siete** caminos que hoy cierran una sesión y todos llaman por su cuenta a `eliminar_flujo_onos(sesion.dpid, sesion.flow_id)`. Cada uno debe borrar también las excepciones:

| # | Camino | Dónde está hoy |
|---|---|---|
| 1 | Cierre de sesión por el portal | `portal_logout` (y `_restaurar_base` si es un ascenso) |
| 2 | Fin del ascenso (vuelve al rol base) | `_restaurar_base` |
| 3 | Sesión vencida por tiempo | `_expirar_sesiones_portal` **y** su copia en `ha_runtime.expire` (dos sitios) |
| 4 | Equipo ausente / puerto sin enlace | `_cerrar_por_ausencia`, desde `/sessions/ausente` y desde `ha_runtime.ports` |
| 5 | Revocación de R5 | `revocar_sesion` (`/sessions/revoke`) |
| 6 | Logout avisado por el agente 802.1X | `/sessions/logout` |
| 7 | El propio equipo reautentica por 802.1X y se anula el ascenso | `webhook` |

**Recomendación:** antes de la función nueva, hacer una refactorización pequeña y con pruebas: **una sola función** `_borrar_flujos_de_sesion(sesion)` que borre el flujo 40001 y las excepciones, y que los siete caminos la llamen. Así el camino que se olvide no deja una regla abierta.

Además, la excepción se retira **cuando llega su `hasta`**, aunque la sesión siga viva (el lazo que ya revisa el plazo cada 10 s lo comprobaría), y se vuelve a comprobar al admitir.

### 4.4 Dónde se guardan los ids y qué pasa con el HA

Hay dos formas de saber qué borrar:

| | Cómo | Pros | Contras |
|---|---|---|---|
| **A. Guardar los ids en la sesión** | Campo `excepciones` (lista de `{dpid, flow_id}`) en `ActiveSession`; para el HA, entra en el estado de etcd y en el **outbox** de borrados (hoy un registro por sesión; habría uno por flujo) | Borrado directo, reintentable | Ver «versiones mezcladas» abajo |
| **B. No guardar ids; consultar** | Al borrar, listar los flujos del appId de excepciones del switch y borrar los de `ETH_SRC = mac` y `PRIO_EXCEPCION` | Nada que migrar; sirve también para purgar huérfanos | Una lectura de flujos de ONOS por cierre (leerla del maestro del switch: las lecturas remotas fueron las que se atascaron el 23-nov) |

**Versiones mezcladas (importante para A).** El estado de las sesiones en etcd es `asdict(sesión)` y se lee con `cls(**d)`; un campo nuevo que una réplica **antigua** no conoce hace fallar la lectura (`TypeError`), y el despliegue es réplica por réplica. Para evitarlo: (1) **omitir** `excepciones` del snapshot cuando está vacía, de modo que mientras no haya ninguna excepción el estado sea idéntico al de antes; (2) desplegar **primero** el código nuevo en las tres réplicas **sin** ninguna excepción configurada, y solo después dar de alta la primera; (3) tener claro el camino de vuelta atrás (un snapshot con una excepción activa no lo leería una réplica antigua).

### 4.5 Reconciliación y huérfanos

El mapper ya comprueba y repone el flujo 40001 **al confirmar un login existente** (`6726bc4`); no hay un lazo periódico que lo haga. Las excepciones necesitan, además, una comprobación en las dos direcciones:
- **Falta una:** sesión de portal viva con excepciones configuradas y sin sus reglas en ONOS → reinstalarlas.
- **Sobra una:** regla `PRIO_EXCEPCION` en ONOS cuya MAC no tiene sesión viva, o cuyo usuario ya no tiene la excepción → borrarla. Es la red de seguridad si un camino de cierre falla o una réplica cae a mitad de un borrado.

La purga de huérfanos existente (`purgar_sesiones_huerfanas`) solo conoce los flujos 40001: hay que extenderla (o hacer una aparte) para el appId de excepciones.

### 4.6 Qué ve el administrador

- **Eventos nuevos** en la bitácora del mapper (`eventos.TIPOS`): `excepcion_aplicada` y `excepcion_retirada`, con usuario, MAC, servicio y motivo del retiro.
- **Panel:** lista de excepciones definidas y de las **activas** (quién, qué servicio, desde cuándo, sesión y switch), alta con motivo y caducidad, baja manual, y el efecto por switch antes de confirmar.

### 4.7 El rol `superadmin` y quién puede usar el panel (decisión del 23-nov)

**Decisión:** solo el rol **superadmin** (rol nuevo) entra al panel y da de alta y de baja las excepciones. Ese rol lo tiene **un equipo dedicado** que se autentica por 802.1X y **no entra por el portal**. En la v1 no hay un rol de solo lectura.

**La identidad es el equipo, no una cuenta.** En LDAP el equipo dedicado es una entrada como las demás (con MAC, switch y puerto fijos, que RADIUS compara) y pertenece al grupo `superadmin`; la persona queda identificada por la asignación de ese equipo. Por eso la auditoría registrará la identidad del equipo, su IP y su MAC.

**Crear el rol toca cuatro sitios, no tres:**

| # | Dónde | Qué |
|---|---|---|
| 1 | LDAP (`R1/shared/ldap/ldif/custom/estructura.ldif`) | Grupo `superadmin` y la entrada del equipo dedicado. El LDIF solo se carga con la carpeta vacía: en vivo, `ldapadd` y la replicación ctrl-1/ctrl-2 |
| 2 | RADIUS (`R1/radius/raddb/sites-available/default`, `post-auth`) | Una rama `elsif (LDAP-Group == "superadmin")` con `Tunnel-Private-Group-Id = "superadmin"` |
| 3 | `R2/policy/policy.yaml` (y `policy.local.yaml` en la slice) | `roles: superadmin: {matriz: superadmin, tabla: 45}`, el servicio `admin_panel` (IP y puerto) y las filas de `rules` del rol nuevo |
| 4 | **El webhook del mapper** (`policy_mapper.py`, asignación de política y rol) | Hoy es `if "docente" in rol: 10 / "alumno": 20 / "soporte": 40 / else: rechazo`. Un rol que no esté ahí se rechaza con «Rol sin acceso autorizado», aunque esté en los otros tres sitios. **Recomendación:** que la tabla salga de `POLITICA["roles"][rol]["tabla"]` con coincidencia **exacta** del nombre (no por subcadena): es un cambio de R1 con pruebas que sirve a cualquier rol futuro |

**La tabla del rol** debe ser **menor que 60** (las tablas de rol terminan en `goto` a la tabla 60 con el reenvío proactivo, y un `goto` solo puede ir hacia una tabla mayor), distinta de la 30 (IoT/MAB) y de la 99. Propongo la **45**.

**Los permisos**: una fila `matriz: superadmin` propia, **no** compartir la de `administrator` (los administradores heredarían el acceso al panel). Qué más puede alcanzar ese equipo es la decisión 15.

**Cómo entra al panel, sin contraseña.** El panel pregunta al mapper por la **IP de origen** de la conexión (el mismo `GET /portal/estado?ip=` con token que habilita el parche del portal) y deja pasar solo si la sesión 802.1X de ese equipo tiene el rol `superadmin`. Segunda capa independiente: R2 abre el puerto del panel únicamente al rol `superadmin` (tabla 45); cualquier otro rol lo descarta en el switch.

**Consecuencia para el diseño del panel.** El equipo dedicado está en la **red de datos**, así que el panel **no puede escuchar solo en la interfaz de gestión** como se había escrito: escucha en la IP de servicios del `server` (`10.0.0.10`, con un puerto propio, por ejemplo 8444) y la restricción por rol la hace R2. El panel sigue hablando con ONOS y el mapper por la red de gestión (`ens3`): **hace de puente entre las dos redes**, así que comprometer el panel daría acceso a la gestión. Mitigaciones: el panel solo lee y ejecuta las acciones acotadas del diseño, cada cambio pide confirmación explícita, y todo queda auditado.

**¿Hace falta un rol, o basta con la MAC del equipo?** (pregunta del 23-nov). Con el rol, la MAC **ya queda atada**: la entrada del equipo en LDAP fija su MAC, switch y puerto y el mapper rechaza cualquier otra ubicación («Puerto físico no asignado»), y el flujo de sesión (`in_port` + `eth_src`) ancla todo lo que viene después a esa MAC y puerto. Lo que cambia es **dónde vive la autorización**:

| | Rol `superadmin` (elegido) | Solo una regla por MAC |
|---|---|---|
| Cómo | El equipo está en el grupo LDAP `superadmin` → rol → tabla 45; solo ese rol alcanza el panel | El equipo entra como `soporte` y una regla estática de R2 con `eth_src` = su MAC abre el panel |
| Coste | Cuatro sitios, incluido el webhook de R1 | Una función nueva en R2 (regla de rol restringida a MAC) y la MAC escrita en la política |
| Revocar o cambiar de equipo | Quitar del grupo LDAP o revocar el certificado | Editar la política y desplegarla en las tres réplicas (P10) |
| Segundo equipo (suplente) | Añadirlo al grupo | Otra MAC en la política |
| Qué ve el panel y la bitácora | El rol `superadmin` | `soporte` y una lista de MAC |
| Separación de funciones | Un administrador (`soporte`) no hereda el panel | Comparte rol con los demás administradores; solo la lista de MAC los distingue |

A nivel de red las dos son igual de seguras: un equipo sin sesión 802.1X solo llega a la cuarentena (ARP y portal), y la regla por MAC solo se alcanza a través del flujo de sesión. El rol gana en operación, revocación y claridad, y su coste extra (hacer el webhook dirigido por política) es un arreglo que hace falta de todos modos para cualquier rol futuro. **La regla por MAC es válida como solución provisional** si el rol se retrasa, y se migraría después.

**Riesgos de esta decisión:**
- **Un solo equipo.** Si se pierde o se compromete, hay acceso total al panel (certificado revocable, ubicación fija verificada en LDAP, y una sesión 802.1X que no vence: el panel debe tener su propio tiempo de inactividad). Si falla, nadie edita por el panel: queda el procedimiento de emergencia de editar el archivo por el repositorio.
- **Sin rol de solo lectura**, quien solo necesite mirar no puede; se puede añadir más adelante como otro rol.
- **El panel confía en lo que le dice el mapper**: depende de P1 y P3 (parche del portal y token).

## 5. Seguridad

- La cuenta es la del portal: usuario y contraseña de LDAP por HTTPS con el certificado fijado. **Comprobar o añadir un límite de intentos fallidos**: en `R2/portal_cautivo/server.py` no se ve ninguno, y esta función hace más valiosa una cuenta de profesor.
- Requiere **ascenso**: misma MAC, misma ubicación y una sesión 802.1X previa del equipo. No se puede obtener desde un equipo sin sesión (`login_directo` sigue siendo solo `invitado`).
- **Caduca** con la sesión (8 h por defecto para el rol docente) y, si se define, con `hasta`.
- `no_excepcionables` impide abrir servicios críticos aunque alguien lo escriba en la lista.
- Los cambios de la lista son cambios de política: pasan por validación, simulación y auditoría (ver el panel).
- El `PORTAL_TOKEN` está **vacío** hoy en el portal y en `mapper-ha-prod` (medido el 23-nov): definirlo en el mismo despliegue.

## 6. Proceso para construirla

**Fase 0 — requisitos previos.** Ver la sección 0: P1 a P10. La fase 1 no depende de ellos; el resto, sí.

**Fase 1 — R2, funciones puras y pruebas (sin red).** En `policy_tables`: leer y validar `excepciones`, `excepciones_de(politica, usuario, ahora)` y `flujos_excepcion(politica, dpid, tabla, mac, servicios)`. Pruebas con el simulador de la tubería completa (tabla 0 → tabla de rol → tabla 60): la excepción abre **solo** ese servicio, **solo** para esa MAC; otro host del mismo rol no pasa; el alumno no; el servicio no excepcionable no se abre; `hasta` vencido no instala; con capa 3 y reenvío proactivo el tratamiento es el correcto. Entregable: los flujos que se instalarían por switch, visibles en la vista previa.

**Fase 2 — R1, el mapper.**
1. Refactorización: `_borrar_flujos_de_sesion` y los siete caminos de 4.3, con una prueba por camino (que el camino borre también las excepciones).
2. Instalar al admitir el ascenso; guardar lo que se necesite (4.4); eventos nuevos.
3. HA: `ha_runtime.expire`, el outbox y el estado de etcd, con la compatibilidad de versiones de 4.4.
4. Pruebas de integración del ascenso con excepciones, incluida la restauración del rol base.

**Fase 3 — reconciliación y huérfanos** (4.5), con pruebas de las dos direcciones (falta una / sobra una).

**Fase 4 — despliegue.** Réplica a réplica, con el procedimiento de R1 (paquete, comprobación de sesiones y acceso al gateway, copia de seguridad): **primero el código sin ninguna excepción configurada en las tres**, después la política (cada réplica lee su `policy.yaml`: que las tres tengan el mismo) y por último la primera excepción de prueba.

**Fase 5 — validación en vivo** (cuando 0 a 4 estén hechos). Con h2 como equipo de alumno y `profesor02` por el portal. Por defecto `student_records` (puerto 8102 del server) está denegado al docente: `nc -zv -w 2 -s <IP de h2> 10.0.0.10 8102` da **`timed out`**.
1. Con la excepción definida y el ascenso hecho: la misma prueba da **`Connection refused`** (hay camino; el servidor no escucha). Otro puerto no autorizado (8105) sigue en `timed out`.
2. En el switch de h2, `ovs-ofctl -O OpenFlow13 dump-flows br0 table=10 | grep eth_src` muestra la regla; ONOS la lista bajo su appId.
3. Cerrar sesión en el portal: vuelve a `timed out` y la regla desaparece.
4. Repetir con: bajar la NIC del equipo (ausencia), vencimiento de `hasta`, revocación de R5 y reinicio de una réplica del mapper a mitad de la sesión.
5. Comprobar con `95` que no hay reglas huérfanas ni tormentas, y que el panel muestra la excepción activa.

**Fase 6 — el panel.** Alta y baja con motivo y caducidad, lista de activas, simulación del efecto por switch (ver `DISENO_PANEL_ADMIN.md`).

**Orden y reparto.** 0 → 1 (R2) → 2 (R1) → 3 → 4 → 5 → 6. La 1 y la preparación de pruebas de la 2 pueden avanzar en paralelo. Esfuerzo relativo: la fase 2 es la mayor y la más delicada (siete caminos y el HA); la 1 es pequeña y de bajo riesgo; la 4 y la 5 son cuidadosas más que largas.

## 7. Pruebas que debe tener

- Validación de la sección `excepciones` (cada tipo de error) y que un archivo inválido no cambia la política activa.
- Los flujos generados: selector, prioridad, `eth_src`, tratamiento, un flujo por servicio y switch.
- Cada uno de los siete caminos de cierre retira las excepciones, también con el HA y con un borrado fallido que se reintenta por el outbox.
- Ascenso con y sin excepción; ascenso de un usuario sin excepción no instala nada; el logout del portal; la reautenticación 802.1X del equipo.
- Reconciliación: falta una (se reinstala) y sobra una (se borra).
- Compatibilidad: un estado de etcd sin excepciones es idéntico al anterior (`schema` 1 y sin el campo).
- Simulador de la tubería: solo esa MAC, solo ese servicio.

## 8. Riesgos

- **Una regla que no se retira** (un camino de cierre sin cubrir) deja a un equipo con un permiso de más. Mitigación: la función única de borrado y la reconciliación de «sobra una».
- **Versiones mezcladas en el HA** (4.4).
- **Carga en ONOS:** reglas extra por switch y lecturas de flujos si se elige la opción B; leerlas del nodo maestro.
- **Atar el permiso a la MAC del equipo**: si la persona entra desde otro equipo, la excepción se aplica en el nuevo al ascender, no en el anterior; es el comportamiento deseado, pero hay que decirlo al administrador.
- **Una política mal escrita**: por eso la validación estricta y el flujo de simulación antes de aplicar.

## 9. Decisiones abiertas

Cada una con la opción que recomiendo, quién decide y qué bloquea. **Decididas el 23-nov: todas, como se recomienda, salvo la 6, que se cambió (solo el rol `superadmin`); la 14 a 18, que nacen de esa decisión, se aceptan como se recomiendan.** No queda ninguna decisión abierta: lo pendiente son los requisitos previos de la sección 0.

| # | Decisión | Opciones | Recomendación | Decide | Bloquea |
|---|---|---|---|---|---|
| **1** | **Dónde vive la lista de excepciones** | `policy.yaml` · archivo propio | **Archivo propio** (`R2/policy/excepciones.yaml`), cargado y validado por `cargar_politica` como la fusión que ya hace con `policy.local.yaml`: el panel lo edita sin tocar la matriz de roles y se despliega y revisa aparte | R2 | Fase 1 (formato) |
| **2** | **Cómo saber qué borrar** | ids guardados en la sesión (A) · consulta a ONOS (B) | **A**, con el campo **omitido cuando está vacío** y el despliegue en dos tiempos de 4.4; y **B solo en la reconciliación** (leída del maestro) como red de seguridad. Borrar por id es directo y reintentable por el outbox; depender de una lectura de ONOS para cerrar una sesión es lo que falló el 23-nov | R1 | Fase 2 |
| **3** | **appId de las excepciones** | el de R2 · uno propio | **Propio** (`org.campus.excepciones`), por el contador de `dispositivos_incompletos` (4.2) | R2 | Fase 1 |
| **4** | **Caducidad `hasta`** además de la duración de la sesión | no · opcional · obligatoria | **Opcional con tope máximo** (por ejemplo 90 días), para que una excepción olvidada no sea permanente | R2 | Fase 1 |
| **5** | **Límites** | sin límite · por usuario y total | **3 servicios por usuario y 10 usuarios con excepción** en la v1; subirlos si hace falta | R2 | Fase 1 |
| **6** | **Quién entra al panel y da de alta y de baja** | cualquier administrador · rol propio de edición · **rol `superadmin`** | **Decidida: solo el rol `superadmin` (rol nuevo), con un equipo dedicado por 802.1X que no entra por el portal.** Sin rol de solo lectura en la v1 (sección 4.7) | equipo (decidida) | Fase 6 |
| **7** | **Orden de la refactorización** (P6) | antes · junto con la función | **Antes y en su propio cambio**: se puede revisar y desplegar sin riesgo de la función nueva | R1 | Fase 2 |
| **8** | **Límite de intentos fallidos** del portal (P5) | portal · mapper · ambos | **Portal** (por IP y por cuenta, por ejemplo 5 fallos en 10 min y espera creciente) **y** un evento `rechazado` en la bitácora, que el mapper ya tiene | R2 | P5, fase 5 |
| **9** | **Qué pasa al cambiar una excepción con sesión abierta** | al siguiente login · inmediato | **Bajas inmediatas** (la reconciliación de «sobra una», unos 15 s) y **altas al siguiente ascenso**; que la excepción no aparezca a mitad de una sesión sin que la persona haya vuelto a autenticar | R1 + R2 | Fase 3 |
| **10** | **Servicios no excepcionables** (lista inicial) | ninguno · una lista | `[network_management]`, y revisar `student_records` y `research_database` con quien administre los datos | R2 + quien decida la política | Fase 1 |
| **11** | **Dónde queda el registro permanente** | solo la bitácora en memoria del mapper · un registro duradero | **Duradero** (etcd o un registro estructurado): la bitácora de `/events` está en memoria y por réplica y se pierde; ligado al pendiente de R1 del historial compartido | R1 | Fase 6 |
| **12** | **¿Mostrar al profesor sus permisos adicionales** en la página del portal? | no · sí | **Sí, en una versión posterior**: `/portal/estado` devolvería los servicios y la fecha; da transparencia y facilita detectar errores | R2 | no bloquea |
| **13** | **Fuente única de la política entre réplicas** (P10) | cada una su archivo · repo y despliegue ordenado · etcd | **Repo y despliegue ordenado** ahora, con la comprobación por `sha256sum`; etcd si el número de excepciones crece | R1 | Fase 4 |
| **14** | **Nombre y tabla del rol** nuevo | `superadmin` en la tabla 45 · otra | **`superadmin`, tabla 45**: menor que 60 (los `goto` solo van hacia delante), distinta de la 30 y de la 99 | R2 | P11 |
| **15** | **Qué puede alcanzar el equipo `superadmin`** además del panel | solo el panel · el panel y lo del administrador · todo | **El panel y los servicios del administrador**, para no necesitar otro equipo; si se prefiere el mínimo, solo el panel y un equipo de soporte aparte | equipo | P11 |
| **16** | **Cómo se autentica la persona en el panel** | solo la sesión 802.1X del equipo · además un login personal | **Solo la sesión 802.1X** (el panel la consulta al mapper por la IP de origen); sumar un login personal si el equipo llega a compartirse, porque la auditoría solo sabría quién es el equipo | equipo | Fase 6 |
| **17** | **Interfaz y puerto del panel** | IP de servicios del `server` con restricción por rol · solo gestión | **IP de servicios (`10.0.0.10`, puerto propio) restringida por R2 al rol `superadmin`**: el equipo dedicado está en la red de datos; la gestión (`ens3`) solo sirve para que el panel hable con ONOS y el mapper | R2 | Fase 6 |
| **18** | **Suplente y emergencia** | un solo equipo · un segundo equipo | **Documentar el procedimiento de emergencia (editar el archivo por el repositorio)** y decidir si se guarda un segundo equipo apagado con su certificado | equipo | Fase 6 |

## 10. Fuera de este diseño

- **Soporte TI:** sigue por 802.1X con equipo asignado. La variante «soporte por el portal por ascenso» (grupo `soporteTI` mapeado al rol `soporte`, sin `login_directo`) queda aparcada.
- **Equipos y alumnos:** sin excepciones en la v1; el mismo mecanismo podría servir más adelante con la identidad del certificado como clave.
- **Permisos por grupo de LDAP** (el mapper ya recibe los grupos en el login): alternativa descrita en `DISENO_PANEL_ADMIN.md`, sección 13.1.
