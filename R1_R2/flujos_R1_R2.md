# Flujos R1 y R2: control de acceso y restricción de recursos

Diagramas en [`diagramas/`](diagramas/). Cada bloque es una etapa del mismo flujo de admisión.

```
Bloque 1 (802.1X + EAP-TLS) -> Bloque 2 (rol en OpenLDAP) -> Bloque 3 (decisión Zero Trust + sesión) -> Bloque 4 (reglas OpenFlow multitabla)
```

## Actores

| Actor | Módulo | Rol |
|---|---|---|
| Usuario final | `wpa_supplicant` | Suplicante 802.1X |
| OVS de borde | M10 | Enforcement, OpenFlow 1.3, pipeline Tabla 0 -> 10/20/30 |
| ONOS | M01 / M06 / M07 | App AAA (relay EAP), Flow Manager (REST 8181) |
| FreeRADIUS | M02 | AAA: `rlm_eap`, `rlm_ldap`, `rlm_rest` |
| OpenLDAP | M03 | `ou=users`, `ou=groups`, `ou=devices` |
| Policy Mapper | M05 | FastAPI, PDP/PEP |
| Session Manager | `session_manager.py` | Estado de sesiones en memoria |

## Bloque 1A: estado inicial e inicio de 802.1X ([Bloque1A.png](diagramas/Bloque1A.png))

Estado inicial (Zero Trust en L2), flujos proactivos en Tabla 0:

| Match | Acción | Prioridad |
|---|---|---|
| `dl_type=0x888e` (EAPOL) | CONTROLLER | 50000 |
| LLDP (0x88cc), BDDP (0x8942), ARP (0x0806) | CONTROLLER | 40000 |
| Todo IP/DHCP | DROP | 40000 |

Secuencia:
1. Usuario -> OVS: EAPOL-Start.
2. OVS -> ONOS: Packet-In (TCP 6653).
3. ONOS -> Usuario: EAP-Request/Identity (Packet-Out).
4. Usuario -> OVS: EAP-Response/Identity; OVS -> ONOS: Packet-In.
5. ONOS registra el contexto en la tabla interna `aaa-users`: user-name, switch DPID, puerto físico, MAC origen.
6. ONOS -> FreeRADIUS: RADIUS Access-Request (UDP 1812) con User-Name, NAS-IP-Address = IP del controlador, EAP-Message y Message-Authenticator. No lleva Calling-Station-Id ni NAS-Port-Id.

## Bloque 1B: handshake EAP-TLS ([Bloque1B.png](diagramas/Bloque1B.png))

1. FreeRADIUS -> ONOS -> Usuario: EAP-TLS Start.
2. Usuario -> FreeRADIUS (vía ONOS): Client Hello.
3. FreeRADIUS -> Usuario: Server Hello + `server.pem` + Certificate Request. El suplicante valida `server.pem` contra `ca.pem` local (anti-RADIUS falso).
4. Usuario -> FreeRADIUS: `client.pem` + ClientKeyExchange + CertVerify firmado con `client.key`.
5. FreeRADIUS valida `client.pem` contra `ca.pem`, verifica la firma y extrae `TLS-Client-Cert-Common-Name = user@example.org`.
6. ChangeCipherSpec + Finished en ambos sentidos: túnel TLS establecido.
7. **FreeRADIUS retiene el Access-Accept** y pasa a la fase post-auth (Bloque 2).

## Bloque 2: autorización post-auth en OpenLDAP ([Bloque2.png](diagramas/Bloque2.png))

1. `rlm_ldap`: búsqueda en `ou=users,dc=example,dc=org`, filtro `(cn=user@example.org)`. Devuelve el DN y el atributo `description` (MAC autorizada, device binding).
2. Búsqueda en `ou=groups`, filtro `(&(cn=docente)(member=uid=user...))`. Confirma la pertenencia al grupo (`groupOfNames`).
3. FreeRADIUS consolida rol = `docente` y MAC. Si algo falla, REJECT.

## Bloque 3: decisión Zero Trust y control de concurrencia ([Bloque3.png](diagramas/Bloque3.png))

1. FreeRADIUS (`rlm_rest`) -> Policy Mapper: `POST /webhook` con headers `X-Rol-LDAP`, `X-MAC-LDAP` y body con User-Name y NAS-IP.
2. Policy Mapper -> ONOS AAA API: `GET /onos/aaa/app/users` (Basic Auth) para obtener el contexto físico: MAC, DPID, puerto.
3. Policy Mapper -> Session Manager: `obtener_sesion(mac)`.
   - **Caso A: ya existe sesión** (duplicación o spoofing): HTTP 409; FreeRADIUS emite Access-Reject y no se tocan flujos.
   - **Caso B: no existe**: validación PDP en memoria.
     - Device binding: MAC de LDAP == MAC física de ONOS (coincidencia exacta).
     - Resolución de rol: `docente` -> VLAN 10.
     - `registrar_sesion(username, mac, dpid, puerto, vlan)`.

## Bloque 4: enforcement con pipeline multitabla ([Bloque4.png](diagramas/Bloque4.png))

1. Policy Mapper -> ONOS: `POST /onos/v1/flows/of:...0001` con Tabla 0, `match(in_port=1, MAC)`, `action(push_vlan=10, goto_table=10)`.
2. ONOS -> OVS: `OFPT_FLOW_MOD` (TCP 6653); el switch confirma el flujo en TCAM; ONOS responde 201 con `flow_id`.
3. Policy Mapper -> Session Manager: `asociar_flow_id(mac, flow_id)`; luego HTTP 200 a FreeRADIUS.
4. FreeRADIUS emite Access-Accept (EAP-Success); ONOS entrega EAPOL-Success al usuario y se dispara DHCP.
5. Plano de datos: DHCP Discover -> Tabla 0 (prio 40001: push_vlan 10, goto_table 10) -> Tabla 10 (prio 30000: `udp_dst=67` -> NORMAL).

## Tablas del pipeline ([tablas.png](diagramas/tablas.png))

| Tabla | Contenido |
|---|---|
| 0 (admisión, device binding, etiquetado) | EAPOL -> CONTROLLER; flujo por usuario (`in_port` + `eth_src`) con `push_vlan` + `goto_table`; table-miss -> DROP |
| 10 (Docente, VLAN 10) | Base: `udp_dst=53`, ARP, `udp_dst=67` -> NORMAL. Específicas: `ip_dst=192.168.10.0/24` (servidores de docencia) -> NORMAL; `ip_dst=0.0.0.0/0` -> NORMAL; prio 0 -> DROP (aislamiento intra-VLAN) |
| 20 (Alumno, VLAN 20) | prio 0 DROP (bloqueo a VLAN 10 y servidores críticos); ARP/DHCP/DNS -> NORMAL; `ip_dst=192.168.20.0/24` (laboratorios) -> NORMAL |
| 30 (IoT, VLAN 30) | Solo servidores autorizados (ver fallback MAB) |

## Fallback MAB nativo ([PolicyMapperMAB.png](diagramas/PolicyMapperMAB.png), [fallbackF.png](diagramas/fallbackF.png))

Para equipos sin suplicante 802.1X (IoT, VMs bridged):

1. DHCP Discover o ARP Request llega a OVS. Tabla 0: prio 50000 (EAPOL) no coincide; prio 40001 sin match para esa MAC; prio 20000 (trampa MAB) coincide -> Packet-In a ONOS.
2. `MabInterceptor` (Java PacketProcessor) llama `aaaService.isAuthenticating(mac)`. Si devuelve false, es un dispositivo mudo en 802.1X.
3. `MabInterceptor` -> Policy Mapper: `POST /api/mab-request` con `{mac, deviceId, port}`.
4. Policy Mapper consulta Session Manager y hace `ldapsearch` en `ou=devices` con `(&(objectClass=device)(macAddress=...))`.
   - **Caso A: IoT registrado**: rol `iot` -> VLAN 30 -> `goto_table: 30`; flujo prio 40001 vía ONOS REST; `registrar_sesion`; PacketOut reinyecta el DHCP/ARP retenido.
   - **Caso B: no registrado**: default deny, HTTP 403; `context.block()`, no se instala regla.
5. Árbol de decisión MAB: ¿MAC en LDAP? Sí (IoT): ¿usuario en sesión en este Port+DPID? Si no, DROP + timeout (dispositivo sin ancla física); si sí, ¿rol del usuario es Docente o Alumno? Si sí, VLAN 30; si no, DROP. No (VM bridged): ¿usuario en sesión en este Port+DPID? Si no, DROP + timeout (VM huérfana); si sí, ¿cuota N > 0? Sí: misma VLAN del usuario y N = N-1; no: DROP (cuota agotada).

## Árbol de decisión 802.1X ([PolicyMapperEAPTLS.png](diagramas/PolicyMapperEAPTLS.png))

¿MAC coincide con OpenLDAP? No -> DROP + timeout (usuario externo). Sí -> ¿existe otra sesión con esa MAC? Sí -> DROP + timeout (alerta MAC spoofing / blacklist). No -> ¿ubicación válida (User + Port + DPID)? Sí -> flujo VLAN-rol prio 40001 + registrar en SessionManager; no -> DROP (puerto físico no autorizado). Ciclo de cierre asíncrono: Logout / Link-Down -> remover usuario y flujos de TCAM.

## Protección del plano de control ([seguridadmeters.png](diagramas/seguridadmeters.png))

| Meter | Tráfico | Tasa | Burst | Exceso |
|---|---|---|---|---|
| EAPOL | 0x888E | 20 pps | 20 | DROP |
| MAB | ARP / DHCP | 10 pps | 10 | DROP |

Blacklist en hardware: prio 45000, DROP. Cuando falla la validación, Policy Mapper inyecta la regla DROP con `idle_timeout = 300 s` (`OFPT_FLOW_MOD`).

## Desconexión y revocación ([Desauth.png](diagramas/Desauth.png))

1. Detección: **A** (software) EAPOL-Logoff -> Packet-In a ONOS; **B** (hardware) `OFPT_PORT_STATUS` con `OFPPS_LINK_DOWN`.
2. ONOS -> FreeRADIUS: Accounting-Request (UDP 1813), `Acct-Status-Type = Stop`, `Calling-Station-Id`.
3. FreeRADIUS (`rlm_rest`) -> Policy Mapper: `POST /webhook/stop` con `{mac, status: stop}`.
4. Policy Mapper -> Session Manager: `obtener_sesion(mac)` devuelve `flow_id`, `dpid` y `vlan`.
5. Policy Mapper -> ONOS: `DELETE /onos/v1/flows/{dpid}/{flow_id}`. ONOS -> OVS: `OFPFC_DELETE_STRICT` (Tabla 0, `in_port`, `eth_src`). Respuesta 204.
6. `remover_sesion(mac)`: se liberan los índices `_sessions_by_mac` y `_mac_by_username`. HTTP 200 a FreeRADIUS y Accounting-Response a ONOS. Cualquier reconexión vuelve al Bloque 1.

## Código principal

Ver [Codigo_principal.png](diagramas/Codigo_principal.png) y [`codigo/policy_mapper.py`](codigo/policy_mapper.py). Pendiente de subir: Session Manager y MabInterceptor (ver `docs/pendientes-entrega-1.md`).
