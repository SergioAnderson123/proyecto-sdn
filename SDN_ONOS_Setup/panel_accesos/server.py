"""Panel de accesos de R1/R2/R5: vista de estado en vivo (solo lectura).

Junta en un único JSON lo que hoy hay que mirar en cuatro sitios: las sesiones y la bitácora del Policy Mapper, los flujos de ONOS
(TCAM por switch y tabla), los bloqueos de R5 y la lista negra de R1. La página es `index.html` (diseño de Tabler, MIT).

Uso:  python panel_accesos/server.py [puerto]        -> http://localhost:8090
Variables: ONOS_URL (http://localhost:8181/onos/v1), ONOS_USER/ONOS_PASS (onos/rocks), MAPPER_URL (http://localhost:5000)
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ONOS = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1").rstrip("/")
MAPPER = os.getenv("MAPPER_URL", "http://localhost:5000").rstrip("/")
AUTH = "Basic " + base64.b64encode(f'{os.getenv("ONOS_USER", "onos")}:{os.getenv("ONOS_PASS", "rocks")}'.encode()).decode()
RAIZ = os.path.dirname(os.path.abspath(__file__))
P_DROP_R1, P_DROP_R5, P_ESPEJO = 45000, int(os.getenv("P_R5", "42000")), int(os.getenv("P_ESPEJO", "41000"))


def pedir(url, onos=False):
    r = urllib.request.Request(url)
    if onos:
        r.add_header("Authorization", AUTH)
    try:
        return json.load(urllib.request.urlopen(r, timeout=2))
    except Exception:  # noqa: BLE001 - un servicio caído no debe tumbar el panel
        return None


def nombre(dpid):
    try:
        return f"s{int(dpid[-4:], 16)}"
    except ValueError:
        return dpid


def criterio(flujo, tipo, clave):
    for c in flujo.get("selector", {}).get("criteria", []):
        if c.get("type") == tipo:
            return c.get(clave)
    return None


def estado():
    ses = pedir(MAPPER + "/sessions")
    ev = pedir(MAPPER + "/events?limit=40")
    flujos = (pedir(ONOS + "/flows", True) or {}).get("flows", [])
    disp = (pedir(ONOS + "/devices", True) or {}).get("devices", [])

    sesiones = [{"mac": mac, "usuario": s["username"], "rol": s["rol"], "vlan": s["vlan"], "switch": nombre(s["dpid"]),
                 "puerto": s["port"], "flow_id": s.get("flow_id")} for mac, s in (ses or {}).get("sessions", {}).items()]

    tcam = {}
    for f in flujos:
        d = tcam.setdefault(nombre(f["deviceId"]), {"total": 0, "pendientes": 0, "tablas": Counter()})
        if f["state"] == "ADDED":
            d["total"] += 1
            d["tablas"][str(f["tableId"])] += 1
        else:
            d["pendientes"] += 1
    for d in tcam.values():
        d["tablas"] = dict(sorted(d["tablas"].items(), key=lambda kv: int(kv[0])))

    bloqueos_r5, espejo, lista_negra = [], Counter(), []
    for f in flujos:
        if f["state"] != "ADDED":
            continue
        pkts = f.get("packets", 0)
        if f["priority"] == P_DROP_R5 and criterio(f, "IPV4_SRC", "ip"):
            bloqueos_r5.append({"ip": criterio(f, "IPV4_SRC", "ip").split("/")[0], "switch": nombre(f["deviceId"]),
                                "prioridad": f["priority"], "paquetes": pkts, "vida": f.get("life", 0)})
        elif f["priority"] == P_ESPEJO:
            sentido = "entrante" if criterio(f, "IN_PORT", "port") is not None else (
                "interno" if criterio(f, "IPV4_SRC", "ip") == criterio(f, "IPV4_DST", "ip") else "saliente")
            espejo[sentido] += pkts
        elif f["priority"] == P_DROP_R1 and criterio(f, "ETH_SRC", "mac"):
            lista_negra.append({"mac": criterio(f, "ETH_SRC", "mac").lower(), "switch": nombre(f["deviceId"]),
                                "puerto": criterio(f, "IN_PORT", "port"), "paquetes": pkts, "vida": f.get("life", 0)})

    return {
        "hora": time.time(),
        "salud": {"onos": bool(disp), "mapper": ses is not None},
        "dispositivos": [{"nombre": nombre(d["id"]), "disponible": d["available"]} for d in disp],
        "sesiones": sorted(sesiones, key=lambda s: s["usuario"]),
        "eventos": (ev or {}).get("events", []),
        "tcam": tcam,
        "bloqueos_r5": sorted(bloqueos_r5, key=lambda b: b["vida"]),
        "espejo": dict(espejo),
        "lista_negra": lista_negra,
    }


class Manejador(BaseHTTPRequestHandler):
    def _enviar(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta == "/api/estado":
            return self._enviar(200, "application/json", json.dumps(estado()).encode())
        archivo = "index.html" if ruta == "/" else ruta.lstrip("/")
        destino = os.path.normpath(os.path.join(RAIZ, archivo))
        if not destino.startswith(RAIZ) or not os.path.isfile(destino) or destino.endswith(".py"):
            return self._enviar(404, "text/plain", b"no encontrado")
        tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"
        with open(destino, "rb") as f:
            self._enviar(200, tipo + ("; charset=utf-8" if tipo.startswith("text") else ""), f.read())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f"Panel de accesos en http://localhost:{puerto}  (ONOS {ONOS}, mapper {MAPPER})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", puerto), Manejador).serve_forever()
