# Nota: validación en vivo del Policy Mapper HA (22-nov-2026)

Para quien mantiene `R1/mapper-ha/` (commit `c0058c2`). Probado en la slice `topologia14` con h1 (profesor01) y h2 (alumno01) autenticados, `95_diagnostico_lazos` limpio antes y sin tormenta. Todo lo de abajo son lecturas y pruebas de la operación normal; no se tocó configuración ni código.

## Lo que se probó y salió bien

| Prueba | Resultado |
|---|---|
| Salud y sesiones en las 3 réplicas y por el gateway (`192.168.201.254:5000`) | Idénticas; el gateway alterna ctrl-1, ctrl-2, ctrl-3 |
| Parar el mapper de ctrl-1 | El gateway sigue sirviendo las mismas sesiones |
| Reiniciar el mapper de ctrl-1 | `[HA] recovered 2 sessions; no startup purge`; las 3 réplicas vuelven a la misma revisión |
| Presencia con ctrl-1 caído | `POST /sessions/ausente` (por el gateway) lo atendió ctrl-2 y cerró la sesión de h2 ~60 s después de bajar su interfaz; las 3 réplicas lo vieron |
| Login nuevo con ctrl-1 caído | h2 subió su interfaz, el agente reautenticó solo y ctrl-3 lo aprobó a los 4 s (`APROBADO ... VLAN 20`); luego las 3 réplicas mostraban las 2 sesiones |
| etcd | Compactación periódica de 1 h, cuota 128 MB, base de datos de 737 kB, sin errores; la revisión crece ~4 por segundo (cerrojo + mantenimiento) y no es problema con esa compactación |

## Cosas a tener en cuenta (no son fallos)

- El `flow_id` de una sesión lo calcula ONOS a partir del contenido del flujo: reautenticar la misma MAC en el mismo puerto da el **mismo** `flow_id`. No sirve para distinguir una sesión nueva de una confirmada.
- Con la interfaz del host abajo menos de ~60 s la sesión no se cierra (la presencia sondea por ARP); el login posterior se registra como «ya tiene esta misma sesión activa... se confirma sin reprocesar».
- El ping al servidor 10.0.0.10 no responde porque el servidor está apagado; el camino se mide con `nc` (como hace `80_caida_cable.yml`).

## Pendientes y preguntas

1. **`/events` (el panel de accesos) sigue en memoria por réplica.** `eventos.py` es una cola en proceso, así que el panel solo ve lo que vio la réplica que respondió. ¿Se guarda en etcd o se agrega al leer?
2. **Dos copias del mapper.** `R1/mapper-ha/app/policy_mapper.py` es una copia de `R1/shared/policy_mapper/policy_mapper.py` con ~58 líneas de diferencia. Los cambios de uno (por ejemplo `POST /sessions/ausente`) hay que llevarlos al otro. ¿Cuál es la versión oficial?
3. **El gateway `.254:5000` es un único punto de fallo, y se acepta (decisión del 22-nov).** Corre en `switch-borde`: la IP `.254` es la gestión del borde, que además es la salida con NAT de todas las VM, así que si cae el borde se pierde mucho más que el proxy y duplicar solo el proxy no mejoraría el conjunto por la topología. No hay que confundirlo con el gateway virtual de los hosts (`10.0.x.254`, MAC `02:00:5E:00:00:FE`), que está en el plano de datos y lo contesta la app `gateway-arp` de ONOS en cada switch. Si algún día hiciera falta, la alternativa barata es que RADIUS, DHCP y el fallback MAB lleven las tres direcciones de réplica y reintenten ellos.
4. **R2 MAB** sigue en ctrl-1 (ya anotado en el README).
5. **Sin probar:** perder el quórum de etcd (dos nodos a la vez), apagar un ONOS, y medir la latencia del cerrojo en un login (cada petición toma el cerrojo, espera hasta 8 s y puede devolver 503).

## Cómo repetirlo

Desde `switch-borde`: `curl http://192.168.201.<n>:15002/health/ha` en cada nodo y `curl http://192.168.201.254:5000/sessions`; los contenedores son `mapper-ha-prod` y `mapper-etcd-prod`. Para el login, bajar y subir `ens4` en h2 (más de 60 s abajo para que la presencia cierre la sesión) y mirar `sudo docker logs -t mapper-ha-prod` en las réplicas.

## Respuesta de implementación a esta revisión

Se mantiene intacta la evidencia en vivo de arriba, que corresponde a la versión
anterior. La corrección posterior establece:

1. Fuente única en `R1/shared/policy_mapper/`, con HA opcional; `R1/mapper-ha/app/`
   enlaza a ella. Las políticas y el portal tienen su fuente única en `R2/policy/`.
   El generador `R1/mapper-ha/build_bundle.py` empaqueta las fuentes reales y evita
   depender del soporte de enlaces de Windows.
2. Los últimos 200 eventos se guardan en etcd y se consultan sin cerrojo de escritura.
   Sin quórum `/events` responde 503. Las sesiones no se borran ni se reinicializan.
   El historial previo en memoria requiere exportación antes de reiniciar.
3. Se conserva la decisión del gateway único. R2 MAB en ctrl-1 continúa siendo
   una limitación explícita y separada; no se declara resuelta por el HA del mapper.
4. Se agregan medidas `wait_ms` y `hold_ms` por transacción y pruebas simuladas
   de quórum perdido y failover ONOS. No son una medición de login real ni una
   prueba de apagar ONOS en la slice.
5. Regresión local: 402 pruebas aprobadas; 10 de integración etcd omitidas por
   requerir un entorno aislado. Se incluyen presencia, logout, revocación,
   ascenso por portal, políticas y vigilante. La prueba que abre un servidor HTTP
   local se ejecutó fuera del sandbox porque éste bloqueaba `bind`.

Antes de declarar validada esta nueva versión en producción, actualizar las
réplicas gradualmente con rollback disponible, comprobar `/sessions` y `/events`
en las tres, y repetir presencia y login. Registrar por separado la prueba de
caída ONOS, pérdida de quórum y latencia del login. No extrapolar resultados de
pruebas unitarias a la red real.

## Despliegue VNRT posterior (22-nov-2026)

Se desplegó el commit `76d0f7f` gradualmente en ctrl-1, ctrl-2 y ctrl-3. El
mapper acepta `LDAP_HOST` y `LDAP_PORT`; `portal_auth` usa esas variables para
construir su URL LDAP. En VNRT se configuró `192.168.201.1:1389`, porque ctrl-3
no tiene un contenedor LDAP local. Los LDAP consultados en ctrl-1 y ctrl-2
coincidieron; ctrl-3 pudo consultar la instancia central.

Tras cada reinicio se compararon las MAC de sesión y el historial de eventos
antes y después. Las tres réplicas quedaron `ready`, con las mismas 2 sesiones y
4 eventos (huellas de respuesta idénticas). Se probó `/api/mab-request` con una
MAC sintética y puerto 9999: las tres réplicas llegaron al servicio R2 y
respondieron `403 PORT_NOT_ALLOWED`; no se instaló ningún flujo ni cambió la
tabla de sesiones. Los contenedores previos quedaron detenidos como rollback.

La prueba final detuvo solo el mapper de ctrl-1: el gateway atendió desde
ctrl-3 y mantuvo las 2 sesiones y 4 eventos; ctrl-1 volvió a `ready`. Quince
solicitudes MAB sintéticas a través del gateway devolvieron HTTP 403, con
mediana de 189.91 ms y p95 de rango más cercano de 310.90 ms. No equivalen a una
autenticación EAP/RADIUS real.

La prueba real de caída ONOS se ejecutó el 22-nov-2026. Se detuvo solo ONOS en
ctrl-1; ctrl-2 y ctrl-3 siguieron `READY`, cada uno conservó los 5/5 switches y
el gateway respondió desde ctrl-2 con las 2 sesiones y 4 eventos. Después de
reiniciar ctrl-1, los tres miembros volvieron a `READY`, cada uno vio 5/5
switches, y se restituyó ctrl-1 como maestro de los cinco dispositivos. No se
detuvieron Atomix ni miembros etcd.

Se midió una reautenticación real EAP-TLS de h1-profesor: 2384 ms desde `logon`
hasta `EAP-SUCCESS` y el puerto autorizado. En los diez minutos consultados,
RADIUS registró 7 Access-Request con atributos EAP y cero Access-Reject. Las
tres réplicas del mapper conservaron las mismas 2 sesiones y 4 eventos. Es una
sola muestra de reautenticación, no un percentil ni una medición de primer
acceso; se necesitan más muestras para caracterizar latencia.

Siguen como límites explícitos: el gateway de gestión es único por decisión del
grupo y R2 MAB continúa como una instancia en ctrl-1, aunque es alcanzable desde
las tres réplicas del mapper. La pérdida de quórum se comprobó en etcd 3.6.6,
en tres contenedores temporales conectados solo a una red Docker `internal` y
sin puertos publicados: con un miembro caído las otras dos réplicas confirmaron
escrituras; con dos caídos la escritura no se confirmó; al recuperar el segundo,
se restablecieron el quórum y la salud de los tres miembros. Los commits del
ensayo tardaron aproximadamente 7–11 ms; no son una medida de latencia del
mapper. Se retiraron los contenedores, la red y sus datos temporales. No se
detuvo ni cambió ningún miembro ni dato de etcd de producción.

No se ha probado en vivo la respuesta HTTP 503 del mapper mientras pierde
quórum; esa conducta tiene cobertura simulada en `test_review.py`. La muestra de
2384 ms es una sola reautenticación EAP-TLS; hacen falta más muestras para
percentiles. El gateway único y R2 MAB en ctrl-1 siguen siendo limitaciones de
arquitectura pendientes.

## Estado de los seis puntos de revisión

| Punto | Estado al 22-nov-2026 | Qué queda |
|---|---|---|
| 1. Copia duplicada del mapper | Resuelto en el repo: `R1/shared/policy_mapper/` es la fuente oficial; `app/` enlaza a ella. | Ninguna acción de producción. |
| 2. Eventos locales por réplica | Resuelto y desplegado: últimos 200 en etcd; `/events` no cae a memoria local y devuelve 503 si no hay quórum. | Los eventos anteriores a la actualización no se recuperan solos. |
| 3. Quórum, latencia y 503 | Quórum y respuestas HTTP del mapper completo probados con etcd real en una red aislada; métricas de espera/retención disponibles. | No se interrumpió el quórum de producción. La medición EAP de 2384 ms es una muestra; faltan muestras para p50/p95. |
| 4. Gateway y R2 MAB | Gateway único aceptado por decisión del grupo. R2 MAB HA está preparado como overlay opcional, con importador no destructivo de sesiones. | R2 sigue desplegado solo en ctrl-1; migrar su snapshot y desplegar ctrl-2/3 requiere acceso al VNRT y ventana coordinada. La unicidad del gateway requiere decisión/topología, no un cambio local al mapper. |
| 5. Presencia y cierre de sesiones | Resuelto y probado con ctrl-1 mapper detenido: presencia llegó a otra réplica y cerró h2. | Ninguna conocida para ese escenario probado. |
| 6. DHCP, portal y vigilante | La regresión automatizada cubre contratos de presencia, logout, revocación, ascenso y políticas; el despliegue preservó sesiones/eventos. | Falta una prueba conjunta en vivo de esos consumidores tras failover, sin generar tráfico de descubrimiento adicional. |

El acceso desde la Mac se realiza por SSH a `10.20.11.14`, puertos
`5801`, `5802` y `5803`; las consultas a `192.168.201.x` se hacen desde dentro
del slice. Los timeouts anteriores al intentar llegar directamente a esas IP
no demostraban una falla de VPN. Se verificó el acceso a los tres controladores:
las tres réplicas estaban `ready`, en la misma revisión y con las mismas dos
sesiones.

## Validación HTTP integrada y reparación de R2 (22-nov-2026)

Se ejecutó una copia del **mapper completo desplegado** en ctrl-3, con tres
contenedores etcd 3.6.6 temporales, en una red Docker `internal`, sin puertos
publicados, sin credenciales productivas y con mantenimiento ONOS deshabilitado.
El estado fue sintético y usó un prefijo `/sdn/mapper/test/...`. No se conectó
el ensayo a switches ni al etcd de producción.

- Tres miembros y un miembro detenido: `/sessions` y `/events` devolvieron 200.
- Dos miembros detenidos: `/sessions`, `/events`, `/health/ha` y un POST a
  `/sessions/ausente` devolvieron 503, aproximadamente en 2,01–2,07 segundos.
- Al recuperar un miembro: se recuperó HTTP 200 con la misma sesión y evento
  sintéticos. La comparación de las sesiones productivas antes y después
  confirmó que las dos permanecieron intactas.
- Se retiraron exclusivamente los cuatro contenedores y la red temporales.

Evidencia: [resultado JSON](evidencias/mapper-http-quorum-20261122.json).
Esto cierra la prueba integrada del comportamiento HTTP ante pérdida de quórum;
no representa una caída provocada del etcd productivo ni mide latencia EAP.

También se detectó y corrigió la configuración del contenedor R2 existente:
las variables estaban concatenadas dentro de `R1_URL` con caracteres literales
`\n`. Se recreó únicamente ese contenedor con variables independientes y su
mismo volumen. Se guardaron el contenedor anterior y un respaldo protegido en
el servidor. El estado conservó cero hijos y dos marcas de revocación. Una
solicitud usando la MAC del padre autenticado devolvió `403 CHILD_MAC_IS_PARENT`,
confirmando que R2 reconoce al padre; esta comprobación no instala un flujo de
VM. Esto **no equivale a desplegar R2 HA**, que sigue pendiente.

La regresión local se repitió: 401 pruebas aprobaron en el sandbox y la única
prueba que abre un servidor HTTP local aprobó fuera de él (402 en total).
