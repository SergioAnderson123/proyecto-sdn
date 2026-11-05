"""Genera grafana/dashboards/r5-perimetral.json (tablero de Grafana de R5).

Uso: python gen_dashboard.py   (reescribe el JSON; Grafana lo recarga solo cada 10 s)
Todas las consultas son PromQL sobre las métricas de exporter.py.
"""
import json
import os

DS = {"type": "prometheus", "uid": "prometheus"}
_id = 0


def nid():
    global _id
    _id += 1
    return _id


def q(expr, legend="", ref="A", instant=False, table=False):
    d = {"datasource": DS, "expr": expr, "refId": ref, "legendFormat": legend}
    if instant:
        d["instant"] = True
        d["range"] = False
    if table:
        d["format"] = "table"
    return d


def pos(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def stat(titulo, expr, x, y, w=4, h=4, unit="short", steps=None, desc="", decimals=None):
    steps = steps or [{"color": "blue", "value": None}]
    f = {"defaults": {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []}
    if decimals is not None:
        f["defaults"]["decimals"] = decimals
    return {"id": nid(), "type": "stat", "title": titulo, "description": desc, "datasource": DS, "gridPos": pos(x, y, w, h),
            "targets": [q(expr, instant=True)], "fieldConfig": f,
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "background",
                        "graphMode": "area", "textMode": "value", "justifyMode": "center"}}


def serie(titulo, targets, x, y, w, h, unit="short", stack=False, desc=""):
    custom = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 18 if not stack else 55, "showPoints": "never",
              "lineInterpolation": "smooth", "spanNulls": True}
    if stack:
        custom["stacking"] = {"mode": "normal", "group": "A"}
    return {"id": nid(), "type": "timeseries", "title": titulo, "description": desc, "datasource": DS, "gridPos": pos(x, y, w, h),
            "targets": targets, "fieldConfig": {"defaults": {"unit": unit, "custom": custom, "min": 0}, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def tabla_bloqueos(x, y, w, h):
    return {"id": nid(), "type": "table", "title": "Bloqueos activos en ONOS", "datasource": DS, "gridPos": pos(x, y, w, h),
            "description": "Un flujo DROP por IP de origen (appId org.campus.r5, prioridad 42000).",
            "targets": [q("r5_bloqueo_paquetes", ref="A", instant=True, table=True),
                        q("r5_bloqueo_vida_segundos", ref="B", instant=True, table=True)],
            "transformations": [
                {"id": "joinByField", "options": {"byField": "ip", "mode": "outer"}},
                {"id": "organize", "options": {
                    "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "job": True, "job 1": True, "job 2": True,
                                      "instance": True, "instance 1": True, "instance 2": True, "__name__": True,
                                      "__name__ 1": True, "__name__ 2": True, "switch 2": True},
                    "renameByName": {"ip": "IP bloqueada", "switch 1": "Switch", "Value #A": "Paquetes descartados", "Value #B": "Vida (s)"},
                    "indexByName": {"ip": 0, "switch 1": 1, "Value #A": 2, "Value #B": 3}}}],
            "fieldConfig": {"defaults": {"custom": {"align": "left", "cellOptions": {"type": "auto"}}},
                            "overrides": [{"matcher": {"id": "byName", "options": "IP bloqueada"},
                                           "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                          {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]},
            "options": {"showHeader": True, "footer": {"show": False}}}


def barras(titulo, expr, x, y, w, h, unit="ms", desc=""):
    return {"id": nid(), "type": "bargauge", "title": titulo, "description": desc, "datasource": DS, "gridPos": pos(x, y, w, h),
            "targets": [q(expr, "{{ip}}", instant=True)],
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "color": {"mode": "thresholds"},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                                                       {"color": "yellow", "value": 150},
                                                                                       {"color": "red", "value": 500}]}}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


panels = [
    stat("Bloqueos activos", "r5_bloqueos_activos", 0, 0, steps=[{"color": "green", "value": None}, {"color": "red", "value": 1}],
         desc="IPs con un DROP vigente instalado por R5."),
    stat("Alertas", "sum(r5_alertas_total) or vector(0)", 4, 0, decimals=0,
         steps=[{"color": "blue", "value": None}], desc="Alertas accionables de Suricata (severidad 1 o 2) desde el arranque del exportador."),
    stat("Ruido (sev 3)", "r5_alertas_ignoradas_total", 8, 0,
         steps=[{"color": "text", "value": None}], desc="Alertas de severidad 3 (checksum, LLDP): el integrador no actúa sobre ellas."),
    stat("Bloqueados", "r5_bloqueo_paquetes_total", 12, 0, steps=[{"color": "orange", "value": None}],
         desc="Paquetes que murieron en un DROP de R5 (contadores de ONOS)."),
    stat("Descartes IDS", "r5_suricata_descartes_total", 16, 0,
         steps=[{"color": "green", "value": None}, {"color": "red", "value": 1}],
         desc="kernel_drops: paquetes que Suricata no alcanzó a inspeccionar."),
    stat("Switches", "r5_dispositivos_conectados", 20, 0,
         steps=[{"color": "red", "value": None}, {"color": "green", "value": 3}], desc="Dispositivos OpenFlow disponibles en ONOS (se esperan 3)."),

    serie("Alertas por firma (ventana de 60 s)",
          [q("sum by (firma) (r5_alertas_ultimo_minuto)", "{{firma}}")], 0, 4, 12, 8, stack=True,
          desc="Qué reglas de Suricata disparan y con qué intensidad."),
    serie("Tráfico descartado por los bloqueos (paquetes/s)",
          [q("sum(rate(r5_bloqueo_paquetes_total[10s]))", "descartados")], 12, 4, 12, 8, unit="pps",
          desc="Lo que el atacante sigue enviando y muere en el switch."),

    tabla_bloqueos(0, 12, 14, 8),
    barras("Reacción: alerta → bloqueo aceptado por ONOS", "r5_bloqueo_alerta_a_201_ms", 14, 12, 10, 4,
           desc="Desde que Suricata ve el paquete hasta que ONOS acepta el DROP."),
    barras("Del integrador a ONOS (lectura → 201)", "r5_bloqueo_lectura_a_201_ms", 14, 16, 10, 4,
           desc="Solo el tramo del integrador: decisión + POST REST."),

    serie("Espejo hacia Suricata (paquetes/s)",
          [q("rate(r5_espejo_paquetes_total[10s])", "{{direccion}}")], 0, 20, 8, 7, unit="pps",
          desc="Tráfico Norte-Sur copiado al puerto del IDS."),
    serie("Captura de Suricata (paquetes/s)",
          [q("rate(r5_suricata_paquetes_capturados_total[10s])", "capturados", ref="A"),
           q("rate(r5_suricata_descartes_total[10s])", "descartados", ref="B")], 8, 20, 8, 7, unit="pps"),
    serie("Entradas de flujo por switch (TCAM)",
          [q("r5_flujos_switch", "{{switch}}")], 16, 20, 8, 7, desc="Una entrada por IP bloqueada más las reglas base."),
]

dashboard = {
    "uid": "r5-perimetral", "title": "R5 · Seguridad perimetral", "tags": ["R5", "SDN", "Suricata", "ONOS"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "2s", "editable": True,
    "time": {"from": "now-15m", "to": "now"}, "timepicker": {"refresh_intervals": ["1s", "2s", "5s", "10s", "30s"]},
    "graphTooltip": 1, "panels": panels,
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True,
                              "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]},
}

salida = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grafana", "dashboards", "r5-perimetral.json")
with open(salida, "w", encoding="utf8", newline="\n") as f:
    json.dump(dashboard, f, indent=2, ensure_ascii=False)
print("escrito", salida, len(panels), "paneles")
