#!/usr/bin/env python3
"""Cuenta, por switch, los flujos de la tabla 62, las reglas de entrada (tabla 0, prioridad 40060) y los de la tabla 60 que ve ONOS, y los flujos de una app. Solo lee.

    python3 contar_flujos.py --onos http://192.168.201.1:8181 --usuario onos:rocks [--app org.campus.gatewayarp]
"""
import argparse
import base64
import json
import urllib.request

SWITCHES = ["of:000052ca28945241", "of:00007e3401ecd842", "of:000076f83813d34e", "of:0000e6925d87354b", "of:0000766a0c84c741"]
NOMBRES = {"of:000052ca28945241": "borde", "of:00007e3401ecd842": "core-1", "of:000076f83813d34e": "core-2", "of:0000e6925d87354b": "acc-1", "of:0000766a0c84c741": "acc-2"}


def pedir(base, usuario, ruta):
    r = urllib.request.Request(base + "/onos/v1" + ruta, headers={"Authorization": "Basic " + base64.b64encode(usuario.encode()).decode()})
    with urllib.request.urlopen(r, timeout=10) as x:
        return json.load(x)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--app")
    arg = a.parse_args()
    if arg.app:
        flujos = pedir(arg.onos, arg.usuario, "/flows/application/" + arg.app)["flows"]
        print(f"flujos de la app {arg.app}: {len(flujos)} ({sum(1 for f in flujos if f['state'] == 'ADDED')} ADDED)")
    entrega, paquetes = 0, 0
    for d in SWITCHES:
        f = pedir(arg.onos, arg.usuario, "/flows/" + d)["flows"]
        e = sum(1 for x in f if x["tableId"] == 60 and x["priority"] == 160)      # capa 3 en produccion: entrega por IP /32
        entrega += e
        paquetes += sum(int(x.get("packets", 0)) for x in f if x["tableId"] == 60 and x["priority"] == 160)
        print(NOMBRES[d], "tabla62=%d" % sum(1 for x in f if x["tableId"] == 62),
              "entrada40060=%d" % sum(1 for x in f if x["tableId"] == 0 and x["priority"] == 40060),
              "tabla60=%d" % sum(1 for x in f if x["tableId"] == 60), "entrega160=%d" % e)
    print("total_entrega160=%d" % entrega, "paquetes_entrega160=%d" % paquetes)


if __name__ == "__main__":
    main()
