"""Panel en vivo del laboratorio R5 (http://localhost:8080).

Muestra, actualizado cada segundo: la narración del escenario en curso (la envía attacks.py),
las alertas de Suricata, los bloqueos que ONOS tiene instalados para el appId de R5 con sus
contadores de paquetes, los contadores del espejo y el log del integrador.
"""
import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

ONOS = os.environ["ONOS_URL"]
AUTH = (os.environ["ONOS_USER"], os.environ["ONOS_PASS"])
APP_ID = os.getenv("APP_ID", "org.campus.r5")
EVE = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")
INTEG_LOG = os.getenv("INTEG_LOG", "/var/log/r5/integrador.log")
STATE_PATH = os.getenv("STATE_PATH", "/var/lib/integrador/state.json")
NOMBRES = {"of:0000000000000001": "s1 Core", "of:0000000000000002": "s2 borde (hsrv)",
           "of:0000000000000003": "s3 borde (hint)", "of:0000a223f2c04547": "s1 Core", "of:00001a749039894a": "s2 borde (h1,h2)", "of:0000ca272d265744": "s3 borde (h3,h4)"}
CORE = os.getenv("CORE_DEVICE", "of:0000000000000001")
eventos, lock = [], threading.Lock()


def cola(ruta, n):
    try:
        with open(ruta, "rb") as f:
            f.seek(0, 2)
            tam = f.tell()
            f.seek(max(0, tam - 60000))
            return f.read().decode("utf8", "replace").splitlines()[-n:]
    except OSError:
        return []


def get(ruta):
    try:
        return requests.get(f"{ONOS}{ruta}", auth=AUTH, timeout=2).json()
    except Exception:
        return {}


def estado():
    alertas, ignoradas = [], 0
    for l in cola(EVE, 400)[::-1]:
        try:
            e = json.loads(l)
        except ValueError:
            continue
        a = e.get("alert", {})
        if a.get("severity", 99) > 2:   # ruido (checksum, LLDP): el integrador lo ignora
            ignoradas += 1
            continue
        alertas.append({"t": e.get("timestamp", "")[11:23], "src": e.get("src_ip"), "dst": e.get("dest_ip"),
                        "dport": e.get("dest_port"), "sid": a.get("signature_id"), "sev": a.get("severity"),
                        "firma": a.get("signature")})
    bloqueos = []
    for f in get(f"/flows/application/{APP_ID}").get("flows", []):
        ip = next((c.get("ip") for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"), "?")
        bloqueos.append({"ip": ip, "dev": NOMBRES.get(f["deviceId"], f["deviceId"]), "prio": f["priority"],
                         "pkts": f.get("packets", 0), "vida": f.get("life", 0), "estado": f.get("state"),
                         "timeout": f.get("timeout")})
    espejo = []
    for f in get(f"/flows/{CORE}").get("flows", []):
        if f["priority"] == 30000:
            cr = {c["type"]: c.get("ip") or c.get("port") for c in f["selector"]["criteria"]}
            dire = "entrante" if "IN_PORT" in cr else ("interno" if cr.get("IPV4_DST") == cr.get("IPV4_SRC") else "saliente")
            espejo.append({"regla": dire, "pkts": f.get("packets", 0)})
    total = len(get(f"/flows/{CORE}").get("flows", []))
    with lock:
        ev = list(eventos[-60:])
    return {"eventos": ev, "alertas": alertas[:10], "ignoradas": ignoradas, "bloqueos": sorted(bloqueos, key=lambda b: b["vida"]),
            "espejo": espejo, "flujos_core": total, "tiempos": tiempos(), "log": cola(INTEG_LOG, 8),
            "hora": time.strftime("%H:%M:%S")}


def tiempos():
    """Tramos de cada bloqueo del estado del integrador: M1 alerta->lectura y M2 lectura->201 (ms)."""
    try:
        with open(STATE_PATH) as f:
            bl = json.load(f).get("bloqueadas", {})
    except (OSError, ValueError):
        return []
    out = []
    for ip, v in bl.items():
        if not isinstance(v, dict) or not v.get("t_201") or not v.get("t_alerta"):
            continue
        try:
            t_a = datetime.strptime(v["t_alerta"], "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
        except ValueError:
            continue
        m1, m2 = (v["t_lectura"] - t_a) * 1000, (v["t_201"] - v["t_lectura"]) * 1000
        if m1 < 0 or m1 > 60000:      # reloj o evento viejo: no se muestra un dato falso
            continue
        out.append({"ip": ip, "sid": v.get("sid"), "donde": v.get("donde"), "m1": round(m1, 1),
                    "m2": round(m2, 1), "total": round(m1 + m2, 1), "t": v["t_201"]})
    return sorted(out, key=lambda x: -x["t"])[:6]


HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html"), encoding="utf8").read()


class H(BaseHTTPRequestHandler):
    def _send(self, code, tipo, cuerpo):
        b = cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            self._send(200, "application/json", json.dumps(estado()))
        else:
            self._send(200, "text/html; charset=utf-8", HTML)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        e = json.loads(self.rfile.read(n) or b"{}")
        with lock:
            if e.get("limpiar"):
                eventos.clear()
            else:
                eventos.append({"t": time.strftime("%H:%M:%S"), **e})
        self._send(200, "application/json", "{}")

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", 8080), H).serve_forever()
