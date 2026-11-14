"""plan_arboles.py: SOLO LECTURA. Calcula e imprime los árboles sin lazos con los enlaces reales de ONOS. No instala nada.

    curl.exe -u onos:rocks http://192.168.201.1:8181/onos/v1/links -o links.json
    python plan_arboles.py links.json
"""
import json
import sys

import caminos as c

NOMBRES = {"of:00007e3401ecd842": "core-1", "of:000076f83813d34e": "core-2", "of:000052ca28945241": "borde",
           "of:0000e6925d87354b": "acc-1", "of:0000766a0c84c741": "acc-2"}
PREFERIDOS = ["of:00007e3401ecd842", "of:000076f83813d34e"]


def n(d):
    return NOMBRES.get(d, d)


def main(ruta):
    with open(ruta, encoding="utf-8") as f:
        links = json.load(f)["links"]
    grafo = c.grafo_desde_links(links)
    print("Enlaces dirigidos:", sum(len(v) for v in grafo.values()), "| dispositivos:", len(grafo))
    ok = True
    for destino in sorted(grafo, key=n):
        arbol = c.arbol_hacia(grafo, destino, PREFERIDOS)
        bien = c.es_arbol(arbol, destino) and len(arbol) == len(grafo) - 1
        ok &= bien
        print(f"\nHacia {n(destino)}  [{'OK, sin lazos' if bien else 'PROBLEMA'}]")
        for d in sorted(arbol, key=n):
            sig, puerto = arbol[d]
            print(f"  {n(d):7s} sale por puerto {puerto} -> {n(sig)}")
    print("\nRESULTADO:", "todos los árboles son válidos" if ok else "HAY ÁRBOLES INCOMPLETOS: no activar nada")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
