#!/usr/bin/env python3
"""Exportador de métricas de R5 para Prometheus (puerto 9108), para un sensor del slice de 14 VM.

Es la versión de `R5/lab/docker/monitoring/exporter.py` adaptada a `topologia.yaml` (varios cores reales, no un solo
switch de Mininet): usa los mismos nombres de switch que ya resuelven `integrador.py`, `r5ctl` y `lab/espejo.py`.

Lee cuatro fuentes y las expone en formato Prometheus:
  - ONOS REST: flujos del appId de R5 (bloqueos) y del espejo en cada core (appId org.campus.espejo / org.onosproject.rest)
  - eve-alerts.json de Suricata: contador de alertas por firma/severidad
  - state.json del integrador M09 (mismo volumen, montado de solo lectura): tiempos lectura->201 y alerta->201 por IP bloqueada
  - stats.log de Suricata: paquetes capturados y descartes de este sensor

No escribe nada: ni en ONOS ni en los archivos que lee. Un sensor caído (o este exportador caído) no afecta el bloqueo:
el integrador no depende de este proceso para nada.
"""
import json
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topologia  # noqa: E402

topo = topologia.aplicar_entorno()      # con TOPOLOGIA=... rellena ONOS_URL y CORE_DEVICES si el entorno no los trae

ONOS = os.environ["ONOS_URL"].split(",")[0]       # el exportador solo lee: alcanza con el primer nodo (si cae, se reintenta solo)
ONOS_TODOS = [u.strip() for u in os.environ["ONOS_URL"].split(",") if u.strip()]
AUTH = (os.environ["ONOS_USER"], os.environ["ONOS_PASS"])
APP_ID = os.getenv("APP_ID", "org.campus.r5")
APP_ESPEJO = ("org.campus.espejo", "org.onosproject.rest")
P_ESPEJO = int(os.getenv("P_ESPEJO", "41000"))
EVE = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")
STATE = os.getenv("STATE_PATH", "/var/lib/integrador/state.json")
STATS = "/var/log/suricata/stats.log"
SENSOR = os.getenv("SENSOR", "sensor")
CORE_DEVICES = [d.strip() for d in os.getenv("CORE_DEVICES", "").split(",") if d.strip()] \
    or [os.getenv("CORE_DEVICE", "of:0000000000000001")]
# nombre de cada switch (cores y accesos), igual que ve r5ctl; sin topologia.yaml, al menos "core-1", "core-2"...
SWITCH = topologia.nombres_switches(topo) if topo else {d: f"core-{i+1}" for i, d in enumerate(CORE_DEVICES)}

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
    """Cuenta las alertas nuevas de eve-alerts.json de ESTE sensor (tolera rotación o truncado)."""
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


_nodo = 0


def onos(ruta):
    """GET de solo lectura a ONOS; si el nodo recordado no responde, prueba el resto del clúster (igual que integrador.py)."""
    global _nodo
    for i in range(len(ONOS_TODOS)):
        idx = (_nodo + i) % len(ONOS_TODOS)
        try:
            r = requests.get(f"{ONOS_TODOS[idx]}{ruta}", auth=AUTH, timeout=2)
            if r.status_code < 300:
                _nodo = idx
                return r.json()
        except requests.RequestException:
            continue
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

    add("# HELP r5_bloqueos_activos IPs bloqueadas por R5 en ONOS (vistas desde este sensor)")
    add("# TYPE r5_bloqueos_activos gauge")
    flujos = onos(f"/flows/application/{APP_ID}").get("flows", [])
    bloq = []
    for f in flujos:
        ip = next((c["ip"].split("/")[0] for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"), None)
        if ip:
            bloq.append((ip, SWITCH.get(f["deviceId"], f["deviceId"]), f))
    add(linea("r5_bloqueos_activos", len(bloq), sensor=SENSOR))
    add("# TYPE r5_bloqueo_paquetes gauge")
    add("# TYPE r5_bloqueo_vida_segundos gauge")
    for ip, sw, f in bloq:
        add(linea("r5_bloqueo_paquetes", f.get("packets", 0), ip=ip, switch=sw))
        add(linea("r5_bloqueo_vida_segundos", f.get("life", 0), ip=ip, switch=sw))
    add("# TYPE r5_bloqueo_paquetes_total counter")
    add(linea("r5_bloqueo_paquetes_total", sum(f.get("packets", 0) for _, _, f in bloq), sensor=SENSOR))

    add("# TYPE r5_espejo_paquetes_total counter")
    espejo_por_switch = {}      # (switch, direccion) -> paquetes sumados: puede haber varias reglas (una por prefijo/entrada)
    for dev in CORE_DEVICES:
        for f in onos(f"/flows/{dev}").get("flows", []):
            if f["priority"] == P_ESPEJO and f.get("appId") in APP_ESPEJO:
                cr = {c["type"]: c.get("ip") or c.get("port") for c in f["selector"]["criteria"]}
                d = "entrante" if "IN_PORT" in cr else ("interno" if cr.get("IPV4_DST") == cr.get("IPV4_SRC") else "saliente")
                k = (SWITCH.get(dev, dev), d)
                espejo_por_switch[k] = espejo_por_switch.get(k, 0) + f.get("packets", 0)
    for (sw, d), paquetes in espejo_por_switch.items():
        add(linea("r5_espejo_paquetes_total", paquetes, switch=sw, direccion=d))
    add("# TYPE r5_flujos_switch gauge")
    devs = onos("/devices").get("devices", [])
    for dev in devs:
        n = len(onos(f"/flows/{dev['id']}").get("flows", []))
        add(linea("r5_flujos_switch", n, switch=SWITCH.get(dev["id"], dev["id"])))
    add("# TYPE r5_dispositivos_conectados gauge")
    add(linea("r5_dispositivos_conectados", sum(1 for d in devs if d.get("available"))))

    add("# TYPE r5_alertas_total counter")
    with lock:
        for (firma, sid, sev), n in alertas.items():
            add(linea("r5_alertas_total", n, firma=firma, sid=sid, severidad=sev, sensor=SENSOR))
        corte = time.time() - 60
        while recientes and recientes[0][0] < corte:
            recientes.popleft()
        por_firma = {}
        for _, firma in recientes:
            por_firma[firma] = por_firma.get(firma, 0) + 1
        add("# HELP r5_alertas_ultimo_minuto Alertas accionables de los últimos 60 s por firma, en este sensor")
        add("# TYPE r5_alertas_ultimo_minuto gauge")
        for firma, n in por_firma.items():
            add(linea("r5_alertas_ultimo_minuto", n, firma=firma, sensor=SENSOR))
        add("# TYPE r5_alertas_ignoradas_total counter")
        add(linea("r5_alertas_ignoradas_total", ignoradas, sensor=SENSOR))

    add("# TYPE r5_bloqueo_lectura_a_201_ms gauge")
    add("# TYPE r5_bloqueo_alerta_a_201_ms gauge")
    try:
        st = json.load(open(STATE)).get("bloqueadas", {})
    except (OSError, ValueError):
        st = {}
    for ip, v in st.items():
        if v.get("t_lectura") and v.get("t_201"):
            add(linea("r5_bloqueo_lectura_a_201_ms", round((v["t_201"] - v["t_lectura"]) * 1000, 1), ip=ip, sensor=SENSOR))
            t = epoch(v.get("t_alerta"))
            if t:
                add(linea("r5_bloqueo_alerta_a_201_ms", round((v["t_201"] - t) * 1000, 1), ip=ip, sensor=SENSOR))

    p, d = suricata_stats()
    if p is not None:
        add("# TYPE r5_suricata_paquetes_capturados_total counter")
        add(linea("r5_suricata_paquetes_capturados_total", p, sensor=SENSOR))
        add("# TYPE r5_suricata_descartes_total counter")
        add(linea("r5_suricata_descartes_total", d, sensor=SENSOR))
    return "\n".join(out) + "\n"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            cuerpo = metricas().encode()
            self.send_response(200)
        except Exception as e:                      # nunca tumbar el exportador por un fallo puntual de ONOS o de lectura
            cuerpo = f"# error al generar métricas: {e}\n".encode()
            self.send_response(500)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9108), H).serve_forever()
