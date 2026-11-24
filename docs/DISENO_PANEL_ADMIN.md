# Diseño: panel web de administración (borrador para revisar, 23-nov-2026)

**Estado (24-nov): las fases 1 y 2 están construidas, desplegadas y probadas en la red real. Lo implementado, con su estado y lo pendiente, está en [`PANEL_ADMIN.md`](PANEL_ADMIN.md); este documento conserva el diseño y las decisiones.** Código en `R2/panel/` (`datos.py` lógica pura, `panel.py` recolector y servidor, `static/` la página; 214 pruebas en `R2/panel/tests`). Lo que muestra: resumen con avisos, sesiones, eventos con filtros y las vistas de ONOS por nodo (el detector de vistas desiguales, en pantalla). Solo biblioteca estándar de Python; solo GET. **Acceso (decisión del equipo): solo el rol `superadmin` entra y edita, sin contraseña ni token** (sección 8): `datos.decidir_acceso` toma la IP de origen, la busca en ONOS (`/hosts`) y en las sesiones del mapper, y solo deja pasar si el rol de esa sesión es el de administración; el único camino alterno es el túnel SSH local, de **emergencia y solo lectura** (editar exige siempre el rol). Como el rol aún no existe en la red (P11), hoy el panel escucha solo en `127.0.0.1`. **Pendiente:** la reparación manual (fase 3) y lo listado en `PANEL_ADMIN.md`, sección 9. Lo escribe quien mantiene la capa 3 y el vigilante (R2) a partir de lo que se vio operando la slice el 23-nov; las decisiones abiertas están marcadas para que las revise el equipo.

**Fase 2 (vigilante), construida el 23-nov y desplegada el 24-nov:** el vigilante publica `GET /estado` (`R2/policy/estado.py`, 24 pruebas con los datos reales de la slice): switches con su nivel y su padre hacia la raíz, cada cable que alguna vez vio con su uso (`arbol` si algún árbol de destino lo usa, `reserva` si está vivo y sin uso, `caido`) y un historial de eventos (cable o switch caído o restaurado, cambio detectado con los flujos por switch, aplicado, alta rápida, red partida y recuperada). Solo registra: no cambia ninguna decisión, y un fallo al armarlo solo da un aviso en el log. No incluye hosts, MAC ni IP. El panel lo lee del vigilante que manda (1 petición más por ronda) y lo enseña en la pestaña **Vigilante**: topología en SVG (los cables rodean los switches que se les cruzarían) e historial; los cables caídos y la red partida suben a los avisos del resumen. **Con los datos reales, los 7 cables están «en uso»** (hay árboles hacia acc-1, acc-2 y el borde): ningún cable queda de reserva hoy. Las reglas de los switches con filtros se hicieron después (siguiente párrafo).

**Flujos de los switches con filtros y comandos de `ovs-ofctl` (24-nov, desplegado y probado en la red real):** pestaña **Flujos** (`R2/panel/flujos.py`, 47 + 15 pruebas). Lee los flujos de UN switch del nodo **maestro** (nunca de otro), bajo demanda, con caché de 10 s (mínimo 3 s entre lecturas aunque se pulse «Releer»), una lectura a la vez en todo el panel y 16 s de espera; si ONOS falla enseña la lectura anterior marcada como antigua. Filtros: tabla, prioridad (valor o rango), quién la instaló, estado, puerto de entrada, MAC, EtherType, VLAN, IP origen/destino, protocolo, puertos TCP/UDP, acción (salida, ir a tabla, medidor, descarte, controlador, modifica), solo con paquetes y texto; orden y paginación. Cada regla trae su **explicación** (catálogo prioridad/tabla; una prueba vigila que no falte ninguna prioridad de `R5/deploy/prioridades.yaml`). **Generador de comandos:** con los mismos filtros arma el `ovs-ofctl -O OpenFlow13 dump-flows br0 "..."` equivalente (la prioridad, las acciones y los paquetes van con `grep -E`, porque ovs-ofctl no los filtra; el rango de prioridad se convierte en una expresión como `priority=[4-9][0-9]{4}[, ]`, comprobada contra todos los números, y **cada comando trae la explicación de cada una de sus partes**, para poder defenderlo en una presentación), el `ofproto/trace` de un paquete concreto y notas sobre lo que ovs-ofctl no puede hacer (el estado es de ONOS; `ip_dst` exacto no muestra las rutas por prefijo que la contienen; para eso está «cubre» y la traza). El cookie de una app se deduce del id del flujo (`id >> 48`). Los valores de los filtros nunca pueden colar un comando (solo caracteres inofensivos). **Validado contra un Open vSwitch real** (contenedor Ubuntu 24.04): 35 comandos generados, todos válidos. Probado con ONOS real: la prueba A (conteos, contadores y cookie por app) coincide con el switch; la traza y la caída de enlace siguen en pruebas.

**Políticas de R2 y archivos fijos (24-nov, desplegado y probado en la red real):** pestañas **Políticas** y **Configuración**. El panel sigue usando solo la biblioteca estándar: quien lee el YAML es `R2/panel/exportar_config.py`, que corre en el borde (con PyYAML, por Ansible) y deja un JSON; `panel_config.yml` lo genera (recoge las copias del repo y de cada controlador, ejecuta el exportador y lo deja en `/etc/panel-red/config.json` del server; solo lee) y el panel lo relee solo cuando cambia. **No se actualiza solo:** hay que repetir `panel_config.yml` tras cambiar algo, y la pantalla dice de hace cuánto es la foto. *Configuración:* `policy.yaml`, `policy.local.yaml`, el plan de subredes, los **hosts estáticos**, la unidad del vigilante (sus argumentos) y el mapa de prioridades, con las copias de cada fuente (huella y tamaño), el estado (`igual`, **`diverge`**, `una_copia`, `sin_datos`) y el diff entre el repo y lo desplegado. *Políticas:* roles (grupo LDAP -> matriz -> tabla y VLAN), matriz rol/servicio con «sin regla» distinguido de «denegado», catálogo de servicios (y los que tienen reglas pero ningún host, como `internet`), portal, ascenso, cuarentena, confianza y los flujos que R2 instala en la tabla de cada rol, calculados con el MISMO código que el mapper (`policy_tables`; solo se hizo opcional su `import httpx`). **Comparar con un switch:** los flujos que la política espera frente a los instalados bajo el appId de R2 (faltan / sobran). Pendiente de esta parte: la configuración JSON de las apps de ONOS (DHCP, gateway-arp, medidores) y el flujo de edición con simulación (§12), que sigue siendo solo diseño.

**Corrección al leer el código del mapper (`eventos.py`):** en modo HA la bitácora **sí se guarda en etcd** (`events`, máximo 200), no por réplica como decía la sección 2. El panel igual guarda la suya en SQLite (se conserva lo que el mapper rota); falta comprobar en vivo que dos lecturas por réplicas distintas den lo mismo.

## 1. Qué se quiere

Un servicio web para quien administra la red, con:

1. **Eventos y logs del Policy Mapper** y **usuarios en sesión**.
2. **Rutas del vigilante**: el árbol activo, los puertos bloqueados y cómo cambia al recalcular (dibujado, no solo texto).
3. **El detector de vistas desiguales** (`registrador_onos.py`) y lo que le convenga ver a un administrador: salud de ONOS, Atomix, mapper, vigilante, DHCP, medidores.
4. **Reglas de los switches con filtros.**
5. **Reparación manual** de una vista de ONOS que quedó desigual (la reparación automática queda fuera por ahora, a propósito).

**Fuera de alcance de la v1:** cambiar políticas, crear usuarios, tocar flujos, reiniciar servicios. La v1 es de lectura salvo la reparación manual de la sección 7.

## 2. De dónde salen los datos (lo que ya existe y lo que falta)

| Fuente | Qué da hoy | Qué falta |
|---|---|---|
| Policy Mapper (gateway `192.168.201.254:5000`, réplicas `:15002`) | `/sessions`, `/events`, `/health/ha`, `/portal/estado` | `/events` está **en memoria y por réplica** (pendiente conocido): sin persistencia el panel vería eventos distintos según qué réplica conteste. Pedirle a R1 un historial compartido (etcd) o que el panel guarde el suyo. |
| ONOS (REST, 3 nodos) | `/devices`, `/links`, `/hosts`, `/flows/{switch}`, `/mastership/{switch}/master` | Ninguna lectura de flujos debe ir a un nodo que no es el maestro del switch (ver 4). |
| Vigilante (`:8098`) | `/salud` (estado de una palabra) y el journal | **Un `/estado` JSON**: grafo vivo, raíz, árbol (padre de cada switch), puertos bloqueados por switch, último cambio con su motivo y un histórico corto de recálculos. Sin él, el panel solo puede mostrar texto. |
| Registrador de latencia (`/var/log/registrador-onos/medidas.log` en cada ctrl) | líneas de ronda, `LENTO`, `VISTA_DESIGUAL`, `VISTA_IGUAL` | Exponerlo (un servicio mínimo de solo lectura que sirva las últimas N líneas y el estado actual) o que el agregador lo lea. |
| Switches (OVS) | `ovs-ofctl dump-flows` (por SSH) | Solo bajo demanda y por Ansible o una cuenta de solo lectura; nunca en un bucle. |
| App DHCP, medidores, R5 | `/hosts` de ONOS (IP, MAC, ubicación), logs, métricas de Prometheus/Grafana (R5, en ctrl-2) | Reutilizar Grafana para las series de tiempo; el panel enlaza, no duplica. |

## 3. Dónde vive el servicio (decisión abierta)

Principio: **no compartir destino con lo que vigila.** Lo que más se quiere ver en una crisis es qué le pasa a ONOS o a un ctrl.

| Candidato | Medido o de diseño | Veredicto |
|---|---|---|
| Un `node-ctrl-N` | 6 GB y 20 GB de disco, ya con ONOS, Atomix, etcd y el mapper (ctrl-1 además con el disco más lento; ctrl-2 con Grafana/Prometheus de R5) | **No.** Comparte destino con ONOS y le añade carga justo donde el 23-nov hubo atascos. |
| `switch-borde` | **Medido 23-nov:** 961 MB de RAM (407 disponibles, sin swap), 1 vCPU, 6,8 GB de disco con 2,4 GB libres; ya hace de nodo de Ansible y de gateway del mapper | **No.** Un fallo por memoria ahí afectaría al switch de borde. |
| VM `server` (`192.168.201.23`) | De diseño: 2 vCPU, 2 GB de RAM, 10 GB de disco; fuera del camino de ONOS/Atomix; es donde la fase E sitúa el portal (`10.0.0.10:8443`) | **Candidata preferida, con condiciones:** (1) **acceso solo para el rol `superadmin`** (decisión del 23-nov, `DISENO_EXCEPCIONES_POR_SESION.md` 4.7): como el equipo dedicado está en la red de datos, el panel escucha en la IP de servicios (`10.0.0.10`, puerto propio) y R2 lo restringe a ese rol; la interfaz de gestión (`ens3`) solo la usa el panel para hablar con ONOS y el mapper, así que hace de **puente entre las dos redes**; (2) es un elemento de las pruebas de autorización (`nc 10.0.0.10 8101`) y puede reiniciarse o reconfigurarse; (3) si el portal vive ahí, panel y portal comparten destino. **Medir antes** sus recursos reales (`./lanzar.sh --tags recursos`). |
| Una VM nueva | No hay: la slice son 14 VM fijas | Es lo ideal en una red real (plano de gestión aparte). |

Si el `server` no sirve, la alternativa es un ctrl con límite de memoria y prioridad baja (como el registrador), sabiendo que comparte destino.

## 4. Arquitectura

```
fuentes (mapper, ONOS, vigilante, registrador, OVS) --> agregador --> API --> navegador
                                          \--> SQLite (histórico corto de eventos y recálculos)
```

- **Un solo agregador** consulta cada pocos segundos y **cachea**; los navegadores leen de la caché. La lección del exporter de R5 (unas 10 llamadas a ONOS por consulta de Prometheus, cada 5 s, desde dos sensores) es que no se debe consultar ONOS una vez por navegador ni por gráfica.
- **Las lecturas de flujos de un switch van a su nodo maestro** (`GET /mastership/{switch}/master`), con caché corta, como ya hace el vigilante (`onos_multi.py`). Las lecturas remotas son las que se atascaron el 23-nov.
- **Presupuesto de carga:** una ronda completa del agregador no debe pasar de unas pocas peticiones por segundo al clúster; todo lo caro (flujos de un switch, comparación con OVS) es **bajo demanda y de uno en uno**.
- **El árbol se dibuja en el navegador** (SVG a partir del JSON del vigilante): puertos bloqueados en rojo, raíz marcada, y un paso a paso del último recálculo (qué cable cayó, qué reglas se quitaron y se añadieron).
- Estado crítico: ninguno. Si el servicio se cae, la red no lo nota. Se puede levantar una segunda instancia sin coordinación (el histórico se pierde, lo demás no).

## 5. Pantallas de la v1 (solo lectura)

1. **Resumen:** switches (disponibles, maestro), enlaces, salud de ONOS por nodo, Atomix, mapper, vigilante, DHCP; avisos activos (`VISTA_DESIGUAL`, `LENTO`, vigilante en `partida`).
2. **Sesiones y eventos:** usuarios en sesión (usuario, rol, MAC, IP, switch y puerto, método, antigüedad) y la línea de eventos con filtros.
3. **Vigilante:** árbol dibujado, puertos bloqueados, histórico de recálculos y su causa.
4. **Vistas de ONOS:** por switch, qué dice cada nodo (la información del detector) y desde cuándo hay discrepancia.
5. **Reglas de los switches** (sección 6).
6. **Latencias:** las del registrador, o un enlace a Grafana.

## 6. Reglas de los switches con filtros

- **Fuente:** `GET /flows/{switch}` del maestro, con caché de 5 a 10 s. **Comparar con el propio switch** (`ovs-ofctl`) solo bajo demanda, con un botón «verificar en el switch» (ha habido sesiones cuyo flujo 40001 faltaba en ONOS y flujos que el switch tenía y ONOS no).
- **Filtros:** switch, tabla (0, 10, 20, 40, 50, 60, 61, 99…), prioridad (valor o rango), quién la instaló (`appId`: vigilante, R2, gateway-arp, DHCP, medidores, espejo de R5), campos de coincidencia (puerto de entrada, MAC origen/destino, IP, puerto TCP/UDP, VLAN), tipo de acción (salida, goto, meter, descarte), estado (ADDED, PENDING_ADD…) y contadores (solo las que han visto paquetes).
- **Explicación de cada regla conocida:** un catálogo prioridad/tabla → significado, tomado de `DISENO_FASE_E.md` y `R5/deploy/prioridades.yaml` («40001 en la tabla 0: sesión 802.1X de este equipo», «40500: DHCP al controlador», etc.). Es lo que más ayuda a un administrador.
- **Límites:** paginación y un tope por consulta (con la capa 3 hay como mucho unos cientos de reglas por switch, pero el panel no debe asumirlo); si ONOS tarda, mostrar el dato en caché marcado como antiguo.

## 7. Reparación manual de una vista desigual

Lo probado en vivo el 23-nov (`docs/DISENO_CAPA3.md`, V9 quinquies): **dar el mando de un switch a otro nodo y devolverlo** hace que las réplicas vuelvan a ver ese switch disponible y lo conserven.

Flujo del botón, por switch y solo uno a la vez:

1. **Precondición:** el detector marca ese switch como `VISTA_DESIGUAL` desde hace más de la gracia, y no hay otra reparación en curso ni una hecha hace menos de 60 s.
2. **Confirmación** que muestra exactamente qué pasará (`device-role <switch> <nodo> master` y su devolución) y lo que ven los tres nodos antes.
3. **Ejecución:** mover el mando a un nodo distinto, esperar a que se asiente (~20 s) y devolverlo; verificar y mostrar lo que ven los tres nodos después.
4. **Si falla:** devolver el mando al nodo original y avisar; nunca reintentar solo.
5. **Auditoría:** quién, cuándo, qué switch, antes y después. Límite por hora.

**Por verificar antes de construirlo:** si ONOS 2.7 permite hacerlo por REST (`PUT /mastership`) en lugar de entrar por la consola de Karaf con `sshpass` como hace hoy `consulta_cli_onos.yml`. No está probado.

La reparación **automática** no se hace ahora. Si se hace, que sea desde el vigilante, apagada por defecto, de un switch cada vez y solo con discrepancias que duren minutos; antes conviene ver con el detector cuántas veces ocurre de verdad.

## 8. Seguridad (pesa más que en el resto)

La página mostraría usuarios, MAC e IP de las sesiones: **datos personales de los estudiantes**.

- **Acceso solo para el rol `superadmin`** (rol nuevo): un equipo dedicado que se autentica por 802.1X y no entra por el portal. El panel no pide contraseña: consulta al mapper por la IP de origen de la conexión y deja pasar solo si esa sesión tiene el rol `superadmin`; R2 además abre su puerto únicamente a ese rol. Sin rol de solo lectura en la v1. Se audita con la identidad del equipo, su IP y su MAC.
- **Cómo se decide (implementado en `R2/panel/datos.py`, `decidir_acceso`):** IP de origen de la conexión TCP (las cabeceras `X-Forwarded-For` se ignoran) → MAC por ONOS → sesión del mapper → rol. Se rechaza si el equipo no tiene sesión, si la IP la tienen dos MAC distintas (ambigua), si la sesión venció o si el rol no es el de administración. **Límite conocido:** la identidad por IP es tan fuerte como la protección contra suplantación de IP de la red de datos; la defensa real es que R2 abra el puerto del panel solo a ese rol (el flujo de sesión va por puerto y MAC), y el panel es la segunda barrera. Cada acceso permitido o denegado se registra con la IP y el motivo.
- **Editar:** cuando existan acciones de edición (fases 3 y posteriores) exigirán **siempre** el rol, también por el túnel local; hoy el panel responde 405 a cualquier método que no sea GET y deja constancia del intento.
- **TLS**, y el puerto del panel abierto en la red de datos **solo al rol `superadmin`** (R2). El acceso desde la gestión (`192.168.201.0/24`, por ejemplo vía `switch-borde`) queda para operación y emergencia.
- Las acciones llevan token anti-CSRF y quedan en un registro de auditoría que el propio panel no puede borrar.
- Credenciales (ONOS, mapper, LDAP) en un archivo `0600` solo de root, como `registrador-onos.env`. Las contraseñas de laboratorio del repo hay que cambiarlas antes de usarlo fuera del laboratorio.

## 9. Qué hay que construir

| Pieza | Dueño probable | Notas |
|---|---|---|
| `/estado` del vigilante (grafo, árbol, bloqueados, histórico) | R2 | Pequeño, con pruebas sin red como el resto del vigilante. |
| Historial compartido de eventos del mapper | R1 | Hoy en memoria por réplica. |
| Servicio de solo lectura del registrador | R2 | O lectura por el agregador. |
| Agregador + API + caché + SQLite | por decidir | Procesos pequeños, sin dependencias pesadas. |
| Front (SVG del árbol, tablas con filtros) | por decidir | Servido por el mismo servicio. |
| Despliegue por Ansible (como `registrador_onos.yml`) | R2 | Con quitar y límites de memoria. |

## 10. Fases

1. **Lectura sin dibujo:** resumen, sesiones, eventos y vistas de ONOS, con el agregador y la caché. Aceptación: no añade más de unas pocas peticiones por segundo a ONOS y no cambia nada. **HECHA y desplegada** (10 peticiones cada 10 s).
2. **Vigilante y reglas:** `/estado`, el árbol dibujado y las reglas con filtros. Aceptación: reproducir la caída de un cable y ver el recálculo. **Hecha y desplegada, con las políticas y los archivos fijos añadidos; la prueba de aceptación (la caída de un cable en vivo con el panel abierto) está pendiente.**
3. **Reparación manual:** con auditoría y permisos. Aceptación: reparar una vista desigual provocada a propósito, con la reversión probada. **Pendiente.**
4. **Después:** reparación automática, acciones sobre sesiones, más fuentes.

## 11. Decisiones abiertas

1. **Dónde vive** (sección 3): *resuelta el 24-nov* — en el `server` (1,5 GB disponibles; el panel usa ≈ 16 MB).
2. **Quién mantiene qué:** el panel toca R1 (mapper, LDAP), R2 (vigilante, reglas) y R5 (Grafana, espejo).
3. **Historial de eventos del mapper:** *resuelta* — el panel guarda el suyo en SQLite; en modo HA el mapper además lo guarda en etcd (máximo 200).
4. **¿REST o Karaf** para la reparación manual?
5. **Alcance de permisos:** *resuelta el 23-nov* — solo el rol `superadmin`; sin rol de solo lectura en la v1.
6. **Fuente única de la política:** hoy cada réplica del mapper lee su propio `policy.yaml` (sección 12); ¿repo, etcd u otra?
7. **¿Permisos por usuario?** Hoy el permiso lo da el rol; una excepción por usuario es trabajo nuevo de R1+R2 (sección 13).

## 12. Configuración estática y políticas (idea del equipo, 23-nov)

Que el panel muestre, y más adelante permita cambiar con cuidado, los archivos que gobiernan la red. Lo que hay hoy y cómo se recarga cada cosa:

| Archivo | Qué gobierna | Quién lo lee | Cómo se recarga hoy |
|---|---|---|---|
| `R2/policy/policy.yaml` | `roles` (grupo → tabla/VLAN), `rules` (matriz rol → servicio), `services` (IP y puerto de cada servicio), `portal`, `ascenso`, `cuarentena`, `trusted`, `sin_usuarios` | el Policy Mapper (`policy_tables.cargar_politica`) al arrancar | **reiniciar el mapper** (cada réplica); `POST /policy/sync` solo reinstala las tablas en los switches |
| `R2/policy/policy.local.yaml` (no versionado) | lo propio de la slice (MAC del portal, IP del server, puertos de confianza, reenvío); se fusiona sobre `policy.yaml` | el mapper | lo genera `generar_politica_local.py` (fase E, `70_fase_e.yml`); reiniciar el mapper |
| `R2/policy/planes/vnrt_topologia14.json` | plan de subredes: gateways, ámbitos DHCP y su concesión, switches de cada subred | el vigilante (`--subredes`) y los generadores de la app DHCP y de `gateway-arp` | `68` (vigilante), `82` (DHCP), `76` (gateway ARP) |
| `R2/policy/planes/hosts_fijos_capa3.json` | hosts con IP fija (el `server`) y su ubicación | el vigilante | `68` (copia el archivo y reinicia el servicio) |
| Configuración JSON de las apps de ONOS (DHCP, gateway-arp, medidores) | ámbitos, gateways, tasas | las propias apps | `POST /onos/v1/network/configuration/apps/<app>` (los playbooks 76, 82, 90) |
| Parámetros del vigilante (`estable`, `intervalo`, `olvidar_host`…) | tiempos de recálculo y olvido | la unidad `vigilante-capa3` | `68` |
| LDAP (usuarios y grupos) y RADIUS | quién es cada persona y su grupo | R1 | semilla LDIF y despliegue de R1 |

**Qué haría el panel:**
- **v1 (solo lectura):** cada archivo con su contenido vigente, qué significa cada sección y **qué hay de distinto entre lo desplegado y lo que está en el repo** (la divergencia entre réplicas es un riesgo real: cada una lee su `policy.yaml`).
- **Validar antes de aplicar:** reutilizar el código que ya existe (`cargar_politica`, `flujos_tabla_rol`, los generadores y la vista previa) para mostrar **qué reglas de OpenFlow se añadirían o quitarían en cada switch** al añadir o cambiar una política, sin instalar nada.
- **Editar (fase posterior):** flujo editar → validar → simular el cambio por switch → confirmar → aplicar réplica a réplica comprobando sesiones y acceso → auditar y poder volver atrás. Es lo más delicado del panel: un `policy.yaml` mal editado puede dejar a todos sin acceso o abrir lo que no debe, y no hay recarga en caliente. Por eso conviene que el cambio pase por el repositorio (rama o revisión) y el panel lo valide y lo despliegue, en vez de escribir directamente en las VM.
- **Añadir una política** (por ejemplo permitir un servicio a un rol): una fila en `rules` (y en `services` si el servicio es nuevo); el panel enseñaría el diff de reglas por switch y, tras confirmar, reiniciaría las réplicas y ejecutaría `POST /policy/sync`.

## 13. Permisos para un usuario concreto (pregunta del equipo)

**Estado actual.** La política se decide por **rol**: el grupo LDAP del usuario llega por RADIUS como una VLAN, el flujo de sesión (tabla 0, prioridad 40001, `in_port` + `eth_src`) pone esa VLAN y salta a la **tabla de su rol**, y esa tabla solo mira **destino** (`VLAN_VID` + IP y puerto TCP del servicio). No mira quién es la persona, y `policy.yaml` no tiene ninguna sección de excepciones por usuario. Hoy un permiso individual obliga a elegir una de estas vías:

| Opción | Qué hay que tocar | Cuándo conviene |
|---|---|---|
| **A. Un rol nuevo** (grupo LDAP + VLAN/tabla) | LDAP (grupo y miembro), RADIUS (grupo → VLAN, R1), `policy.yaml` (`roles` con una tabla libre **menor que 60** —no la 30 de IoT/MAB—, y todas las filas de `rules` del rol nuevo), **una rama nueva en el webhook del mapper** (el mapeo rol → VLAN está escrito en el código y un rol desconocido se rechaza), reiniciar el mapper y sincronizar. Cada rol crea una tabla en cada switch de acceso. | Una categoría que se repetirá (por ejemplo, «docentes investigadores»). Para una sola persona es pesado y escala mal: un rol por excepción. |
| **B. Pasar al usuario a otro rol existente** | solo LDAP | **No recomendado:** da todos los permisos de ese rol (viola el mínimo privilegio). |
| **C. Excepción por sesión** *(no existe)* | Mapper (R1) y generador de tablas (R2) | Un permiso puntual a una persona, a veces temporal. |

**Propuesta para la opción C.** Una sección `excepciones` (en `policy.yaml` o en su propio archivo) con `usuario`, `servicio`, vigencia y motivo. Al admitir la sesión de ese usuario, el mapper instala **en la tabla de su rol** reglas adicionales de prioridad mayor que las del rol, con `eth_src` = la MAC de su equipo, que permiten solo ese servicio; las borra al cerrar sesión, al vencer o al ausentarse el equipo, igual que el flujo 40001. Ventajas: ni roles ni VLAN nuevas, es temporal y auditable. Costes y riesgos: está **atada al equipo con el que se autentica** (si entra desde otro, se aplica allí); hay que persistirla para el HA (etcd) y **reconciliarla** como ya se hace con el flujo 40001 (una sesión sin su flujo es el fallo que vimos el 23-nov); cuenta reglas extra por usuario; y toca R1 y R2 a la vez.

**Recomendación:** para algo recurrente, la opción A hoy mismo; para permisos individuales, construir la C. El panel sería el sitio natural para gestionarlas (alta con motivo y caducidad, lista de excepciones activas, baja manual), siempre con el flujo de validar y simular de la sección 12.

### 13.1 Refinamiento (23-nov): la excepción se define por CUENTA del portal, no por MAC

El modelo real del proyecto: los **equipos** son estáticos y entran por 802.1X con el certificado del equipo, y todos tienen por defecto el rol **alumno**; en LDAP el «alumno» es una computadora, no una persona. **Las personas solo existen como cuentas del portal** (invitado, ascenso a docente y, si se decide, soporte). Por eso una excepción «para un usuario» solo tiene sentido para una **cuenta del portal**:

- **Se define por cuenta** (`usuario` tal como lo escribe la persona en el portal, el mismo que registra el mapper en la sesión), en la configuración. No depende de ninguna MAC.
- **Se aplica a la sesión de portal de esa cuenta.** El interruptor solo ve cabeceras de red, no cuentas: el único asa que existe para que una regla alcance a una persona es la MAC del equipo desde el que entró, y el mapper ya la usa para el flujo 40001. Que la regla dependa de que el equipo esté conectado **no es un defecto**: una excepción no debe sobrevivir a la sesión. Si el equipo se apaga, cambia de puerto o la sesión vence, el mapper ya cierra la sesión; las reglas de excepción se borran en el mismo paso, y al volver a entrar por el portal se reinstalan.
- **Hereda las protecciones del ascenso.** Solo se instalan después de que el ascenso o el login hayan pasado sus comprobaciones (misma MAC que la sesión 802.1X, misma ubicación en ONOS AAA), así que un tercero no puede reclamarlas con otra MAC.
- **Excepciones por equipo** (opcional): para un equipo estático concreto, la misma tabla con `sujeto: equipo` y la identidad del certificado, sin portal. Mismo mecanismo, otra clave.

**Alcance acordado (23-nov).** Las excepciones son **para cuentas de profesor** (las que suben un equipo por el portal). **Soporte TI no las necesita:** se le asigna un equipo y se autentica por 802.1X, como hoy. La variante «soporte por el portal por ascenso» (grupo `soporteTI` → rol `soporte`, `login_directo` solo `invitado`, `ascenso: alumno: [docente, soporte]`, duración corta) sigue siendo posible y queda aparcada. Si algún día se abre soporte al portal, hay que revisar antes que el portal **limite los intentos fallidos** de contraseña (no se ve en `server.py`).

**El diseño completo y el proceso para construirlo están en [`DISENO_EXCEPCIONES_POR_SESION.md`](DISENO_EXCEPCIONES_POR_SESION.md).**

**Dos formas de guardar las excepciones** (decisión abierta):

| | Qué es | Pros | Contras |
|---|---|---|---|
| **Lista en configuración** | `excepciones: [{usuario, servicio, hasta, motivo}]` en `policy.yaml` (o en su archivo) | Simple; encaja con el flujo repo + validar + simular del panel | Un cambio de política (reiniciar réplicas) por cada alta o baja |
| **Grupos de permiso en LDAP** | `permisos: {perm_notas: {servicios: [grade_system]}}` en `policy.yaml`, y la excepción es pertenecer al grupo LDAP `perm_notas` | **Sin crear roles ni VLAN**; el mapper ya recibe los grupos de la cuenta en el login; escala a muchas personas y la fuente de verdad de las personas sigue siendo LDAP | El panel necesitaría escribir en LDAP (cuenta con permiso de escritura, riesgo); más trabajo en R1 |

Recomendación: empezar por la lista en configuración (pocas excepciones, panel de solo lectura más el flujo del repositorio), y dejar el formato listo para pasar a grupos de permiso. En ambos casos el panel tendría: alta con motivo y caducidad, lista de excepciones activas (quién, qué servicio, desde cuándo, sesión asociada), baja manual y el efecto por switch antes de confirmar.

**Lo que hay que construir** (R1 + R2) está detallado en `DISENO_EXCEPCIONES_POR_SESION.md`, con las fases, los siete caminos de cierre de sesión que deben retirar las reglas, la compatibilidad del estado HA y la validación en vivo.
