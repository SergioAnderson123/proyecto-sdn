# Mapa de módulos del proyecto

Una vista de todo lo que hay en el repositorio, agrupado por capa y por quién depende de quién, con los huecos y las incoherencias detectadas.
Pruebas automáticas al escribir este mapa: R2/policy 270 (267 + 3 del generador), R5 111, topologia14 17; el resto de módulos tiene sus propias pruebas (`test_agente.py`, `test_mab_vm.py`, `R1/deploy/test_slice.py`, `R2/portal_cautivo/tests`).

## 1. Vista por capas

| Capa | Módulo | Dónde | Qué hace | Estado |
|---|---|---|---|---|
| **Identidad (R1)** | Agente 802.1X | `R1/agente_802_1x/` | wpa_supplicant + API de sesiones (`/sessions/logout`, token `AGENTE_TOKEN`) | en vivo |
| | FreeRADIUS + OpenLDAP | `R1/radius/`, `R1/shared/ldap/` | EAP-TLS, roles por LDAP; en la slice RADIUS corre en Docker sin puertos publicados, IP 10.50.0.250 (playbook 73) | en vivo |
| | Policy Mapper | `R1/shared/policy_mapper/`, `R1/policy_mapper/` | traduce rol→flujos de ONOS; solo añade flujos (se limpia con `limpiar_r2.py`) | en vivo |
| **Restricción (R2)** | Tablas por rol | `R2/policy/policy_tables.py`, `policy.yaml`, `portal_auth.py` | tablas de rol, cuarentena, portal, DHCP, prioridades R2 | en vivo |
| | Reenvío proactivo (L2) | `R2/policy/flujos_proactivos.py`, `caminos.py`, `plan_arboles.py`, `puertos.py`, `instalar_proactivo.py`, `explicar_ruta.py`, `vista_previa.py` | tabla 60: unicast por MAC, ARP por árbol, sin STP ni tormentas | en vivo |
| | **Vigilante** | `R2/policy/vigilante.py`, `recalculo.py`, `hosts_fijos.py`, `consultar_hosts.py`, `onos_multi.py` | observa enlaces/hosts de ONOS, recalcula el árbol y repara flujos (borrar antes de añadir, alta rápida, hosts fijos, standby). Modo L2 en vivo; **modo capa 3** (`--subredes`, `--nucleo`) validado solo en simulación | L2 en vivo, L3 offline |
| | **Capa 3 por subredes** | `R2/policy/capa3.py`, `ovs_formato.py`, `viabilidad_capa3.py`, `spike_capa3.py`, `benchmark_capa3.py`, `planes/` | un prefijo por switch de acceso, gateway virtual, rutas por árbol, /32 de entrega con MAC y TTL | datos de V1–V4 en vivo; vigilante L3 offline |
| | **App ONOS "Gateway ARP" (Java)** | `R2/onos-app/gateway-arp/` + `R2/policy/generar_config_gateway_arp.py` | instala como flujos propios de ONOS la respuesta ARP del gateway (el REST de ONOS 2.7 no la admite); permite `allowExtraneousRules=false` | escrita, **sin compilar ni probar** |
| | Portal cautivo, panel, MAB | `R2/portal_cautivo/`, `R2/panel_accesos/`, `R2/mab_vm/`, `R2/fallback/` | invitados, vista de accesos, MAB de respaldo | portal sin desplegar en la slice |
| | Pruebas de aceptación | `R2/tests/`, `R2/mininet/` | barrido, métricas y topología Mininet | laboratorio |
| **Perímetro (R5)** | Suricata + integrador | `R5/suricata/`, `R5/codigo/`, `R5/cli/` | alertas → bloqueo en ONOS (prioridad R5) | en vivo en laboratorio, la slice espera ajustes |
| | Laboratorio y pruebas | `R5/lab/`, `R5/pruebas/`, `R5/tests/` | Docker, evidencias, espejo, TCAM | — |
| | Despliegue de sensores | `R5/deploy/` | `prioridades.yaml` (mapa único de prioridades), topología de ejemplo | pensado para otra topología (ver huecos) |
| **Operación** | Despliegue de la slice de 14 VM | `R1/deploy/topologia14/` | playbooks `00`–`99` (ver §2), `lanzar.sh`, inventario, plantillas, `TOPOLOGIA.md`, `CLUSTER.md` | en vivo |
| | Despliegue previo | `R1/deploy/{controller,hosts,switches,slice.py}` | versión de 6 VM y utilidades | histórico |
| **Documentos** | `docs/` | ver §3 | diseños, resultados y protocolos | — |

## 2. Playbooks de `topologia14` por fase

| Fase | Archivos | Para qué |
|---|---|---|
| Preparación | `00_comprobar`, `01_limpiar_clasico`, `02_disco`, `05_recursos`, `10_docker` | comprobar, limpiar OVS clásico, disco, recursos, Docker |
| Plano de control | `20_controller`, `30_switches`, `40_cluster`, `42_servicios`, `45_maestro`, `50_sensores` | ONOS, OVS, clúster, servicios, nodo maestro, sensores |
| R2 | `60_proactivo`, `65_vigilante`, `70_fase_e`, `71_consulta_hosts`, `72_suplicante`, `73_radius_red_docker`, `74_token_agente`, `75_server_ip_persistente` | reenvío proactivo, vigilante, tablas R2, consulta de hosts, autenticación de prueba |
| Seguridad de la operación | `90_estado`, `95_diagnostico_lazos`, `96_estres_difusion`, `96_prueba_rstp`, `99_freno_emergencia` | estado, detector de lazos, estrés, freno |
| Capa 3 | `97_viabilidad_capa3` (+ pendiente `76_gateway_arp`) | viabilidad en vivo; el 76 instalará la app Java |

## 3. Documentos

| Tema | Archivo |
|---|---|
| Diseño proactivo (L2) | `DISENO_FLUJOS_PROACTIVOS.md`, `Reenvio_proactivo_sin_STP.pptx` |
| Fase E (tráfico IP de R2 por la tabla 60) | `DISENO_FASE_E.md`, `RESULTADOS_FASE_E.md` |
| Capa 3 y direccionamiento | `DISENO_CAPA3.md`, `PLAN_DIRECCIONAMIENTO.md` |
| Pruebas | `PROTOCOLO_PRUEBA_AUTENTICACION.md`, `VALIDAR_H1_VNRT.md`, `VALIDAR_PORTAL_DHCP_VNRT.md` |
| Plataforma | `DESPLIEGUE_UBUNTU.md`, `CERTIFICADOS_Y_ESCALABILIDAD.md`, `topologia-v4-14vms.drawio` |
| Equipo/curso | `avance-01-roles.md`, `plan-avance-R5.md`, `pendientes-entrega-1.md`, `entrega-1/` |

## 4. Dependencias entre módulos (de abajo hacia arriba)

```
caminos / plan_arboles / puertos  →  flujos_proactivos  →  instalar_proactivo
                      └→ capa3 → ovs_formato / viabilidad_capa3
recalculo (usa flujos_proactivos y, en modo L3, capa3) → vigilante (+ hosts_fijos, onos_multi)
planes/*.json → capa3.cargar_plan → generar_config_gateway_arp → app ONOS Gateway ARP
policy_tables / policy.yaml  →  Policy Mapper (R1)  ←  agente 802.1X + FreeRADIUS/LDAP
```
La app Java no importa nada de Python: solo lee la configuración que genera `generar_config_gateway_arp.py` desde el mismo plan que usa el vigilante. Esa es la costura entre ambos: **si cambia el plan, hay que regenerar la configuración y reiniciar el vigilante**.

## 5. Huecos e incoherencias (ordenados por urgencia)

1. **App Java sin compilar.** Los nombres de la API de ONOS están escritos de memoria; falta `construir.ps1` con Docker Desktop abierto y probarla con `allowExtraneousRules=false` (playbook `76`, aún por escribir).
2. **Vigilante en modo capa 3 solo en simulación.** No se ha probado en vivo con la app Java ni con los hosts fijos con IP.
3. **Plan de direccionamiento sin aplicar.** h1/h2 siguen en 10.0.0.201/.202; el plan A los pasa a 10.0.1.x / 10.0.2.x y exige DHCP por subred (hoy solo hay uno).
4. **R5 vs. slice.** `R5/deploy/README` supone un enlace de subida por acceso y control en banda; `topologia14` tiene dos enlaces y gestión fuera de banda. Además: `HOME_NET` debe ser el agregado (10.0.0.0/16), bloquear por `IPV4_SRC`, revocar por (switch, puerto, MAC) y el espejo `NORMAL` debe pasar a la tabla 60.
5. **Servicios sin probar en la slice:** portal cautivo/invitados, DHCP-drop (host-jhon inaccesible), reinicio del servidor para confirmar la IP persistente; vecino FAILED del servidor sin explicar; sin redundancia del servidor (bond).
6. **Decisión abierta:** `--copiar-arp` (que ONOS aprenda hosts con la petición ARP del gateway) y ruta final de instalación del ARP estático (app Java frente a `ovs-ofctl` con `allowExtraneousRules=true`).
7. **Orden del repositorio:**
   - El vigilante y la capa 3 viven en `R2/policy/`, que el README describe como "se montan en el Policy Mapper"; en realidad esa carpeta mezcla módulos del Policy Mapper (`policy_tables`, `portal_auth`, `policy.yaml`) con herramientas de operación del slice (vigilante, capa 3, proactivo). Una reorganización razonable sería `R2/policy/` (Policy Mapper) y `R2/red/` (proactivo, vigilante, capa 3), pero **no se ha movido nada**: los playbooks, los `PYTHONPATH` de Docker y 270 pruebas importan esas rutas.
   - `R2/policy/edge_ports.txt`, `R2/policy/hosts.json`, `docs/hosts.json` son volcados de ONOS sin seguimiento: conviene decidir si son fixtures (a `tests/`) o se ignoran en `.gitignore`.
   - `R1/deploy/{controller,hosts,switches,slice.py}` es la versión de 6 VM; marcar como histórico.
   - El README de raíz no menciona vigilante, capa 3, planes ni la app Java (se actualiza con este mapa).
   - `informe_lab6/` y los PDF de `docs/` no son del proyecto de código; no deben entrar en el mismo commit.
