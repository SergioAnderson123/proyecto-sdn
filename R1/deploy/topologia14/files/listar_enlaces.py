#!/usr/bin/env python3
"""Lista los cables que ONOS ve entre switches (nombre/puerto <-> nombre/puerto). Solo lee.

    python3 listar_enlaces.py --onos http://192.168.201.1:8181 --usuario onos:rocks [--desde acc-1 --puerto 2]
Con --desde y --puerto imprime solo el cable de ese puerto (para confirmar a donde va antes de bajarlo).
"""
import argparse
import base64
import json
import urllib.request

NOMBRES = {"of:000052ca28945241": "borde", "of:00007e3401ecd842": "core-1", "of:000076f83813d34e": "core-2", "of:0000e6925d87354b": "acc-1", "of:0000766a0c84c741": "acc-2"}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--desde")
    a.add_argument("--puerto")
    arg = a.parse_args()
    r = urllib.request.Request(arg.onos + "/onos/v1/links", headers={"Authorization": "Basic " + base64.b64encode(arg.usuario.encode()).decode()})
    with urllib.request.urlopen(r, timeout=10) as x:
        links = json.load(x)["links"]
    filas = sorted((NOMBRES.get(l["src"]["device"], l["src"]["device"]), int(l["src"]["port"]), NOMBRES.get(l["dst"]["device"], l["dst"]["device"]), int(l["dst"]["port"])) for l in links)
    if arg.desde:
        filas = [f for f in filas if f[0] == arg.desde and str(f[1]) == str(arg.puerto)]
        if not filas:
            raise SystemExit(f"ONOS no ve ningun cable en {arg.desde}/{arg.puerto}")
    for o, po, d, pd in filas:
        print(f"{o}/{po} -> {d}/{pd}")
    if not arg.desde:
        print(f"{len(filas)} enlaces dirigidos")


if __name__ == "__main__":
    main()
