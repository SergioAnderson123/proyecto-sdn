# Panel de accesos (R1 · R2 · R5)

Vista de estado en vivo y de solo lectura: **quién está autenticado, quién fue rechazado y por qué, cuántas entradas de TCAM hay por switch y tabla, qué hosts bloqueó el IDS y cuánto tráfico se copia a Suricata**. No es un portal de login: con 802.1X EAP-TLS la autenticación la hace el cliente (`wpa_supplicant`) con su certificado; para invitados y equipos sin 802.1X existe el [portal cautivo](../portal_cautivo/README.md). La tabla de sesiones muestra el **método** de cada una (802.1X o Portal).

```bash
python panel_accesos/server.py 8090      # desde R2; abrir http://localhost:8090  (?tema=light para el tema claro)
```

Lee dos fuentes (sin dependencias de Python): el Policy Mapper (`GET /sessions`, `GET /events`) y la API REST de ONOS (`/flows`, `/devices`). Variables: `ONOS_URL`, `ONOS_USER`, `ONOS_PASS`, `MAPPER_URL`, `P_R5`, `P_ESPEJO`.

| Tarjeta | Fuente |
|---|---|
| Sesiones activas | Policy Mapper `/sessions` |
| Actividad reciente (admitido, rechazado, conflicto, sesión cerrada, revocado por R5) | Policy Mapper `/events` (bitácora en memoria de 200 eventos) |
| TCAM por switch y tabla (y flujos pendientes) | ONOS `/flows` |
| Bloqueos del IDS | flujos de prioridad 42000 con `IPV4_SRC` |
| Espejo al IDS | contadores de los flujos de prioridad 41000 |
| Lista negra (R1) | flujos de prioridad 45000 con `ETH_SRC` |

## Diseño
Basado en [Tabler](https://github.com/tabler/tabler) (MIT): tarjetas, tablas, insignias y modo oscuro/claro. Se usa su CSS **v1.4.0** guardado en `static/tabler.min.css` (licencia en `static/LICENSE-tabler.txt`), de modo que el panel funciona sin internet; solo la tipografía Inter se pide a Google Fonts y tiene respaldo del sistema.
