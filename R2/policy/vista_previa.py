"""vista_previa.py: SOLO LECTURA. Con los enlaces y hosts reales de ONOS imprime los flujos unicast que se instalarían. No instala nada.

    curl.exe -u onos:rocks http://localhost:8181/onos/v1/links -o links.json
    curl.exe -u onos:rocks http://localhost:8181/onos/v1/hosts -o hosts.json
    python vista_previa.py links.json hosts.json [edge_ports.txt]

edge_ports.txt: la salida del comando `edge-ports` de la consola de ONOS (líneas `of:000052ca28945241/1`). Con él también se muestran los flujos de
difusión ARP por árbol (sin ellos no hay forma de saber qué puertos son de hosts, sensores o server).
"""
import json
import re
import sys

import caminos as c
import flujos_proactivos as fp
from plan_arboles import NOMBRES, PREFERIDOS, n


def hosts_de_onos(datos):
    """[{mac, device, port}] desde GET /onos/v1/hosts (un host por MAC y ubicación)."""
    salida = []
    for h in datos.get("hosts", []):
        for loc in h.get("locations", []):
            salida.append({"mac": h["mac"], "device": loc["elementId"], "port": int(loc["port"]), "ips": h.get("ipAddresses", [])})
    return salida


def puertos_acceso_de(ruta):
    """{dispositivo: [puertos]} desde el texto del comando `edge-ports`."""
    r = {}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            m = re.search(r"(of:[0-9a-f]+)/(\d+)", linea)
            if m:
                r.setdefault(m.group(1), []).append(int(m.group(2)))
    return r


def main(ruta_links, ruta_hosts, ruta_edge=None):
    with open(ruta_links, encoding="utf-8") as f:
        grafo = c.grafo_desde_links(json.load(f)["links"])
    with open(ruta_hosts, encoding="utf-8") as f:
        hosts = hosts_de_onos(json.load(f))
    # puertos que son enlaces entre switches: un host "aprendido" ahí sería un error de ubicación
    troncales = {(d, p) for d, v in grafo.items() for _, p in v}
    print(f"Switches: {len(grafo)} | hosts vistos por ONOS: {len(hosts)}")
    if not hosts:
        print("ONOS no conoce ningún host todavía (nadie ha enviado tráfico). No hay flujos que mostrar.")
        return 0
    validos = []
    for h in hosts:
        marca = ""
        if (h["device"], h["port"]) in troncales:
            marca = "  <-- IGNORADO: está en un puerto troncal"
        else:
            validos.append(h)
        print(f"  {h['mac']}  {','.join(h['ips']) or '-':16s} en {n(h['device'])} puerto {h['port']}{marca}")
    flujos = fp.flujos_unicast(validos, grafo, grafo.keys(), PREFERIDOS) + fp.flujos_descarte(grafo.keys())
    if ruta_edge:
        acceso = puertos_acceso_de(ruta_edge)
        raiz = PREFERIDOS[0]
        difusion = fp.flujos_difusion(grafo, raiz, acceso, PREFERIDOS)
        flujos += difusion
        print(f"\nDifusión ARP por árbol con raíz {n(raiz)}: {len(difusion)} flujos (broadcast ARP, tabla {fp.TABLA_REENVIO})")
        for f in sorted(difusion, key=lambda x: (n(x["deviceId"]), x["selector"]["criteria"][0]["port"])):
            ent = f["selector"]["criteria"][0]["port"]
            sal = ",".join(i["port"] for i in f["treatment"]["instructions"]) or "nada"
            print(f"  {n(f['deviceId']):7s} entra por {ent} -> sale por {sal}")
    inunda = c.flujos_que_inundan(flujos)
    print(f"\nFlujos que se instalarían: {len(flujos)} (tabla {fp.TABLA_REENVIO})")
    for f in sorted(flujos, key=lambda x: (n(x["deviceId"]), -x["priority"])):
        if f["priority"] == fp.PRIO_DIFUSION:
            continue
        crit = f["selector"]["criteria"]
        que = crit[0]["mac"] if crit else "(cualquier otro)"
        sal = f["treatment"]["instructions"]
        print(f"  {n(f['deviceId']):7s} eth_dst={que:19s} -> {sal[0]['port'] if sal else 'DESCARTE'}")
    print("\nFLUJOS QUE INUNDAN:", len(inunda), "(debe ser 0)")
    return 1 if inunda else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
