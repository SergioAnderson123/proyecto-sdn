"""Monitor en vivo del laboratorio R2 (se refresca cada 2 s): entradas de TCAM por switch y tabla, sesiones activas,
CPU/RAM de los contenedores y las últimas líneas del registro de métricas (metricas_r2.py).

Uso:  python tests/monitor_r2.py        (Ctrl+C para salir)
"""
import base64
import json
import os
import subprocess
import time
import urllib.request
from collections import Counter

ONOS = "http://localhost:8181/onos/v1"
MAPPER = "http://localhost:5000"
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "pruebas-r2", "metricas.log")
CONTENEDORES = ["onos", "policy_mapper", "mininet", "radius", "ldap"]
NOMBRE = {"of:0000000000000001": "s1 (acceso)", "of:0000000000000002": "s2 (servidores)"}


def get(url, auth=False):
    r = urllib.request.Request(url)
    if auth:
        r.add_header("Authorization", "Basic " + base64.b64encode(b"onos:rocks").decode())
    try:
        return json.load(urllib.request.urlopen(r, timeout=3))
    except Exception:
        return None


def recursos():
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    filas = [l.split("|") for l in out.splitlines() if l.split("|")[0] in CONTENEDORES]
    return sorted(filas, key=lambda f: CONTENEDORES.index(f[0]))


def cola_log(n=14):
    try:
        with open(LOG, encoding="utf8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return ["(aún no hay mediciones: python tests/metricas_r2.py)"]


def main():
    while True:
        flujos = (get(f"{ONOS}/flows", True) or {}).get("flows", [])
        ses = (get(f"{MAPPER}/sessions") or {}).get("sessions", {})
        por = Counter((f["deviceId"], f["tableId"]) for f in flujos if f["state"] == "ADDED")
        pend = sum(1 for f in flujos if f["state"] != "ADDED")
        out = ["\033[H\033[J\033[1mR2 · monitor en vivo\033[0m   " + time.strftime("%H:%M:%S"), ""]
        out.append(f"\033[1mTCAM (flujos ADDED en ONOS)\033[0m   total {sum(por.values())}   pendientes/fallidos {pend}")
        for dev in sorted({d for d, _ in por}):
            partes = "  ".join(f"t{t}:{por[(dev, t)]}" for t in sorted(t for d, t in por if d == dev))
            out.append(f"  {NOMBRE.get(dev, dev[-4:]):<16}{sum(v for (d, _), v in por.items() if d == dev):>5}   {partes}")
        out += ["", f"\033[1mSesiones activas\033[0m  {len(ses)}"]
        for mac, s in list(ses.items())[:6]:
            out.append(f"  {s['username']:<26} VLAN {s['vlan']:<3}{s['rol']}")
        out += ["", "\033[1mRecursos\033[0m"]
        for n, cpu, mem in recursos():
            out.append(f"  {n:<14}{cpu:>8}   {mem}")
        out += ["", "\033[1mÚltimas mediciones\033[0m"] + ["  " + l[:110] for l in cola_log()]
        print("\n".join(out), flush=True)
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
