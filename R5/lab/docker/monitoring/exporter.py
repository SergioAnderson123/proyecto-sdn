"""Exportador de métricas de R5 para Prometheus (puerto 9108).

Lee tres fuentes y las expone en formato Prometheus:
  - ONOS REST: flujos del appId de R5 (bloqueos), espejo del Core, dispositivos conectados
  - eve-alerts.json de Suricata: contador de alertas por firma/severidad
  - state.json del integrador M09: tiempos lectura->201 y alerta->201 por IP bloqueada
  - stats.log de Suricata: paquetes capturados y descartes
"""
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

ONOS = os.environ["ONOS_URL"]
AUTH = (os.environ["ONOS_USER"], os.environ["ONOS_PASS"])
APP_ID = os.getenv("APP_ID", "org.campus.r5")
EVE = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")
STATE = os.getenv("STATE_PATH", "/var/lib/integrador/state.json")
STATS = "/var/log/suricata/stats.log"
CORE = os.getenv("CORE_DEVICE", "of:0000000000000001")
SWITCH = {"of:0000000000000001": "s1-core", "of:0000000000000002": "s2-borde", "of:0000000000000003": "s3-borde"}

alertas = {}          # (firma, sid, severidad) -> contador
recientes = deque()   # (hora de lectura, firma) de la última ventana, para las gráficas por minuto
ignoradas = 0
cursor = {"inode": None, "offset": 0}
lock = threading.Lock()


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def linea(nombre, valor, **etiquetas):
    et = ",".join(f'{k}="{esc(v)}"' for k, v in etiquetas.items())
    return f"{nombre}{{{et}}} {valor}" if et else f"{nombre} {valor}"


def leer_alertas():
    """Cuenta las alertas nuevas de eve-alerts.json (tolera rotación o truncado)."""
    global ignoradas
    try:
        s = os.stat(EVE)
    except OSError:
        return
    with lock:
        if cursor["inode"] != s.st_ino or s.st_size < cursor["offset"]:
            cursor["inode"], cursor["offset"] = s.st_ino, 0
        with open(EVE, "rb") as f:
            f.seek(cursor["offset"])
            for raw in iter(f.readline, b""):
                if not raw.endswith(b"\n"):
                    break
                cursor["offset"] = f.tell()
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                a = e.get("alert", {})
                sev = a.get("severity", 99)
                if sev > 2:
                    ignoradas += 1
                    continue
                k = (a.get("signature", "?"), a.get("signature_id", 0), sev)
                alertas[k] = alertas.get(k, 0) + 1
                recientes.append((time.time(), k[0]))


def onos(ruta):
    try:
        return requests.get(f"{ONOS}{ruta}", auth=AUTH, timeout=2).json()
    except Exception:
        return {}


def suricata_stats():
    try:
        txt = open(STATS, errors="replace").read()[-300000:]
    except OSError:
        return None, None
    p = re.findall(r"capture.kernel_packets\s*\|\s*Total\s*\|\s*(\d+)", txt)
    d = re.findall(r"capture.kernel_drops\s*\|\s*Total\s*\|\s*(\d+)", txt)
    return (int(p[-1]) if p else None), (int(d[-1]) if d else 0)


def epoch(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except (ValueError, TypeError):
        return None


def metricas():
    leer_alertas()
    out = []
    add = out.append

    add("# HELP r5_bloqueos_activos IPs bloqueadas por R5 en ONOS")
    add("# TYPE r5_bloqueos_activos gauge")
    flujos = onos(f"/flows/application/{APP_ID}").get("flows", [])
    bloq = []
    for f in flujos:
        ip = next((c["ip"].split("/")[0] for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"), None)
        if ip:
            bloq.append((ip, SWITCH.get(f["deviceId"], f["deviceId"]), f))
    add(linea("r5_bloqueos_activos", len(bloq)))
    add("# TYPE r5_bloqueo_paquetes gauge")
    add("# TYPE r5_bloqueo_vida_segundos gauge")
    for ip, sw, f in bloq:
        add(linea("r5_bloqueo_paquetes", f.get("packets", 0), ip=ip, switch=sw))
        add(linea("r5_bloqueo_vida_segundos", f.get("life", 0), ip=ip, switch=sw))
    add("# TYPE r5_bloqueo_paquetes_total counter")
    add(linea("r5_bloqueo_paquetes_total", sum(f.get("packets", 0) for _, _, f in bloq)))

    core = onos(f"/flows/{CORE}").get("flows", [])
    add("# TYPE r5_espejo_paquetes_total counter")
    for f in core:
        if f["priority"] == 30000:
            cr = {c["type"]: c.get("ip") or c.get("port") for c in f["selector"]["criteria"]}
            d = "entrante" if "IN_PORT" in cr else ("interno" if cr.get("IPV4_DST") == cr.get("IPV4_SRC") else "saliente")
            add(linea("r5_espejo_paquetes_total", f.get("packets", 0), direccion=d))
    add("# TYPE r5_flujos_switch gauge")
    for dev in onos("/devices").get("devices", []):
        n = len(onos(f"/flows/{dev['id']}").get("flows", []))
        add(linea("r5_flujos_switch", n, switch=SWITCH.get(dev["id"], dev["id"])))
    devs = onos("/devices").get("devices", [])
    add("# TYPE r5_dispositivos_conectados gauge")
    add(linea("r5_dispositivos_conectados", sum(1 for d in devs if d.get("available"))))

    add("# TYPE r5_alertas_total counter")
    with lock:
        for (firma, sid, sev), n in alertas.items():
            add(linea("r5_alertas_total", n, firma=firma, sid=sid, severidad=sev))
        corte = time.time() - 60
        while recientes and recientes[0][0] < corte:
            recientes.popleft()
        por_firma = {}
        for _, firma in recientes:
            por_firma[firma] = por_firma.get(firma, 0) + 1
        add("# HELP r5_alertas_ultimo_minuto Alertas accionables de los últimos 60 s por firma")
        add("# TYPE r5_alertas_ultimo_minuto gauge")
        for firma, n in por_firma.items():
            add(linea("r5_alertas_ultimo_minuto", n, firma=firma))
        add("# TYPE r5_alertas_ignoradas_total counter")
        add(linea("r5_alertas_ignoradas_total", ignoradas))

    add("# TYPE r5_bloqueo_lectura_a_201_ms gauge")
    add("# TYPE r5_bloqueo_alerta_a_201_ms gauge")
    try:
        st = json.load(open(STATE)).get("bloqueadas", {})
    except (OSError, ValueError):
        st = {}
    for ip, v in st.items():
        if v.get("t_lectura") and v.get("t_201"):
            add(linea("r5_bloqueo_lectura_a_201_ms", round((v["t_201"] - v["t_lectura"]) * 1000, 1), ip=ip))
            t = epoch(v.get("t_alerta"))
            if t:
                add(linea("r5_bloqueo_alerta_a_201_ms", round((v["t_201"] - t) * 1000, 1), ip=ip))

    p, d = suricata_stats()
    if p is not None:
        add("# TYPE r5_suricata_paquetes_capturados_total counter")
        add(linea("r5_suricata_paquetes_capturados_total", p))
        add("# TYPE r5_suricata_descartes_total counter")
        add(linea("r5_suricata_descartes_total", d))
    return "\n".join(out) + "\n"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo = metricas().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9108), H).serve_forever()
