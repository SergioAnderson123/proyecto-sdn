"""benchmark_capa3.py: cuántas reglas necesita cada modo con hosts sintéticos (sin red). Compara el reenvío de capa 2 actual (un unicast por host en CADA switch + difusión por árbol)
con el de capa 3 por subredes (rutas completas y con ruta por defecto en los accesos), y cuánto tarda en calcularse el estado deseado (lo que hace el vigilante en cada ciclo).

    python3 benchmark_capa3.py                      # tabla en Markdown
    python3 benchmark_capa3.py --accesos 100 --hosts 48
"""
import argparse
import time

import caminos as c
import capa3 as l3
import flujos_proactivos as fp

GW = "02:00:5E:00:00:FE"
CORES = ["core1", "core2"]


def plan_sintetico(accesos, por_acceso, puertos=48):
    """`accesos` switches de acceso (cada uno con su /24 y `puertos` puertos de usuario) colgados de 2 cores, y `por_acceso` hosts en cada uno."""
    grafo = {"core1": [], "core2": []}
    subredes, hosts = [], []
    for a in range(accesos):
        d = f"acc{a}"
        grafo[d] = [("core1", 1), ("core2", 2)]
        grafo["core1"].append((d, 10 + a))
        grafo["core2"].append((d, 10 + a))
        red = f"10.{a // 250}.{a % 250}"
        subredes.append({"nombre": d, "dispositivo": d, "prefijo": f"{red}.0/24", "gateway": f"{red}.254", "puertos": list(range(3, 3 + puertos))})
        for i in range(por_acceso):
            hosts.append({"mac": "02:%02x:%02x:00:%02x:%02x" % (a // 256, a % 256, i // 256, i % 256), "ip": f"{red}.{i + 1}", "device": d, "port": 3 + i % puertos})
    return l3.cargar_plan({"gw_mac": GW, "subredes": subredes}), grafo, hosts


def medir(accesos, por_acceso, puertos=48):
    plan, grafo, hosts = plan_sintetico(accesos, por_acceso, puertos)
    t0 = time.perf_counter()
    l2 = (fp.flujos_unicast([{"mac": h["mac"], "device": h["device"], "port": h["port"]} for h in hosts], grafo, grafo.keys(), CORES)
          + fp.flujos_descarte(grafo.keys())
          + fp.flujos_difusion(grafo, CORES[0], {f"acc{a}": range(3, 3 + puertos) for a in range(accesos)}, CORES))
    t_l2 = time.perf_counter() - t0
    t0 = time.perf_counter()
    completo = l3.flujos_capa3(plan, grafo, hosts, CORES)
    t_c = time.perf_counter() - t0
    t0 = time.perf_counter()
    defecto = l3.flujos_capa3(plan, grafo, hosts, CORES, nucleo=CORES)
    t_d = time.perf_counter() - t0
    por = lambda fl: l3.contar_por_switch(fl)    # noqa: E731
    return {"accesos": accesos, "hosts": len(hosts),
            "l2": (len(l2), max(por(l2).values()), por(l2)["core1"], t_l2),
            "l3": (len(completo), max(por(completo).values()), por(completo)["core1"], t_c),
            "l3d": (len(defecto), max(por(defecto).values()), por(defecto)["core1"], t_d)}


def tabla(casos):
    lineas = ["| Accesos | Hosts | L2 total | L2 máx/switch | L2 core-1 | L3 total | L3 máx/switch | L3 core-1 | L3 (ruta por defecto) total | …core-1 | …acceso | t cálculo L2 / L3 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a, h, p in casos:
        m = medir(a, h, p)
        l2, l3c, l3d = m["l2"], m["l3"], m["l3d"]
        plan, grafo, hosts = plan_sintetico(a, h, p)
        acceso_d = l3.contar_por_switch(l3.flujos_capa3(plan, grafo, hosts, CORES, nucleo=CORES))["acc0"]
        lineas.append(f"| {a} | {m['hosts']} | {l2[0]:,} | {l2[1]:,} | {l2[2]:,} | {l3c[0]:,} | {l3c[1]:,} | {l3c[2]:,} | {l3d[0]:,} | {l3d[2]:,} | {acceso_d:,} | {l2[3]:.2f}s / {l3c[3]:.2f}s |")
    return "\n".join(lineas)


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--accesos", type=int)
    a.add_argument("--hosts", type=int, help="hosts por switch de acceso")
    a.add_argument("--puertos", type=int, default=48)
    arg = a.parse_args(argv)
    casos = [(arg.accesos, arg.hosts, arg.puertos)] if arg.accesos and arg.hosts else [(5, 20, 48), (20, 48, 48), (50, 48, 48), (100, 48, 48), (200, 48, 48)]
    print(tabla(casos))


if __name__ == "__main__":
    main()
