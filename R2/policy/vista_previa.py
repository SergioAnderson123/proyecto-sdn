"""vista_previa.py: SOLO LECTURA. Con los enlaces y hosts reales de ONOS imprime los flujos unicast que se instalarían. No instala nada.

    curl.exe -u onos:rocks http://localhost:8181/onos/v1/links -o links.json
    curl.exe -u onos:rocks http://localhost:8181/onos/v1/hosts -o hosts.json
    python vista_previa.py links.json hosts.json
"""
import json
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


def main(ruta_links, ruta_hosts):
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
    inunda = c.flujos_que_inundan(flujos)
    print(f"\nFlujos que se instalarían: {len(flujos)} (tabla {fp.TABLA_REENVIO})")
    for f in sorted(flujos, key=lambda x: (n(x["deviceId"]), -x["priority"])):
        crit = f["selector"]["criteria"]
        que = crit[0]["mac"] if crit else "(cualquier otro)"
        sal = f["treatment"]["instructions"]
        print(f"  {n(f['deviceId']):7s} eth_dst={que:19s} -> {sal[0]['port'] if sal else 'DESCARTE'}")
    print("\nFLUJOS QUE INUNDAN:", len(inunda), "(debe ser 0)")
    return 1 if inunda else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
