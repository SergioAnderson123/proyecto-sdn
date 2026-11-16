#!/usr/bin/env python3
"""Muestra lo que ONOS sabe de los equipos de la prueba DHCP (MAC que empiezan por 02:AA:BB:00:01): su IP, su ubicacion y si el switch de acceso tiene la regla de entrega /32 de esa IP. Solo lee.

    python3 hosts_dhcp_prueba.py --onos http://192.168.201.1:8181 --usuario onos:rocks [--prefijo 02:AA:BB:00:01] [--switch of:0000e6925d87354b]
"""
import argparse
import base64
import json
import urllib.request


def pedir(base, usuario, ruta):
    r = urllib.request.Request(base + "/onos/v1" + ruta, headers={"Authorization": "Basic " + base64.b64encode(usuario.encode()).decode()})
    with urllib.request.urlopen(r, timeout=10) as x:
        return json.load(x)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--prefijo", default="02:AA:BB:00:01")
    a.add_argument("--switch", default="of:0000e6925d87354b")
    arg = a.parse_args()
    hosts = [h for h in pedir(arg.onos, arg.usuario, "/hosts")["hosts"] if h["mac"].upper().startswith(arg.prefijo.upper())]
    flujos = pedir(arg.onos, arg.usuario, "/flows/" + arg.switch)["flows"]
    entregas = set()
    for f in flujos:
        if f["tableId"] == 60 and f["priority"] == 160:
            for c in f["selector"]["criteria"]:
                if c["type"] == "IPV4_DST":
                    entregas.add(c["ip"].split("/")[0])
    print(f"equipos de la prueba en ONOS: {len(hosts)}")
    for h in sorted(hosts, key=lambda x: x["mac"]):
        ips = h.get("ipAddresses", [])
        ubic = ",".join(f"{l['elementId'][-4:]}/{l['port']}" for l in h.get("locations", []))
        con_regla = [ip for ip in ips if ip in entregas]
        print(f"  {h['mac']}  ips={ips}  ubicacion={ubic}  regla_entrega_32={'SI' if con_regla else 'NO'}")
    print(f"reglas de entrega /32 en el switch de acceso: {sorted(entregas)}")


if __name__ == "__main__":
    main()
