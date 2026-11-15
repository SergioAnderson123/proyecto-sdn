"""explicar_ruta.py: SOLO LECTURA. Explica paso a paso cómo se decide la ruta entre dos switches (o hacia un host) y la compara con ONOS.

    python3 explicar_ruta.py acc-2 acc-1                       # lee la topología de ONOS (http://192.168.201.1:8181)
    python3 explicar_ruta.py acc-2 acc-1 --comparar            # además pregunta a ONOS su propia ruta (/paths) y las contrasta
    python3 explicar_ruta.py acc-2 FA:16:3E:B6:96:08           # el destino puede ser la MAC de un host: muestra también sus flujos
    python3 explicar_ruta.py acc-2 acc-1 --links links.json    # sin ONOS: usa un /links guardado (curl .../onos/v1/links)

Los nombres son acc-1, acc-2, core-1, core-2, borde (o un deviceId de ONOS). No instala nada.
"""
import argparse
import json
import re
import sys

import caminos as c
import flujos_proactivos as fp
from plan_arboles import NOMBRES, PREFERIDOS

POR_NOMBRE = {v: k for k, v in NOMBRES.items()}


def nombre(dpid):
    return NOMBRES.get(dpid, dpid)


def resolver(texto):
    return POR_NOMBRE.get(texto, texto)


def saltos(n):
    return f"{n} salto" + ("" if n == 1 else "s")


def derivar(grafo, origen, destino, preferidos):
    """Estructura con TODO lo que hay que mostrar (pura: sin imprimir ni tocar la red)."""
    dist = c.niveles_hacia(grafo, destino)
    if origen not in dist:
        return {"error": f"{nombre(origen)} no tiene camino hacia {nombre(destino)} en la topología leída."}
    dec = c.decisiones(grafo, destino, preferidos)
    arbol = c.arbol_hacia(grafo, destino, preferidos)
    ruta = c.camino(arbol, origen, destino)
    pasos = [dict(dec[sw], switch=sw) for sw in ruta[:-1]]
    capas = {}
    for d, n in dist.items():
        capas.setdefault(n, []).append(d)
    return {"dist": dist, "capas": {n: sorted(v, key=nombre) for n, v in sorted(capas.items())}, "ruta": ruta, "pasos": pasos,
            "saltos": len(ruta) - 1, "arbol_valido": c.es_arbol(arbol, destino)}


def lineas(grafo, d, origen, destino, preferidos, mac=None, puerto_host=None):
    """El informe en texto."""
    o = []
    cables = sorted({tuple(sorted([(nombre(a), p) for a, p in ((x, pa), (y, pb))])) for x, pa, y, pb in
                     [(a, p, b, next((q for v, q in grafo.get(b, []) if v == a), "?")) for a, sal in grafo.items() for b, p in sal]})
    o.append("PASO 1. Topología leída de ONOS (GET /onos/v1/links): %d switches, %d cables" % (len(grafo), len(cables)))
    for (a, pa), (b, pb) in cables:
        o.append(f"    {a} p{pa}  <->  {b} p{pb}")
    if "error" in d:
        o.append(d["error"])
        return o
    o.append("")
    o.append(f"PASO 2. Distancia en saltos hasta {nombre(destino)} (búsqueda en anchura desde el destino)")
    for n, sws in d["capas"].items():
        o.append(f"    {saltos(n)}: {', '.join(nombre(s) for s in sws)}" + ("   <- destino" if n == 0 else ""))
    o.append("")
    o.append("PASO 3. Decisión en cada switch del camino (regla: vecino un salto más cerca; a igualdad, el preferido; luego el de nombre menor)")
    o.append(f"    preferidos, en orden: {', '.join(nombre(p) for p in preferidos)}")
    for p in d["pasos"]:
        cands = ", ".join(f"{nombre(u)} (por p{pt})" for u, pt in p["candidatos"])
        if p["empate"]:
            o.append(f"    {nombre(p['switch'])} está a {saltos(p['nivel'])}. Candidatos: {cands}.")
            o.append(f"        EMPATE -> gana {nombre(p['elegido'])}, el más preferido. Sale por p{p['puerto']}.")
        else:
            o.append(f"    {nombre(p['switch'])} está a {saltos(p['nivel'])}. Único candidato: {cands}. Sale por p{p['puerto']}.")
    o.append("")
    o.append("PASO 4. Ruta resultante (%s)" % saltos(d["saltos"]))
    partes = []
    for p in d["pasos"]:
        partes.append(f"{nombre(p['switch'])} --p{p['puerto']}-->")
    o.append("    " + " ".join(partes) + f" {nombre(destino)}")
    o.append("    Sin ciclos: la distancia al destino baja en cada salto (%s). Árbol válido: " % " > ".join([str(x["nivel"]) for x in d["pasos"]] + ["0"])
             + ("sí" if d["arbol_valido"] else "NO"))
    if mac:
        o.append("")
        o.append(f"PASO 5. Flujos unicast para la MAC {mac} (tabla {fp.TABLA_REENVIO}), uno por switch del camino")
        host = [{"mac": mac, "device": destino, "port": puerto_host}]
        flujos = fp.flujos_unicast(host, grafo, [p["switch"] for p in d["pasos"]] + [destino], preferidos)
        for f in flujos:
            o.append(f"    {nombre(f['deviceId']):8s} eth_dst={mac} -> output:{f['treatment']['instructions'][0]['port']}")
    return o


def comparar_onos(onos, origen, destino, ruta):
    """Contrasta con GET /paths/{src}/{dst} de ONOS (su propio cálculo, independiente del nuestro)."""
    o = ["", "VERIFICACIÓN CRUZADA con ONOS (GET /onos/v1/paths/%s/%s)" % (origen, destino)]
    try:
        paths = onos.get(f"/paths/{origen}/{destino}").get("paths", [])
    except Exception as e:  # noqa: BLE001
        return o + [f"    no se pudo consultar: {e}"]
    if not paths:
        return o + ["    ONOS no devolvió ninguna ruta."]
    rutas = []
    for p in paths:
        links = p["links"]
        rutas.append([links[0]["src"]["device"]] + [l["dst"]["device"] for l in links])
    o.append(f"    ONOS devuelve {len(rutas)} ruta(s) de igual costo (cost={paths[0].get('cost')}):")
    for r in rutas:
        o.append("        " + " -> ".join(nombre(x) for x in r) + ("   <- la nuestra" if r == ruta else ""))
    if ruta in rutas:
        o.append("    RESULTADO: nuestra ruta es una de las más cortas que calcula ONOS.")
    elif len(rutas[0]) == len(ruta):
        o.append("    RESULTADO: misma longitud que ONOS; elige otro desempate entre rutas igual de cortas (no es un error).")
    else:
        o.append("    RESULTADO: DISTINTA LONGITUD. Revisar: no debería pasar.")
    return o


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("origen")
    a.add_argument("destino", help="switch (acc-1...) o MAC de un host")
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--links", help="usar un /links guardado en vez de consultar ONOS")
    a.add_argument("--hosts", help="usar un /hosts guardado (solo si el destino es una MAC)")
    a.add_argument("--comparar", action="store_true", help="contrastar con el cálculo de ONOS (/paths); necesita ONOS")
    arg = a.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    onos = None
    if not arg.links or arg.comparar or (re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", arg.destino) and not arg.hosts):
        from instalar_proactivo import Onos
        onos = Onos(arg.onos, arg.usuario)
    links = json.load(open(arg.links, encoding="utf-8"))["links"] if arg.links else onos.get("/links")["links"]
    grafo = c.grafo_desde_links(links)
    origen = resolver(arg.origen)
    mac = puerto_host = None
    if re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", arg.destino):
        from vista_previa import hosts_de_onos
        datos = json.load(open(arg.hosts, encoding="utf-8")) if arg.hosts else onos.get("/hosts")
        hs = [h for h in hosts_de_onos(datos) if h["mac"].lower() == arg.destino.lower()]
        if not hs:
            print(f"ONOS no conoce ningún host con la MAC {arg.destino}.")
            return 2
        mac, destino, puerto_host = hs[0]["mac"].upper(), hs[0]["device"], hs[0]["port"]
        print(f"El host {mac} cuelga de {nombre(destino)}, puerto {puerto_host} (GET /onos/v1/hosts).\n")
    else:
        destino = resolver(arg.destino)
    for dev in (origen, destino):
        if dev not in grafo:
            print(f"{dev} no está en la topología de ONOS.")
            return 2
    d = derivar(grafo, origen, destino, PREFERIDOS)
    print("\n".join(lineas(grafo, d, origen, destino, PREFERIDOS, mac, puerto_host)))
    if arg.comparar and "error" not in d:
        print("\n".join(comparar_onos(onos, origen, destino, d["ruta"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
