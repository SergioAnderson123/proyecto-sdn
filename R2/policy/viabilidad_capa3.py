"""viabilidad_capa3.py: PRUEBA FUNCIONAL AISLADA de la capa 3 en el slice real (VNRT), sin tocar el tráfico de producción.

Monta una mini red de capa 3 con subredes de PRUEBA (10.99.0.0/16) sobre los switches reales y la usa con tráfico real entre h1-profesor, h2-alumno y el server:
    servicios_p 10.99.0.0/24 (core-1, puerto 5: server)   acc1_p 10.99.1.0/24 (acc-1, puerto 1: h1)   acc2_p 10.99.2.0/24 (acc-2, puerto 1: h2)   gateway virtual .254, MAC 02:00:5E:99:99:FE

AISLAMIENTO (por qué no puede afectar a producción):
  - Los flujos de la capa 3 van a la TABLA 62, a la que ningún flujo de producción salta.
  - Solo se entra a ella por dos reglas de la tabla 0 (prioridad 40060): (1) el ARP cuya IP objetivo es un gateway de prueba (10.99.x.254: nadie más la usa) y (2) el IPv4 cuya MAC destino es la
    MAC de gateway de prueba (nadie más la usa). Cualquier otro paquete sigue exactamente su camino de siempre.
  - Todos los flujos llevan una cookie propia (0x99c3...): se borran de una vez con `del-flows cookie=0x99c3000000000000/0xffff000000000000`.
  - Los flujos se instalan con `ovs-ofctl` (no por ONOS): el REST de ONOS 2.7 no sabe emparejar ARP_TPA ni usar las extensiones de copia de campos de Open vSwitch. ONOS puede borrarlos por
    "extraños" (allowExtraneousRules=false): medirlo es parte de la prueba (ver el playbook 97).

    python3 viabilidad_capa3.py generar --salida /tmp/viab            # un archivo .flows por switch y hosts.json (lee los cables de ONOS)
    python3 viabilidad_capa3.py generar --salida /tmp/viab --links links.json   # sin ONOS (para pruebas)
"""
import argparse
import json
import os
import sys
import urllib.request
import base64

import caminos as c
import capa3 as l3
import ovs_formato as ovs
from plan_arboles import NOMBRES, PREFERIDOS

GW_MAC = "02:00:5E:99:99:FE"
TABLA_PRUEBA = 62
PRIO_ENTRADA = 40060                 # sobre la sesión (40001), el portal (40002) y el ARP proactivo (40003); bajo la lista negra (45000)
COOKIE_BASE = 0x99C3000000000000
COOKIE_MASCARA = 0xFFFF000000000000

PLAN = {"gw_mac": GW_MAC, "subredes": [
    {"nombre": "servicios_p", "dispositivo": "core-1", "prefijo": "10.99.0.0/24", "gateway": "10.99.0.254", "puertos": [5]},
    {"nombre": "acc1_p", "dispositivo": "acc-1", "prefijo": "10.99.1.0/24", "gateway": "10.99.1.254", "puertos": [1]},
    {"nombre": "acc2_p", "dispositivo": "acc-2", "prefijo": "10.99.2.0/24", "gateway": "10.99.2.254", "puertos": [1]}]}

DPID = {v: k for k, v in NOMBRES.items()}
HOSTS = [   # MAC reales del slice; la IP de prueba se añade como dirección SECUNDARIA (la de producción no se toca)
    {"nombre": "server", "mac": "fa:16:3e:92:d3:e6", "ip": "10.99.0.10", "device": DPID["core-1"], "port": 5, "iface": "ens4", "gw": "10.99.0.254"},
    {"nombre": "h1-profesor", "mac": "fa:16:3e:b6:96:08", "ip": "10.99.1.101", "device": DPID["acc-1"], "port": 1, "iface": "ens4", "gw": "10.99.1.254"},
    {"nombre": "h2-alumno", "mac": "fa:16:3e:71:08:66", "ip": "10.99.2.102", "device": DPID["acc-2"], "port": 1, "iface": "ens4", "gw": "10.99.2.254"},
]


def pasos_de_entrada(plan: l3.Plan, grafo: c.Grafo) -> list:
    """Las reglas de la tabla 0 que llevan a la tabla de prueba (la única puerta de entrada)."""
    reglas = []
    for d in sorted(grafo):
        reglas.append({"priority": PRIO_ENTRADA, "tableId": 0, "deviceId": d, "selector": {"criteria": [
            {"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": plan.gw_mac}]},
            "treatment": {"instructions": [{"type": "TABLE", "tableId": TABLA_PRUEBA}]}})
    for s in plan.subredes:
        for p in s.puertos:
            reglas.append({"priority": PRIO_ENTRADA, "tableId": 0, "deviceId": s.dispositivo, "selector": {"criteria": [
                {"type": "IN_PORT", "port": p}, {"type": "ETH_TYPE", "ethType": "0x0806"}, {"type": "ARP_OP", "op": 1},
                {"type": "ARP_TPA", "ip": f"{s.gateway}/32"}]}, "treatment": {"instructions": [{"type": "TABLE", "tableId": TABLA_PRUEBA}]}})
    return reglas


def flujos(links) -> list:
    """Todos los flujos de la prueba en formato interno (entrada en la tabla 0 + capa 3 en la tabla 62)."""
    grafo = c.grafo_desde_links(links)
    plan = l3.cargar_plan(PLAN, nombres=NOMBRES)
    return pasos_de_entrada(plan, grafo) + l3.flujos_capa3(plan, grafo, HOSTS, PREFERIDOS, tabla=TABLA_PRUEBA)


def construir(links) -> dict:
    """{dpid: [líneas de ovs-ofctl]} con todo lo necesario."""
    salida = {d: [] for d in c.grafo_desde_links(links)}
    for n, f in enumerate(flujos(links), start=1):
        salida[f["deviceId"]].append(ovs.a_ovs(f, COOKIE_BASE | n))
    return salida


def comandos_hosts() -> dict:
    """{nombre: {'poner': [...], 'quitar': [...]}}: dirección secundaria de prueba y ruta por el gateway de prueba (solo para 10.99.0.0/16)."""
    r = {}
    for h in HOSTS:
        r[h["nombre"]] = {
            "poner": [f"ip addr add {h['ip']}/24 dev {h['iface']}", f"ip route add 10.99.0.0/16 via {h['gw']} dev {h['iface']}"],
            "quitar": [f"ip route del 10.99.0.0/16 via {h['gw']} dev {h['iface']}", f"ip addr del {h['ip']}/24 dev {h['iface']}"]}
    return r


def leer_links(onos_url: str, usuario: str = "onos:rocks"):
    req = urllib.request.Request(onos_url.rstrip("/") + "/onos/v1/links", headers={"Authorization": "Basic " + base64.b64encode(usuario.encode()).decode()})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["links"]


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("orden", choices=["generar"])
    a.add_argument("--salida", required=True)
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--links", help="archivo con la respuesta de /links (en vez de consultar ONOS)")
    arg = a.parse_args(argv)
    links = json.load(open(arg.links, encoding="utf-8"))["links"] if arg.links else leer_links(arg.onos)
    if sum(len(v) for v in c.grafo_desde_links(links).values()) != 14:
        raise SystemExit("ONOS no ve los 14 enlaces: no se genera nada (con un cable caído los árboles serían otros).")
    os.makedirs(arg.salida, exist_ok=True)
    for dpid, lineas in construir(links).items():
        with open(os.path.join(arg.salida, f"{NOMBRES.get(dpid, dpid)}.flows"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lineas) + "\n")
    with open(os.path.join(arg.salida, "hosts.json"), "w", encoding="utf-8") as f:
        json.dump(comandos_hosts(), f, indent=1)
    print(f"Generado en {arg.salida}: " + ", ".join(f"{NOMBRES.get(d, d)}={len(l)}" for d, l in sorted(construir(links).items(), key=lambda x: NOMBRES.get(x[0], x[0]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
