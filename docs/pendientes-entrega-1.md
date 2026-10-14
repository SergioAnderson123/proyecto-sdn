# Pendientes y coherencias antes de la Primera Entrega

Cierre en Paideia: miércoles 14-oct-2026, 14:59. Un solo PDF autocontenido por grupo, prefijo `G04_`, sin enlaces a documentos en línea.

## Checklist de entrega (ver `docs/entrega-1/enunciado_primera_entrega.png`)

- [ ] Arquitectura de la solución
- [ ] Diseño de módulos con evaluación/análisis
- [ ] Evaluación cuantitativa de al menos algunos módulos
- [ ] Código de R1 y R2 + código relevante que interactúe con R3/R4/R5
- [ ] Video de la demo del HLD de R1 y R2
- [ ] Ficha de autoevaluación, indicando en qué parte del documento está la evidencia
- [ ] Presentación del parcial (PPT/PPTX/PDF)

## Incoherencias a resolver

1. **IdP y fallback.** SEM-05/06/07 hablan de Keycloak/OIDC y portal cautivo (I03, I05); los diagramas usan OpenLDAP (`rlm_ldap`) y MAB nativo en Java. Unificar y actualizar el catálogo de módulos M01-M10. Módulos nuevos: Session Manager, MabInterceptor, app AAA de ONOS.
2. **VLANs.** Diagramas: 10/20/30 por rol. Arquitectura: 100-400 por pabellón. Explicar que son dimensiones distintas.
3. **Tabla única de prioridades** (propuesta):

| Prioridad | Regla |
|---|---|
| 50000 | EAPOL -> controller (borde) y DROP de R5 |
| 45000 | Blacklist MAB |
| 40001 | Flujo de usuario |
| 40000 | Base (LLDP/BDDP/ARP) |
| 30000 | Reglas base en Tabla 10 y espejo en el Core |
| 20000 | Trampa MAB |
| 0 | Table-miss DROP |

   En el Bloque 1A, el DROP de IP/DHCP en prio 40000 tapa la trampa MAB (20000); revisar.
4. **R2: Docente con `ip_dst=0.0.0.0/0 -> NORMAL`** abre también el Sistema administrativo, que en SEM-07 es DROP. Añadir DROP explícito de mayor prioridad hacia recursos privilegiados.
5. **R2: faltan tablas** para Administrativo, Híbrido y superusuario; y revocación por cambio de rol o expiración (CoA).
6. **R1: "ubicación válida = Port + DPID"** choca con la movilidad de alumnos; definir el alcance.
7. **Session Manager en memoria:** persistir o reconciliar contra ONOS al arrancar.
8. **R5: el espejado solo cubre tráfico entrante** (`ipv4_dst=10.0.0.0/24`, `in_port=uplink`), así que la rama "host interno -> externo (borde)" de `decidir()` no se dispara. Añadir espejo saliente o activar `stream.async-oneside` y quitar esa rama.
9. **R5: HOME_NET `10.0.0.0/24`** vs. subredes `192.168.10/20/30.0/24` de R1/R2: unificar.
10. **Autenticación a ONOS:** el diagrama dice JWT; el código usa Basic sobre HTTP 8181.
11. **Timeout de ONOS REST** es duro, no de inactividad; en R1 se menciona `idle_timeout = 300`. Aclarar.
12. **Nombre del log:** el diagrama dice `eve.json`; el código lee `eve-alerts.json`.
13. **`policy_mapper.py` vs. diagramas.** El código actual valida device binding y devuelve la VLAN a FreeRADIUS (atributos `Tunnel-*`), pero no tiene Session Manager (sin HTTP 409 por sesión duplicada), no instala flujos en ONOS ni expone `/api/mab-request` ni `/webhook/stop`. Marcarlo como implementado parcialmente, o completarlo. Además, las credenciales de ONOS están fijas en el código: pasarlas a variables de entorno.

## Rúbrica: qué falta medir

- **R1:** logins/s, latencia de autenticación desglosada, multiplataforma (emisión de certificados, equipos sin suplicante), niveles de roles.
- **R2:** entradas TCAM por switch (`ovs-ofctl dump-flows | wc -l` con 1, 10 y 100 usuarios), sesiones/s, recursos de ONOS (CPU/RAM).
- **R5:** tiempos de detección y mitigación, tasa de detección y fracción que pasa, escalabilidad por volumen (`tcpreplay`, `capture.kernel_drops`).
