# Estado del proyecto SDN al 17 de noviembre de 2026

Actualización de integración autorizada: ver `REGRESION_TOPOLOGIA14_2026-11-17.md`. Se conservan los avances más recientes de Daniel y se integran en `topologia14-deploy`. El arranque LDAP ahora conserva también el bootstrap original para instalaciones vacías.

Repositorio de ctrl-1: `/home/ubuntu/proyecto-sdn`, base `topologia14-deploy`, HEAD inicial `36abc69`. Por solicitud del usuario, los avances se agrupan en la nueva rama `avance-topologia14-fallback` para revisión del equipo, sin merges a la rama base.

## Resultado por pendiente

| Pendiente | Resultado comprobado |
|---|---|
| LDAP multimaestro ctrl-1/ctrl-2 | STARTTLS con certificados verificados, escritura en ambos sentidos y eliminación de una entrada temporal propagada. Ambos nodos conservan 16 entradas y el mismo hash de contenido. |
| Persistencia LDAP | Se corrigieron los montajes del primario y se recreó solo LDAP. El contenido se verificó después de la recreación. Se conservaron los directorios originales y una imagen del contenedor anterior. |
| RADIUS ctrl-2 | Misma imagen que ctrl-1, LDAP local y sin puertos UDP publicados. Configuración validada, sonda Status-Server aislada y EAP real de h2 aprobado en 4 s, con sesión Alumno/VLAN 20. |
| ctrl-3 | ONOS/Atomix y vigilante principal de capa 3 conservados. Se completó RADIUS local con LDAP remoto por STARTTLS hacia los dos proveedores existentes. EAP real de h2 verificado. No se instaló otro LDAP ni se modificó R5. Grafana/EveBox/logs siguen como monitoreo previsto en los documentos. |
| R1 | 141 pruebas pasan y 1 se omite, en ejecución aislada sin red. Autenticación real del alumno verificada. Se conservan docente/VLAN 10 y alumno/VLAN 20. Se cargó la misma feature AAA 2.7.0 de ctrl-1 en los secundarios, donde faltaba localmente. |
| MAB backend | Admisión positiva por API con VLAN 10 heredada, rechazo sin padre y rechazo de la MAC del padre como VM. 22 pruebas unitarias pasan. |
| MAB IoT registrado | Una entrada LDAP temporal propia obtuvo VLAN 30 y flujo confirmado ADDED. Se retiraron su sesión, flujos y entrada LDAP. |
| MAB automático | Variante 1.0.3 activa en los tres ONOS, 10 pruebas de seguridad pasan. Una única trama inyectada dentro del pipeline OVS recorrió ONOS → mapper → R2: HTTP 200, sesión ACTIVE, VLAN 10 heredada. Se retiraron el flujo de prueba y la sonda temporal; quedan cero hijos MAB y cero flujos de prueba. La trama física previa de h1 llegó al switch, pero se envió antes de corregir el punt ARP; no se presenta la inyección como una nueva emisión física de h1. |
| Jhon | La consola confirma `vm-atacante`, `ens3=10.20.11.203/24` por DHCP y SSH inactivo. No requiere inventar otra IP. El puerto 5824 no responde. Falta activar SSH desde la consola y habilitar el acceso de la clave dedicada. No se alteró ens4 ni el direccionamiento. |

## Qué se conservó

- Durante la implementación no se hizo push ni merge. La publicación posterior autorizada usa una rama nueva `avance-topologia14-fallback`, sin modificar por merge la rama base.
- No se borraron repositorios, volúmenes ni configuraciones del equipo.
- R5, los archivos/certificados de RADIUS y el OAR original `fallback-mab-1.0.0.oar` no tienen cambios según Git.
- No se reinició la topología, ONOS ni los switches. Se recreó únicamente LDAP primario para corregir persistencia y el fallback propio durante sus actualizaciones.
- La prueba de RADIUS2 cambió temporalmente solo el maestro del acceso de h2. Se restauró el maestro original `192.168.201.3` en el bloque de limpieza. Los otros switches no se cambiaron.
- Se conservó `r1-eapol-bcast` y el agente existente. No se instalaron escritorios en hosts.

## Cambios que explican los resultados

### LDAP

La CA incluida originalmente en la imagen LDAP venció el 15 de enero de 2026. Se renovó exclusivamente TLS de LDAP y se fijó confianza explícita entre los dos nodos, sin desactivar la validación de certificados. Los certificados EAP-TLS de usuarios/RADIUS no se regeneraron.

El Compose original montaba `/etc/openldap/slapd.d` y `/var/lib/openldap/openldap-data`, aunque el proceso usaba `/etc/ldap/slapd.d` y `/var/lib/ldap`. El primario ahora usa:

- `R1/shared/ldap/persistent-config` → `/etc/ldap/slapd.d`
- `R1/shared/ldap/persistent-data` → `/var/lib/ldap`
- `R1/shared/ldap/persistent-tls` → `/sdn/tls` de solo lectura

Estos directorios privados se excluyeron de Git. El arranque usa la configuración importada ya existente y conserva la IP Docker `10.50.0.3`.

El hash canónico verificado de las 16 entradas es `580fe0d8f30229da2533f84b925d80100acb7fd0757979b713c1990cd49afba6`. Se excluyen del hash únicamente metadatos de replicación/modificación que pueden diferir entre proveedores.

### RADIUS y AAA

Los tres RADIUS usan la imagen original `controller-radius`, ID `sha256:454501b4228e05fffa028a6b171b941d7dee84d2fbb2ff9dc33d5fd9d252d992`. No publican UDP 1812/1813 en el host: ONOS usa localmente `10.50.0.250`.

Ctrl-2 consulta su LDAP local. Ctrl-3 consulta `ldap://ldap:1389` y `ldap://ldap-ctrl2:1389` con STARTTLS y validación `demand`, usando únicamente los certificados públicos de confianza nuevos. Ambos resuelven `policy_mapper` hacia ctrl-1. Las sesiones del mapper siguen en memoria en ctrl-1; no se replicó el mapper.

La API de ONOS declaraba AAA activa globalmente, pero los bundles `org.opencord.aaa-api` y `org.opencord.aaa-app` faltaban en los secundarios. Se cargó su feature existente `aaa-app/2.7.0`, conservando el código y certificados originales. Las pruebas reales de RADIUS2 y RADIUS3 confirmaron solicitudes, challenges y respuestas, con h2 autenticado y VLAN 20.

### MAB

El adaptador R2 ahora usa `connectPoint` antes de `subscriberId`, reconoce `AUTHORIZED_STATE` y espera hasta 6 s la confirmación de ONOS. Se impide que la MAC del padre reemplace su flujo R1 por un flujo temporal de VM.

La variante ONOS 1.0.3 conserva el OAR original y añade fuentes revisables. Observa ARP antes del resolvedor como `advisor(2)` sin emitir, bloquear ni reenviar paquetes. Solo consulta en:

- `of:0000e6925d87354b/1`
- `of:0000766a0c84c741/1`

Esos son los mismos accesos habilitados para VM en el backend. La URL del mapper funciona desde los tres Controllers. La caché vence, la cola tiene límite 32 y el mapa tiene límite 4096. No se usan FLOOD, NORMAL o ALL.

Registrar una aplicación como ACTIVE por REST no aseguró que se instalara su feature. Se verificó y cargó explícitamente `fallback-mab/1.0.3` en cada nodo por el cliente local de Karaf. La copia de prueba no procesada en la carpeta hot-deploy del primario se conservó en `/tmp/sdn-mab-held`, para evitar duplicados. No se habilitaron agentes de gestión nuevos.

Se localizaron dos bloqueos adicionales mediante la prueba controlada:

- La regla original `r2.proactivo`, prioridad 40003, enviaba ARP directamente a tabla 60 y tapaba las copias al controlador de menor prioridad. Se conservaron todas las reglas originales y se añadieron solo dos reglas de acceso, tabla 0/prioridad 40004: `IN_PORT=1 + ARP → CONTROLLER + TABLE60`. No se aplican a enlaces entre switches. Su estado y las reglas originales están respaldados en `/home/ubuntu/sdn-identity/mab-ingress-state.json`. El helper permite comprobarlas o retirar exclusivamente sus IDs propios.
- El cliente Java intentaba una actualización HTTP/2 que el Uvicorn actual no admitía: devolvía 422 por cuerpo ausente. Una comparación controlada confirmó 422 con el cliente por defecto y el rechazo correcto `CHILD_MAC_IS_PARENT` con HTTP/1.1. La variante 1.0.3 fija HTTP/1.1 sin cambiar el mapper de Daniel.

Evidencia de integración a las 19:51 UTC: MAC `02:cd:00:00:00:11`, acceso `of:0000e6925d87354b/1`, HTTP 200, sesión ACTIVE/VLAN 10, flujo `50665496103719592`. Se eliminó exclusivamente ese flujo y el monitor liberó la sesión. El bundle temporal `pe.edu.sdn.validation.single-packet` fue desinstalado (ID 234). La sonda emitió una trama de 42 bytes por `TABLE`, con puerto lógico de entrada 1; no usa FLOOD, ALL o NORMAL y no configura interfaces.

Revisión final: tres nodos READY, cinco switches disponibles, dos reglas EAPOL conservadas y sesiones Docente 10/Alumno 20. Ambos LDAP mantienen las mismas 16 entradas y STARTTLS verificado. Los cuatro switches registraron un máximo de 0,8 pps por interfaz, sin indicios de tormenta en esa muestra.

## Respaldos principales

- Ctrl-1, antes del trabajo: `/home/ubuntu/sdn-backups/20261117T042453Z-pre-ldap`.
- Ctrl-1, antes de multimaestro: `/home/ubuntu/sdn-backups/20261117T044416Z-ldap-before-multimaster`.
- Ctrl-1, antes de persistencia: `/home/ubuntu/sdn-backups/20261117T173356Z-before-primary-persistence`.
- Imagen previa: `sdn-ldap-before-persistence:20261117173356`, cuya configuración se validó antes de recrear LDAP.
- Ctrl-1, después de persistencia: `/home/ubuntu/sdn-backups/20261117T173415Z-after-primary-persistence`.
- Ctrl-2: `/home/ubuntu/sdn-backups/20261117T044852Z-ldap-multimaster-node2`, además del respaldo previo a actualizar confianza TLS.

Los respaldos, seeds, configuraciones LDAP reales y archivos de imagen pueden contener material privado. Permanecen fuera de Git o en directorios ignorados. No publicar esos archivos.

## Operación del despliegue actual

En ctrl-1, para gestionar solo LDAP persistente:

```bash
cd ~/proyecto-sdn
docker compose -p controller -f R1/compose.yml up -d --no-deps ldap
```

Para gestionar solo fallback VM:

```bash
cd ~/proyecto-sdn
docker compose -p r2-mab-vnrt -f R2/mab_vm/compose.yml -f R1/deploy/controller/compose.mab-vnrt.yml up -d --no-deps r2_mab_vm
```

En ctrl-2, LDAP y RADIUS están definidos en `/home/ubuntu/sdn-identity/compose.yml` y `radius.yml`, proyecto `sdn-identity`. En ctrl-3, RADIUS está en `/home/ubuntu/sdn-identity/radius.yml`, mismo proyecto. Los archivos privados que montan deben conservarse.

No lanzar el stack clásico completo ni playbooks de limpieza para aplicar estos cambios al clúster: sus servicios ONOS clásicos pertenecen a otra fase del despliegue. No es necesario recrear todo.

## Comprobaciones de tormenta

Se midieron pasivamente los cuatro switches por sus contadores de interfaz, y los cinco por ONOS. En las muestras registradas no hubo señales de tormenta; las muestras tranquilas estuvieron entre 0,4 y 0,8 pps por interfaz, frente al umbral 2000 pps del playbook 95. No quedaron acciones de flujo NORMAL/FLOOD/ALL en la consulta.

No se ejecutaron pruebas de estrés ni el freno 99. El playbook exacto 95 requiere contraseña sudo y está diseñado para ejecutarse desde switch-borde. Se emplearon consultas pasivas equivalentes sin sudo. Las mediciones son evidencia puntual, no una garantía permanente.

## Acciones que requieren intervención del usuario

1. En la consola web de Jhon basta activar SSH con `sudo systemctl enable --now ssh`. Después la clave pública puede copiarse desde PowerShell, sin escribir un bloque largo en la consola web. Esta activación no pudo hacerse remotamente porque SSH está inactivo y el usuario no tiene que ejecutar comandos mientras está ausente.
2. Si 5824 sigue sin responder después de activar SSH, verificar el reenvío VNRT hacia `10.20.11.203:22` antes de cambiar direccionamiento. El acceso SSH de Jhon no se declara completado.
3. Como comprobación física adicional, cuando haya consola con sudo disponible puede repetirse una sola trama desde h1. La integración OVS/ONOS/mapper/R2 ya se verificó sin exigir otra intervención del usuario; la nueva emisión física completa todavía no está registrada.
