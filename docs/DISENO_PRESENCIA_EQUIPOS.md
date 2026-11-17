# Presencia de equipos: liberar la IP y el rol cuando un equipo ya no está

**Estado (17-nov-2026):** escrito y probado sin red (11 pruebas Java de la lógica, 11 del Policy Mapper, 2 del generador, 297 de R2). **Probado en vivo (87):** equipo muerto → la IP desaparece de ONOS a los 35 s del ACK (sondeo lento 20 s + 15 s); equipo vivo → la IP se conserva. **Falta en vivo:** cierre de la sesión en el Policy Mapper (`88_prueba_sesion_ausente.yml`, modos `api` y `puerto`) y el logoff (caso 1).

## El problema
Una sesión 802.1X es **permanente** (flujo 40001 sin plazo): solo la cierran el logoff del agente, la revocación de R5, el portal o un reinicio del Policy Mapper. Y una concesión DHCP dura `leaseSeg` (900 s). Cuando un equipo desaparece sin avisar, quedan su rol abierto en ese puerto (alguien que conecte otro equipo con su MAC heredaría el rol), su IP ocupada y su regla /32 puesta.

## Qué cubre cada caso

| Caso | Quién lo detecta | Qué hace | Tiempo |
|---|---|---|---|
| **1. Apagado normal / logoff** | El agente 802.1X avisa a R1 (`/sessions/logout`) y R1 borra el flujo 40001 | La app DHCP ve la baja del flujo (`FlowRuleListener`) y **sospecha**: sondea por ARP; si no contesta, libera la IP | ≈ 15 s |
| **2. El puerto del switch pierde el enlace** | ONOS: evento de puerto. Policy Mapper: lee `isEnabled` de los puertos de las sesiones | App DHCP: libera la concesión al instante. Policy Mapper: cierra la sesión (2 lecturas seguidas) | App: inmediato · sesión: 5 a 10 s |
| **3. Apagado forzado o enlace caído por el lado del host** | Nadie avisa (en VNRT bajar la NIC de un host no baja el puerto del switch) | **Sondeo ARP** de la app DHCP; sin respuesta a 3 intentos libera la IP y avisa al Policy Mapper (`/sessions/ausente`), que cierra la sesión y borra el flujo 40001 | Sondeo lento (120 s) + 15 s |

## Cómo funciona el sondeo (app `campus-dhcp`)
- `Presencia` (clase pura): por cada concesión confirmada lleva un estado. **Modo lento**: cada `sondeoLentoSeg` empieza un ciclo; un equipo vivo contesta al primer ARP y el ciclo termina. **Modo rápido**: `intentos` (3) sondeos cada `intervaloSeg` (5 s); sin respuesta tras el último → AUSENTE (15 s). Una **sospecha** (baja del flujo de sesión) abre el modo rápido; si el equipo contesta, **conserva su IP** (un equipo revocado por R5 sigue vivo y no debe perder su concesión: se podría dar a otro y duplicarla).
- El sondeo es un **ARP unicast** "¿quién tiene la IP X?" con emisor el gateway (IP .254 y MAC común), enviado por el puerto del equipo. El núcleo de cualquier sistema contesta aunque tenga firewall.
- La respuesta (dirigida a la MAC del gateway) la captura un flujo **propio de la app** en la tabla 60 de cada switch con ámbito (prioridad 310, `arp_op=2`, destino = MAC del gateway → controlador). Sobrevive a `allowExtraneousRules=false` (lo posee la app) y ONOS lo repone al reconectar el switch. Registrado en `R5/deploy/prioridades.yaml`.
- Al dar por ausente: libera la concesión (`removeIpFromHost`: ONOS pierde la IP y el vigilante retira la /32) y hace `POST /sessions/ausente` al Policy Mapper con la clave `X-Presencia-Token`.

## Policy Mapper (R1)
- `POST /sessions/ausente` (clave propia `PRESENCIA_TOKEN`, **distinta** de `AGENTE_TOKEN`: quien controla un host no puede cerrar la sesión de otro). Cierra la sesión de cualquier método (802.1X, portal, ascenso: del ascenso no se restaura el rol base de quien ya no está), borra su flujo 40001 y deja el evento `logout` en la bitácora. Sin lista negra.
- `_vigilar_puertos_de_sesion`: cada 5 s lee `GET /devices/{id}/ports`; si el puerto de una sesión dice `isEnabled=false` dos lecturas seguidas, la cierra. Lo que ONOS no lista se ignora (un switch que no contesta no cierra sesiones).

## Límites (a propósito)
- El sondeo ARP solo cubre equipos **con concesión DHCP**. Un equipo con IP fija (h1 y h2 hoy) solo se cubre por el caso 2 (puerto sin enlace) y por el logoff del agente; si se apaga de golpe con el puerto arriba, su sesión queda hasta que otro evento la cierre. Pasar h1/h2 a DHCP lo resuelve.
- Las concesiones viven en la memoria del nodo de ONOS que atiende el switch; si cambia el maestro, el sondeo de esas concesiones también se reinicia (los clientes renuevan solos).
- El sondeo no distingue un equipo apagado de uno con el cable suelto: en ambos la IP y el rol se liberan y, al volver, hay que autenticar de nuevo (el agente ya hace logoff y logon al recuperar el enlace).

## Parámetros (`campusdhcp.presencia` en la configuración, `generar_config_dhcp.py`)
`intervaloSeg` 5, `intentos` 3, `sondeoLentoSeg` 120 (0 = solo tras una sospecha), `policyMapperUrl`, `policyMapperToken` (la clave sale del entorno al generar, nunca de un archivo del repositorio ni de la línea de comandos). Sin `gwMac` no hay sondeo; sin URL o clave solo se libera la IP.

## Cómo probarlo en vivo
1. `PLAYBOOK=86_presencia.yml ./lanzar.sh` (solo lee) y luego `-e aplicar=true -e confirmar=true`: `git pull` en node-ctrl-1, clave `PRESENCIA_TOKEN` en el `.env`, **recrea el Policy Mapper (los usuarios autentican de nuevo)** y reinstala la app con el sondeo.
2. Para acortar la prueba: `PLAYBOOK=86_presencia.yml ./lanzar.sh --tags config -e confirmar=true -e sondeo_lento=20`.
3. `PLAYBOOK=87_prueba_presencia.yml ./lanzar.sh -e prueba=muerta -e sondeo_lento=20` (la IP debe desaparecer de ONOS a los ≈ 35 s) y `-e prueba=viva` (debe quedarse).
4. Caso 1 (logoff): botón Desconectar del agente en h1 y mirar el log de la app. Caso 2: bajar la NIC del puerto **en el switch** (`ip link set <nic> down` en la VM del switch), como en `80_caida_cable.yml`.
5. Al terminar, `95_diagnostico_lazos.yml` y volver a `-e sondeo_lento=120`.
