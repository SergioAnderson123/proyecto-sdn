# Certificados, PC compartidas y escalabilidad

Cómo se identifican los equipos en la red del campus, qué hace hoy el proyecto (prueba de concepto), qué hacer si se filtra la contraseña del rol Profesor y qué habría que cambiar para llevarlo a un campus real. Complementa a [`DESPLIEGUE_UBUNTU.md`](DESPLIEGUE_UBUNTU.md) (sección 3c, el agente 802.1X, y sección 5, los usuarios).

## 1. Decisiones del equipo

- **Las PC son de uso general** (laboratorios y aulas): no se sabe quién las va a usar.
- **Cada PC lleva dos certificados, uno por rol**: por ejemplo `PC1-V303-Alumno` y `PC1-V303-Profesor`. **No hay certificados por persona.** El certificado identifica al *equipo en un rol*, no a quien se sienta.
- Las claves se protegen con la contraseña de la cuenta de la PC: la del rol **Alumno es conocida** y la del rol **Profesor solo la conocen los profesores y los JP**.
- **El rol JP (híbrido) no se usa**: quien ejerce de JP entra con el rol Profesor (Docente, VLAN 10). El perfil híbrido sigue pendiente en R2.

## 2. Cómo se hace en la vida real

| Modelo | Qué identifica | Cuándo autentica | Cómo se decide el rol |
|---|---|---|---|
| **Certificado de equipo** | La máquina | Al arrancar | Por el grupo o la unidad del equipo (p. ej. "PC de laboratorio") |
| **Certificado de usuario** | La persona | Al iniciar sesión | Por el grupo del usuario |

El modelo del equipo (un certificado por PC y rol) se parece al **certificado de equipo**, con el rol dentro de la identidad. Se emite **automáticamente**: una CA con inscripción automática (política de grupo), un MDM o SCEP/EST, con validez corta, renovación y revocación (CRL u OCSP). Los dispositivos personales usan un portal de alta o PEAP, los invitados el portal cautivo, y los equipos sin suplicante (impresoras, IoT), MAB; esas tres vías ya están en el diseño.

## 3. Cómo está hoy el proyecto y cómo encaja el modelo del equipo

- Hoy hay seis certificados (`alumno01..03`, `profesor01..02`, `support01`), emitidos a mano con `R1/radius/raddb/certs/regenerar.sh` y repartidos a los hosts con Ansible. En la prueba los tres `alumno` hacen de **identidad de equipo** (rol Alumno, en todas las PC de aula) y `support01` es la del **equipo de TI** (rol Soporte): su certificado va solo al host al que se asigna. Los de profesor quedan para pruebas puntuales: los profesores suben el equipo desde el portal con su cuenta personal.
- El Policy Mapper identifica al cliente por el **nombre del certificado** (su CN) y busca esa entrada en LDAP: de ahí salen el rol, la **MAC** y el **switch y puerto** permitidos (`description`, `physicalDeliveryOfficeName`, `roomNumber`). Si la MAC o la ubicación no coinciden, rechaza y mete la MAC en lista negra 300 s; si esos atributos no existen, no hace la comprobación.
- **El modelo `PC1-V303-Alumno` / `PC1-V303-Profesor` funciona sin cambiar el Policy Mapper**: basta una entrada en LDAP por cada PC y rol (con el grupo `alumno` o `profesor`, y la MAC y el puerto de esa PC) y un certificado con ese nombre. Lo que falta es automatizarlo: `regenerar.sh` y el LDIF están escritos para los seis usuarios de la prueba, y habría que generarlos a partir de una lista de PC.
- Una MAC solo admite **una sesión a la vez**: para pasar de Alumno a Profesor en la misma PC hay que cerrar la sesión antes (el agente avisa al Policy Mapper al desconectar).

## 4. Consideraciones de escalabilidad

1. **Crece con el número de equipos, no con el de personas.** Con P PC se necesitan 2P certificados y 2P entradas en LDAP, cada una atada a la MAC y el puerto de su PC. No hay un certificado por alumno. Y el atado a la MAC es natural: un certificado solo sirve en su equipo; copiado a otro, el Policy Mapper lo rechaza por la MAC.
2. **Cambios de equipo.** Si se sustituye o reubica una PC, hay que actualizar sus dos entradas (MAC, switch y puerto) y reemitir sus certificados. Conviene automatizarlo con un inventario de PC (`ldap-ubicacion.sh` ya aprende MAC, switch y puerto de lo que ONOS ve).
3. **Aprovisionamiento a mano.** Hoy se emiten y copian los certificados a mano. En un campus hay que automatizarlo (inscripción automática o SCEP/EST), con renovación. Los certificados del proyecto caducaron en las pruebas (60 días por defecto en FreeRADIUS), y la CRL del repositorio ya estaba vencida sin que RADIUS lo notara: **no se está comprobando la revocación**.
4. **Estado en memoria y una sola instancia.** Las sesiones del Policy Mapper están en memoria: un reinicio las pierde (aunque los flujos sigan en los switches) y no hay alta disponibilidad. ONOS AAA tampoco se ha probado con varias instancias.
5. **Flujos por equipo.** Cada host autenticado instala un flujo de sesión y una regla de EAPOL por puerto en su switch de acceso, además de las tablas de rol. Con muchos equipos por switch habría que comprobar los límites de tabla del hardware real (es un razonamiento, no una medición).
6. **Cierre de sesión.** ONOS AAA 2.7.0 no envía *accounting* a RADIUS, así que el cierre depende del agente (botón, apagado, enlace caído). Una PC que se apaga de golpe deja la sesión abierta; haría falta un *heartbeat* con caducidad, o un gancho PAM para el cierre de sesión en un escritorio.
7. **Carga de autenticación.** Una autenticación EAP-TLS completa son decenas de mensajes (en las pruebas, ~49 `Access-Request` para 7 autenticaciones). Con cientos de equipos arrancando a la vez hay que dimensionar RADIUS y ONOS AAA.

## 5. Si se filtra la contraseña del rol Profesor

**Qué protege esa contraseña.** Protege la clave privada del certificado `PC1-V303-Profesor` y/o el acceso a la cuenta de profesor de esa PC. Si se filtra, quien la conozca y use esa PC se autentica como Docente (VLAN 10). Gracias al atado por MAC y puerto, **el daño queda en esa PC**: si copian los archivos a otra, el Policy Mapper la rechaza por la MAC. El daño crece si **la misma contraseña** está en todas las PC.

**Una contraseña compartida entre varias personas acaba filtrándose**, y no hay forma técnica de impedirlo. Lo que se puede hacer es evitarla, limitar el daño, detectarla y revocar rápido, en este orden:

1. **Evitar el secreto compartido (la mejor solución).** Que la subida a rol Docente se haga con la **credencial personal** de cada profesor: la PC se autentica como Alumno (VLAN 20) y el profesor entra con su usuario y contraseña de LDAP por el **portal cautivo**, que ya está implementado y que da el rol según el grupo LDAP (`profesor` → Docente). Cada persona tiene su propia credencial, se puede revocar a una sola, y las sesiones quedan a su nombre. Así el certificado de Profesor desaparece de las PC.
2. **Si se mantienen los dos certificados por PC**, reducir la exposición:
   - **Contraseña distinta por PC** y rotación periódica (cada semestre), repartida por un canal seguro.
   - Que la clave sea **legible solo por el servicio del suplicante** (root) y no por la cuenta de alumno: el alumno no puede copiarla ni atacar su contraseña sin conexión.
   - **Mínimo privilegio** en el rol Docente: que la VLAN 10 alcance solo lo necesario (`R2/policy/policy.yaml`).
3. **Detectar.** El Policy Mapper registra cada sesión con rol, MAC y hora (`GET /events`). Por horario no sirve (hay aulas libres y gente que se queda de un día para otro), pero sí por **identidad**: con certificados de PC y contraseña compartida no se sabe quién fue; con la subida por portal (punto 1) cada sesión Docente queda a nombre de una persona.
4. **Revocar rápido.**
   - **Desactivar o borrar la entrada LDAP** de `PC1-V303-Profesor` (o de todas): el efecto es inmediato, porque sin entrada el cliente se queda sin rol y se rechaza.
   - **Cambiar la contraseña no invalida una clave ya copiada**: quien tenga el archivo y la contraseña vieja sigue pudiendo usarla. Hay que **revocar el certificado y reemitirlo con una clave nueva**, y para que cuente hay que **activar la comprobación de revocación (CRL) en FreeRADIUS**, hoy desactivada.

Un caso aparte, para tenerlo presente: si los **dos** certificados están en la PC y la contraseña de Alumno es **conocida por todos**, la protección de esa clave es nula. El rol Alumno lo asume el diseño (es el rol de menor privilegio), pero conviene que el certificado de Alumno no dé acceso a nada que no deba tener cualquier estudiante.

## 6. Subida a Docente por el portal cautivo (implementada)

Es la solución del punto 1 de la sección 5. **En las PC solo hay certificado de Alumno** (`PC1-V303-Alumno`): al encender, la PC se autentica sola por 802.1X y recibe el rol Alumno (VLAN 20). No hay certificado de Profesor.

Flujo:

1. La PC entra como Alumno (802.1X, certificado de equipo). Cualquiera puede usarla como alumno.
2. Un profesor o JP abre el **portal** y entra con **su usuario y contraseña personales de LDAP**. Las cuentas de profesor del LDIF son personales: no llevan MAC ni puerto, porque se mueven entre equipos.
3. El Policy Mapper valida contra LDAP, comprueba que su grupo (`profesor`) permite subir ese equipo y **cambia el flujo de sesión de esa MAC al rol Docente** (VLAN 10, tabla 10).
4. Al cerrar sesión en el portal, o al vencer el plazo, la PC **vuelve a Alumno**.

**Cuándo se permite (todas a la vez).** `POST /portal/login` (R1/shared/policy_mapper/policy_mapper.py, `_validar_ascenso`) acepta el ascenso solo si:
- la MAC tiene una **sesión 802.1X** (no del portal) y **no es ya un ascenso**;
- el **rol base** de esa sesión puede subir al rol del usuario, según la tabla `ascenso` de `R2/policy/policy.yaml` (hoy `alumno: [docente]`);
- el usuario **no tiene otra sesión** (en ese equipo ni en otro);
- la **ubicación coincide**: la que **ONOS AAA** tiene para esa MAC (`/onos/aaa/app/users`, entrada `AUTHORIZED`: el switch y el puerto donde recibió su EAPOL) debe ser la que el **Session Manager** guardó al admitir la sesión. Se lee de la tabla AAA y no de la de hosts de ONOS porque un host con sesión 802.1X casi nunca está en esta (su ARP no llega al controlador), mientras que en la AAA figura siempre. Si ONOS AAA no tiene a la MAC autorizada (por ejemplo, ya hizo `EAPOL-Logoff`) se rechaza (`ascenso_exigir_ubicacion: true`, el valor por defecto); con `false` solo se compara cuando la tiene. Las entradas viejas de otros puertos no cuentan si no están `AUTHORIZED`. La MAC ya coincide por construcción: es la de la sesión.

Cualquier otra combinación sigue respondiendo **409** y registra un evento `conflicto`, exactamente como antes. No se relaja ninguna comprobación de suplantación.

**Cómo convive con el antispoofing de 802.1X.** Mientras hay un ascenso, la sesión de esa MAC es la de la persona, y la identidad del equipo queda guardada como *base*:
- Si el **propio equipo reautentica** por 802.1X (misma identidad, MAC, switch y puerto que la base), no se trata como suplantación: se **anula el ascenso** y el equipo vuelve a Alumno (la persona tendrá que volver a subir).
- Si **otra identidad** autentica con esa MAC, sigue siendo suplantación: 409 y lista negra 600 s, como siempre.
- Si el agente avisa de que el equipo se desconectó (`/sessions/logout`, enlace caído, apagado), se cierra también el ascenso.

**Qué hace al terminar.** Cerrar sesión en el portal (`POST /portal/logout`) o vencer el plazo (`portal.duracion_s`, hoy 8 h salvo invitado) borra el flujo Docente y **reinstala el de Alumno**. Si no se pudiera reinstalar, el equipo debe autenticar de nuevo.

**La subida exige dos cosas**: un equipo con certificado válido (ya autenticado como Alumno) y una persona con credencial. No hay secreto compartido, revocar a un profesor es desactivar **su** cuenta, y cada sesión Docente queda a nombre de una persona.

**Lo que falta o hay que vigilar.**
- **Dónde corre el portal.** En el laboratorio de Mininet, en el contenedor `mininet` (`portal-up.sh`). En VNRT, en **uno de los hosts** (tipo `portal` en `slice.py config`, `slice.py portal`): tiene que estar en el mismo dominio de difusión que los clientes porque toma su MAC de la tabla ARP, y su puerto y las troncales entre switches deben ser de confianza en R2 (lo escribe `slice.py portal` en `R2/policy/policy.local.yaml`). Ver `DESPLIEGUE_UBUNTU.md`, sección 3d. No se ha ejecutado todavía contra las VM.
- El portal envía la contraseña en HTTP plano (HTTPS fuera del laboratorio), conviene limitar los intentos fallidos y `PORTAL_TOKEN` debe estar definido, porque la MAC la lee el portal de su tabla ARP.
- El plazo de 8 h puede no encajar con aulas abiertas que se usan de un día para otro: se ajusta en `portal.duracion_s`.
- Un ascenso no se persiste: reiniciar el Policy Mapper pierde las sesiones, y el equipo debe autenticar de nuevo.

### 6b. Y el administrador (Soporte)

El administrador es el rol más delicado (`student_records`, `network_management`...), así que no es obvio que deba subir con el mismo mecanismo.

| Opción | Cómo | A favor | En contra |
|---|---|---|---|
| **A. Equipo propio con certificado de Soporte** (recomendada) | El personal de TI trabaja en equipos suyos o en puertos propios, con un certificado de **equipo** de rol Soporte (`ADMIN-TI-01-Soporte`, VLAN 40), igual que las PC de aula llevan el de Alumno | Ninguna contraseña de administrador se teclea en una PC compartida; no se puede subir desde un aula; el certificado va en un equipo que TI controla (disco cifrado, TPM) | Quien tiene el equipo de TI tiene el acceso; revocar es revocar el certificado; un técnico que se mueve necesita su equipo |
| **B. Subir por el portal** | Una línea más en `R2/policy/policy.yaml`: `ascenso: {alumno: [docente, soporte]}`; la cuenta `support01` pasa a ser personal (sin MAC ni puerto en el LDIF) | Un solo mecanismo para todos los roles; sin certificado de administrador en ninguna PC; revocar es desactivar la cuenta | La contraseña del rol con más poder se teclea en una PC de aula (keylogger) y el portal usa HTTP plano; basta una PC de aula para intentar subir; la ubicación ya no restringe nada |

**Recomendación:** A para el administrador y B para el docente. Si TI necesita subir de forma excepcional desde cualquier equipo, que B sea el último recurso y con **tres condiciones**: HTTPS en el portal (sin esto no debe usarse), una duración corta del rol (`portal.duracion_s: {soporte: 1800}`) y un segundo factor o, como mínimo, una cuenta de administrador distinta de la de uso diario. Lo que la implementación actual ya da: la tabla `ascenso` es por rol (se habilita Soporte sin tocar código), no se encadena (un Docente no sube a Soporte) y cada sesión queda a nombre de una persona. Lo que no da todavía: restringir el ascenso a un conjunto de puertos o equipos.

## 7. Qué haría falta para llevarlo a un campus

- Generar los certificados y las entradas de LDAP a partir de un **inventario de PC** (uno por equipo, de rol Alumno), en lugar de los tres alumnos de prueba.
- Automatizar la emisión, renovación y revocación de certificados, y activar la comprobación de revocación en FreeRADIUS.
- Desplegar el portal cautivo en la red real con HTTPS (hoy corre en el laboratorio de Mininet y, sin probar, en un host de VNRT), y generalizar la subida a Docente (sección 6, ya implementada) con la credencial personal del profesor y no con una contraseña compartida.
- Persistir las sesiones y poner el Policy Mapper, RADIUS y ONOS en alta disponibilidad.
- Cerrar la sesión también cuando el equipo se cae o el usuario cierra sesión en un escritorio (*heartbeat* y gancho PAM).
- Comprobar los límites de tabla y de autenticaciones simultáneas con carga real.
