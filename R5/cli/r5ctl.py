#!/usr/bin/env python3
"""r5ctl - línea de comandos de administración de R5 (seguridad perimetral).

  r5ctl estado                    resumen: bloqueos, espejo y últimas alertas
  r5ctl bloqueos                  IPs bloqueadas por R5 en ONOS
  r5ctl alertas [-n 20]           últimas alertas accionables (severidad <= 2) de Suricata
  r5ctl bloquear IP [--seg 600]   bloqueo manual de una IP en el Core (o en --dispositivo)
  r5ctl desbloquear IP            retira el bloqueo (falso positivo)

Variables de entorno: ONOS_URL, ONOS_USER, ONOS_PASS, APP_ID, P_R5, CORE_DEVICE, EVE_PATH.
Los bloqueos manuales y los del integrador usan el mismo appId, así que aparecen juntos.
"""
import argparse
import ipaddress
import json
import os
import sys

import requests

ONOS_URL = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1")
AUTH = (os.getenv("ONOS_USER", "onos"), os.getenv("ONOS_PASS", "rocks"))
APP_ID = os.getenv("APP_ID", "org.campus.r5")
PRIORIDAD = int(os.getenv("P_R5", "42000"))
CORE = os.getenv("CORE_DEVICE", "of:0000000000000001")
EVE = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")
MAX_SEVERIDAD = int(os.getenv("MAX_SEVERITY", "2"))
PRIO_ESPEJO = int(os.getenv("P_ESPEJO", "41000"))   # misma variable que lab/espejo.py
NOMBRES = {"of:0000000000000001": "s1 Core", "of:0000000000000002": "s2", "of:0000000000000003": "s3", "of:0000a223f2c04547": "s1 Core", "of:00001a749039894a": "s2 borde (h1,h2)", "of:0000ca272d265744": "s3 borde (h3,h4)"}


def onos(metodo, ruta, **kw):
    try:
        r = requests.request(metodo, f"{ONOS_URL}{ruta}", auth=AUTH, timeout=5, **kw)
    except requests.RequestException as e:
        sys.exit(f"error: no se pudo hablar con ONOS ({ONOS_URL}): {e}")
    if r.status_code >= 400:
        sys.exit(f"error: ONOS respondió HTTP {r.status_code}: {r.text[:200]}")
    return r


def ip_valida(texto):
    try:
        return str(ipaddress.IPv4Address(texto))
    except ValueError:
        sys.exit(f"error: '{texto}' no es una IPv4 válida")


def flujos_r5():
    """[(ip, flujo)] de los DROP por IP de origen que pertenecen a R5."""
    res = []
    try:       # ONOS responde 404 mientras R5 no haya instalado ningún flujo (el appId aún no existe)
        r = requests.get(f"{ONOS_URL}/flows/application/{APP_ID}", auth=AUTH, timeout=5)
    except requests.RequestException as e:
        sys.exit(f"error: no se pudo hablar con ONOS ({ONOS_URL}): {e}")
    if r.status_code == 404:
        return res
    if r.status_code >= 400:
        sys.exit(f"error: ONOS respondió HTTP {r.status_code}: {r.text[:200]}")
    for f in r.json().get("flows", []):
        ip = next((c["ip"].split("/")[0] for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"), None)
        if ip:
            res.append((ip, f))
    return sorted(res, key=lambda x: x[1].get("life", 0))


def alertas_recientes(n):
    try:
        with open(EVE, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400000))
            lineas = f.read().decode("utf8", "replace").splitlines()
    except OSError as e:
        sys.exit(f"error: no se pudo leer {EVE} ({e}); ejecuta r5ctl dentro del contenedor o define EVE_PATH")
    out = []
    for l in reversed(lineas):
        try:
            e = json.loads(l)
        except ValueError:
            continue
        a = e.get("alert", {})
        if a.get("severity", 99) <= MAX_SEVERIDAD:
            out.append((e.get("timestamp", "")[11:23], e.get("src_ip"), e.get("dest_ip"), e.get("dest_port"),
                        a.get("severity"), a.get("signature")))
        if len(out) >= n:
            break
    return out


def tabla(filas, cabecera):
    anchos = [max(len(str(x)) for x in col) for col in zip(cabecera, *filas)]
    linea = lambda fila: "  ".join(str(x).ljust(w) for x, w in zip(fila, anchos))
    print(linea(cabecera))
    print(linea(["-" * w for w in anchos]))
    for fila in filas:
        print(linea(fila))


def cmd_bloqueos(_):
    fl = flujos_r5()
    if not fl:
        print("Sin bloqueos de R5 en ONOS.")
        return
    tabla([(ip, NOMBRES.get(f["deviceId"], f["deviceId"]), f["priority"], f.get("packets", 0), f"{f.get('life', 0)} s",
            f"{f.get('timeout', 0)} s" + (" (permanente)" if f.get("isPermanent") else " de inactividad"))
           for ip, f in fl], ["IP bloqueada", "switch", "prioridad", "paquetes", "vida", "timeout"])


def cmd_alertas(a):
    al = alertas_recientes(a.n)
    if not al:
        print(f"Sin alertas de severidad <= {MAX_SEVERIDAD}.")
        return
    tabla(al, ["hora", "origen", "destino", "puerto", "sev", "firma"])


def cmd_estado(a):
    fl = flujos_r5()
    print(f"Bloqueos activos de R5: {len(fl)}")
    core = onos("GET", f"/flows/{CORE}").json().get("flows", [])
    def sentido(f):
        cr = {c["type"]: c.get("ip") or c.get("port") for c in f["selector"]["criteria"]}
        if "IN_PORT" in cr:
            return "entrante"
        return "interno" if cr.get("IPV4_DST") == cr.get("IPV4_SRC") else "saliente"
    espejo = [(sentido(f), f.get("packets", 0)) for f in core if f["priority"] == PRIO_ESPEJO]
    print("Espejo al IDS:", ", ".join(f"{d} {p} paquetes" for d, p in espejo) or "NO instalado")
    print(f"Flujos en el Core: {len(core)}\n")
    cmd_bloqueos(a)
    print("\nÚltimas alertas accionables:")
    try:
        cmd_alertas(argparse.Namespace(n=5))
    except SystemExit as e:
        print(e)


def cmd_bloquear(a):
    ip = ip_valida(a.ip)
    dispositivo = a.dispositivo or CORE
    regla = {"flows": [{"priority": PRIORIDAD, "timeout": a.seg, "isPermanent": a.seg == 0, "deviceId": dispositivo,
                        "treatment": {"instructions": []},
                        "selector": {"criteria": [{"type": "ETH_TYPE", "ethType": "0x0800"},
                                                  {"type": "IPV4_SRC", "ip": f"{ip}/32"}]}}]}
    onos("POST", "/flows", params={"appId": APP_ID}, json=regla)
    print(f"Bloqueada {ip} en {NOMBRES.get(dispositivo, dispositivo)}"
          + (" (permanente)" if a.seg == 0 else f" por {a.seg} s de inactividad") + ".")


def cmd_desbloquear(a):
    ip = ip_valida(a.ip)
    objetivo = [f for i, f in flujos_r5() if i == ip]
    if not objetivo:
        sys.exit(f"{ip} no está bloqueada por R5.")
    for f in objetivo:
        onos("DELETE", f"/flows/{f['deviceId']}/{f['id']}")
    print(f"Desbloqueada {ip} ({len(objetivo)} flujo(s) retirado(s)). "
          "El integrador la olvida en su próxima reconciliación (hasta RECONCILE_SEC s).")


def main():
    p = argparse.ArgumentParser(prog="r5ctl", description="Administración de R5 (seguridad perimetral)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estado", help="resumen general").set_defaults(f=cmd_estado)
    sub.add_parser("bloqueos", help="IPs bloqueadas").set_defaults(f=cmd_bloqueos)
    s = sub.add_parser("alertas", help="últimas alertas accionables")
    s.add_argument("-n", type=int, default=15)
    s.set_defaults(f=cmd_alertas)
    s = sub.add_parser("bloquear", help="bloqueo manual de una IP")
    s.add_argument("ip")
    s.add_argument("--seg", type=int, default=300, help="timeout de inactividad en segundos; 0 = permanente")
    s.add_argument("--dispositivo", help="deviceId del switch (por defecto, el Core)")
    s.set_defaults(f=cmd_bloquear)
    s = sub.add_parser("desbloquear", help="retira el bloqueo de una IP")
    s.add_argument("ip")
    s.set_defaults(f=cmd_desbloquear)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
