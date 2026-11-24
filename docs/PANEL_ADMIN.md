# Panel de administración de la red: lo implementado (estado al 24-nov-2026)

Este documento describe lo que **ya existe y está desplegado**. El razonamiento y las alternativas están en [`DISENO_PANEL_ADMIN.md`](DISENO_PANEL_ADMIN.md); el dimensionamiento de flujos, en [`CAPACIDAD_FLUJOS.md`](CAPACIDAD_FLUJOS.md).

## 1. Qué es

Un servicio web **de solo lectura** para quien administra la red. Vive en la VM `server` (servicio `panel-red`), usa solo la biblioteca estándar de Python y no cambia nada en la red: no instala ni borra flujos, no reinicia servicios, no edita políticas. Mide ≈ 16 MB de memoria (límite de 128 MB y 25 % de CPU).

| Estado | Detalle |
|---|---|
| Desplegado | `panel_instalar.yml` + `panel_config.yml`, última versión `44520f3` (24-nov, 04:29 UTC) |
| Pruebas | 214 en `R2/panel/tests`, 479 en `R2/policy/tests` (incluye las del `/estado` del vigilante y las de capacidad) |
| Probado en la red real | sí, ver la sección 7 |

## 2. Acceso y seguridad

- **Solo el rol `superadmin` entra** (y editará, cuando haya algo que editar), sin contraseña ni token. El panel mira de qué IP llega la conexión, la busca en ONOS (`/hosts`) y en las sesiones del mapper, y solo deja pasar si el rol de esa sesión es el de administración. Se rechaza a un equipo sin sesión, a una IP que tengan dos MAC, a una sesión vencida y a cualquier otro rol. Se ignoran las cabeceras `X-Forwarded-For`.
- **Hoy ese rol no existe en la red** (requisito P11 de `DISENO_EXCEPCIONES_POR_SESION.md`), así que el panel escucha **solo en `127.0.0.1` del server**. La única vía es un túnel SSH de **emergencia y solo lectura**; editar exigirá siempre el rol, también por el túnel.
- Todo método distinto de GET da 405 y queda registrado. Cada acceso, permitido o denegado, se registra con la IP y el motivo.
- Cabeceras de seguridad (CSP, `no-store`, `nosniff`); la página nunca construye HTML con datos ajenos (un usuario del portal puede escribir cualquier cosa: se muestra como texto).
- Límite conocido: la identidad por IP vale lo que valga la protección contra suplantación de IP de la red de datos. La defensa real debe ser que R2 abra el puerto del panel solo a ese rol.

## 3. Pestañas

| Pestaña | Qué muestra | De dónde sale |
|---|---|---|
| **Resumen** | Switches disponibles, nodos de ONOS, mapper, vigilantes, sesiones y los **avisos activos** | recolector (ronda de 10 s) |
| **Sesiones** | Usuario, rol, VLAN, MAC, switch y puerto, método y vencimiento | `GET /sessions` del mapper |
| **Eventos** | Bitácora del mapper con filtros (tipo, usuario, MAC, texto); el panel guarda su propia copia en SQLite | `GET /events` del mapper |
| **Vigilante** | Topología dibujada (núcleo arriba, accesos abajo, borde a un lado; posiciones fijas), uso de cada cable (`arbol`, `reserva`, `caido`) e historial de recálculos | `GET :8098/estado` del vigilante que manda |
| **Flujos** | Flujos de un switch con filtros, explicación de cada regla y **comandos de `ovs-ofctl` equivalentes** | `GET /flows/{switch}` del maestro, bajo demanda |
| **Políticas** | Roles, matriz rol/servicio, servicios, portal, ascenso, cuarentena, las reglas de cada tabla de rol y la **comparación política contra instalado** | foto de configuración |
| **Configuración** | Archivos fijos (políticas, plan de subredes, **hosts estáticos**, unidad del vigilante, prioridades) con sus copias y divergencias | foto de configuración |
| **Vistas de ONOS** | Qué dice cada nodo de cada switch (el detector de vistas desiguales) | `GET /devices` de cada nodo |

### Avisos del resumen

`VISTA_DESIGUAL`, `NODO_ONOS_SIN_RESPUESTA`, `NODO_SIN_SWITCHES`, `VIGILANTE_SIN_ACTIVO`, `VIGILANTE_SIN_RESPUESTA`, `VIGILANTE_ATENCION`, `MAPPER_NO_LISTO`, `FUENTE_CAIDA`, `CABLE_CAIDO`, `RED_PARTIDA` y, con `--maestros-validos ctrl1,ctrl2`, **`MAESTRO_INVALIDO`** (manda un nodo sin RADIUS, LDAP ni mapper, es decir, sin 802.1X), `MAESTRO_DUPLICADO` y `SIN_MAESTRO`.

### Flujos: cómo funciona

- **Carga sobre ONOS:** se lee **solo del nodo maestro** del switch, bajo demanda, con caché de 10 s (mínimo 3 s entre lecturas), una lectura a la vez en todo el panel y 16 s de espera. Si ONOS falla, se enseña la lectura anterior marcada como antigua.
- **Filtros:** tabla, prioridad (valor o rango), quién la instaló, estado, puerto de entrada, MAC, EtherType, VLAN, IP origen y destino (como `ovs-ofctl`: contenida en; y «cubre»: qué reglas contienen esa IP), protocolo, puertos TCP y UDP, acción, solo con paquetes, texto, orden y paginación.
- **Generador de comandos:** arma el `ovs-ofctl -O OpenFlow13 dump-flows br0 "…"` con esos filtros. La prioridad, las acciones y los paquetes se aplican con **solo `grep -E`**, porque ovs-ofctl no los filtra; un rango de prioridad se convierte en una expresión como `priority=[4-9][0-9]{4}[, ]`. **Cada comando trae la explicación de cada una de sus partes.** También arma el `ofproto/trace` de un paquete concreto y dice dónde ejecutarlo (por ejemplo `ssh -p 5812 ubuntu@10.20.11.14` para sw-acc-1).
- **Seguridad del generador:** los valores de los filtros solo admiten caracteres inofensivos; nada que el shell interprete puede llegar a un comando que se copie.
- **Cookie por app:** se deduce del id del flujo (`id >> 48`); así `cookie=0xb2…/0xffff…` selecciona los flujos de una app.

### Políticas y Configuración: cómo funcionan

El panel no lee YAML. `R2/panel/exportar_config.py` corre en el borde (con PyYAML, por Ansible), interpreta la política con el **mismo código del mapper** (`policy_tables`) y deja un JSON; `panel_config.yml` recoge las copias del repo y de cada controlador, lo genera y lo deja en `/etc/panel-red/config.json`. El panel lo relee solo al cambiar. **No se actualiza solo:** hay que repetir `panel_config.yml` tras cambiar algo, y la pantalla dice de hace cuánto es la foto. Los archivos se comparan por **contenido lógico** (un JSON con otra sangría es igual; la unidad del vigilante se compara sin `--respaldo-de`, que es lo que distingue al respaldo).

## 4. Carga sobre la red

Una ronda de 10 s hace **10 peticiones**: 3 a ONOS (`/devices`), 1 a `/hosts`, 3 al mapper, 2 al `/salud` de los vigilantes y 1 al `/estado` del que manda. Las pantallas leen una caché: pedir páginas no consulta la red (hay una prueba que lo comprueba). Los flujos y la comparación son bajo demanda.

## 5. Archivos

| Archivo | Para qué |
|---|---|
| `R2/panel/datos.py` | lógica pura: sesiones, historial de eventos (SQLite), vistas de ONOS, avisos, **decisión de acceso** |
| `R2/panel/panel.py` | recolector, servidor HTTP y API |
| `R2/panel/flujos.py` | flujos: normalizar, explicar, filtrar, comparar, generador de comandos y lector con caché |
| `R2/panel/configuracion.py`, `exportar_config.py` | foto de la configuración estática |
| `R2/panel/static/` | página (`index.html`, `app.js`, `style.css`) |
| `R2/policy/estado.py` | el `/estado` del vigilante (topología, uso de cada cable e historial) |
| `R1/deploy/topologia14/panel_instalar.yml`, `panel_config.yml` | despliegue y foto de configuración |

## 6. Operación

```bash
PLAYBOOK=panel_instalar.yml ./lanzar.sh      # instala o actualiza el panel (reinicia solo el panel)
PLAYBOOK=panel_config.yml ./lanzar.sh        # regenera la foto de configuración (solo lee)
PLAYBOOK=panel_instalar.yml ./lanzar.sh -e accion=quitar   # lo quita (el historial se conserva)
```

Para verlo desde tu PC, el túnel de emergencia (solo lectura) y luego `http://localhost:8480`:

```
ssh -L 8480:127.0.0.1:8480 -p 5823 ubuntu@10.20.11.14
```

Registro del servicio: `journalctl -u panel-red` en el `server`.

## 7. Qué se ha comprobado en la red real

| Fecha (UTC) | Qué | Resultado |
|---|---|---|
| 24-nov 02:23 | Despliegue de la v1 | 14 MB, 0 avisos, ninguna lectura caída |
| 24-nov 03:03 | Parón de ONOS en ctrl-1 | el aviso `NODO_ONOS_SIN_RESPUESTA` saltó solo |
| 24-nov 03:13 | Autenticación 802.1X de alumno01 con ctrl-2 de maestro | `admitido` en el panel, VLAN 20 |
| 24-nov 03:30 | Lectura de flujos | 58 flujos de acc-1 desde ctrl-2 en 44 ms |
| 24-nov 03:50 | Comparación política contra instalado | **0 diferencias** en los 5 switches (39, 39, 40, 7 y 7 flujos) |
| 24-nov 04:10 | Prueba A: comando generado frente al switch real | mismos 6 flujos y mismos contadores |
| 24-nov 04:29 | Despliegue de los comandos con `grep` y su explicación | el panel real sirve el comando y 6 partes explicadas |

### Lo que enseñó la red real (y las pruebas con datos simulados no habían visto)

1. ONOS devuelve los criterios del selector **en otro orden** que el instalado: el panel los ordena siempre igual.
2. ONOS devuelve un DROP como instrucción **`NOACTION`**, no como lista vacía.
3. Lo desplegado por el `68` va **compacto** y el repo con sangría: los archivos se comparan por contenido.
4. Los flujos del vigilante se instalan con la app **`r2.proactivo`**, y los **flujos de sesión** del mapper con `org.onosproject.rest` (REST sin app). Por eso filtrar por «campus.r2» no los muestra, y la comparación de la política los excluye a propósito.
5. El mando de los switches estuvo en **ctrl-3**, que no sirve 802.1X: eso motivó el aviso `MAESTRO_INVALIDO`.

## 8. Decisiones tomadas

- **Dónde vive:** en la VM `server`, no en un controlador (comparte destino con ONOS) ni en el borde (RAM justa). Medido: 1,5 GB disponibles.
- **Acceso:** solo `superadmin`; sin token ni contraseña; el túnel local solo lee.
- **Historial de eventos:** el panel guarda el suyo (SQLite, hasta 20 000), además de lo que tiene el mapper.
- **Comandos:** solo `grep` y con explicación, para poder defenderlos.
- **Política:** se interpreta con el código del mapper, no con una copia.

## 9. Pendiente (lo que NO está hecho)

| Pendiente | Nota |
|---|---|
| El rol **`superadmin`** en la red real (P11) | sin él, el panel solo se ve por el túnel |
| **Pruebas en curso:** las trazas de `ofproto/trace` frente al switch (prueba B) y la **caída de un enlace** con el panel abierto (`80_caida_cable.yml`) | no se han hecho |
| `policy.local.yaml` solo existe en la carpeta de ctrl-1 | falta comprobar de dónde lee cada réplica del mapper (riesgo de políticas distintas) |
| `/events` entre réplicas del mapper | falta comprobar que dos réplicas den lo mismo |
| **Reparación manual** de una vista de ONOS desigual (fase 3) y la edición | solo diseño |
| Configuración JSON de las apps de ONOS (DHCP, gateway-arp, medidores) | no incluida en la foto |
| Series de tiempo (Prometheus y Grafana de R5) y un `/metrics` del vigilante | propuesta |
| TLS y registro de auditoría duradero | no incluidos |
| Flujos por puerto de acceso: 4 de los 6 son deducidos, no medidos con varios puertos | ver `CAPACIDAD_FLUJOS.md` |
