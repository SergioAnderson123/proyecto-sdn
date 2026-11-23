# Diseño: panel web de administración (borrador para revisar, 23-nov-2026)

**Estado: solo diseño. Nada de esto está implementado.** Lo escribe quien mantiene la capa 3 y el vigilante (R2) a partir de lo que se vio operando la slice el 23-nov; las decisiones abiertas están marcadas para que las revise el equipo antes de tocar código.

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
| VM `server` (`192.168.201.23`) | De diseño: 2 vCPU, 2 GB de RAM, 10 GB de disco; fuera del camino de ONOS/Atomix; es donde la fase E sitúa el portal (`10.0.0.10:8443`) | **Candidata preferida, con condiciones:** (1) escuchar **solo en la interfaz de gestión** (`ens3`), no en la de servicios (`10.0.0.10`), con cortafuegos; (2) es un elemento de las pruebas de autorización (`nc 10.0.0.10 8101`) y puede reiniciarse o reconfigurarse; (3) si el portal vive ahí, panel y portal comparten destino. **Medir antes** sus recursos reales (`./lanzar.sh --tags recursos`). |
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

- **Login** contra el LDAP del proyecto, con un grupo de administradores; permisos separados para **ver** y para **reparar**.
- **TLS** y escuchar **solo en la red de gestión** (`192.168.201.0/24`); si hace falta acceso externo, entrando por `switch-borde` como proxy, nunca expuesto a la red de datos.
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

1. **Lectura sin dibujo:** resumen, sesiones, eventos y vistas de ONOS, con el agregador y la caché. Aceptación: no añade más de unas pocas peticiones por segundo a ONOS y no cambia nada.
2. **Vigilante y reglas:** `/estado`, el árbol dibujado y las reglas con filtros. Aceptación: reproducir la caída de un cable y ver el recálculo.
3. **Reparación manual:** con auditoría y permisos. Aceptación: reparar una vista desigual provocada a propósito, con la reversión probada.
4. **Después:** reparación automática, acciones sobre sesiones, más fuentes.

## 11. Decisiones abiertas

1. **Dónde vive** (sección 3): medir el `server` y decidir con el equipo.
2. **Quién mantiene qué:** el panel toca R1 (mapper, LDAP), R2 (vigilante, reglas) y R5 (Grafana, espejo).
3. **Historial de eventos del mapper:** en el panel o compartido en etcd.
4. **¿REST o Karaf** para la reparación manual?
5. **Alcance de permisos:** quién puede reparar y quién solo ver.
