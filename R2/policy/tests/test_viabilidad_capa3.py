"""La prueba funcional aislada de la capa 3 (viabilidad_capa3.py): que funcione con la topología real y, sobre todo, que NO altere el tráfico de producción."""
import itertools
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import caminos as c  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
import policy_tables as pt  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
import viabilidad_capa3 as v  # noqa: E402
from plan_arboles import NOMBRES, PREFERIDOS  # noqa: E402
from test_capa3 import arp_peticion, tcp  # noqa: E402
from test_generar_politica_local import LINKS  # noqa: E402

POR = {x: k for k, x in NOMBRES.items()}
G = c.grafo_desde_links(LINKS)
ACCESO = {POR["core-1"]: [5], POR["acc-1"]: [1], POR["acc-2"]: [1], POR["borde"]: [1]}
SRV, H1, H2 = v.HOSTS


def produccion():
    """Algo parecido a lo que hay en el slice: ARP e IPv4 a la tabla 60 (como la confianza), unicast por MAC, descarte y difusión por árbol."""
    flujos = (fp.flujos_unicast([{"mac": h["mac"], "device": h["device"], "port": h["port"]} for h in v.HOSTS], G, G.keys(), PREFERIDOS) + fp.flujos_descarte(G.keys())
              + fp.flujos_difusion(G, PREFERIDOS[0], ACCESO, PREFERIDOS))
    for d in G:
        flujos.append(pt._base(d, 0, 40003, [{"type": "ETH_TYPE", "ethType": "0x0806"}], [{"type": "TABLE", "tableId": 60}]))
        flujos.append(pt._base(d, 0, 40001, [{"type": "ETH_TYPE", "ethType": "0x0800"}], [{"type": "TABLE", "tableId": 60}]))
    return flujos


def armar(flujos):
    fisico = {}
    for d, salidas in G.items():
        for nb, p in salidas:
            fisico[(d, p)] = (nb, next(q for x, q in G[nb] if x == d))
    return {"flujos": {d: [f for f in flujos if f["deviceId"] == d] for d in G}, "fisico": fisico, "hosts": {(h["device"], h["port"]): h["nombre"] for h in v.HOSTS}}


def paquete_ip(origen, destino, dst_mac=None, ttl=64):
    return tcp({"mac": origen["mac"], "ip": origen["ip"]}, {"ip": destino["ip"]}, dst_mac=dst_mac or v.GW_MAC, ttl=ttl)


def desde(r, h, pkt):
    return sim.entregar(r, h["device"], h["port"], pkt)


# ---------- el montaje ----------
def test_los_flujos_van_en_la_tabla_62_o_en_la_entrada_de_la_tabla_0_y_con_la_cookie_propia():
    todos = v.flujos(LINKS)
    assert todos and {f["tableId"] for f in todos} == {0, 62}
    assert all(f["priority"] == v.PRIO_ENTRADA for f in todos if f["tableId"] == 0)
    lineas = [x for ls in v.construir(LINKS).values() for x in ls]
    assert all(x.startswith("cookie=0x99c3") for x in lineas) and len(lineas) == len(todos)
    assert not any("table=60" in x or "NORMAL" in x.upper() for x in lineas)


def test_ningun_flujo_de_la_prueba_inunda_ni_salta_hacia_atras():
    todos = v.flujos(LINKS)
    assert c.flujos_que_inundan(todos) == []
    for f in todos:
        for i in f["treatment"]["instructions"]:
            if i["type"] == "TABLE":
                assert int(i["tableId"]) > int(f["tableId"])


def test_la_entrada_a_la_tabla_de_prueba_solo_casa_con_el_arp_de_los_gateways_de_prueba_y_con_la_mac_de_gateway_de_prueba():
    entradas = [x for x in v.flujos(LINKS) if x["tableId"] == 0]
    assert entradas
    for f in entradas:
        crit = {cr["type"]: cr for cr in f["selector"]["criteria"]}
        por_mac = crit.get("ETH_DST", {}).get("mac") == v.GW_MAC
        por_arp = crit.get("ARP_TPA", {}).get("ip", "").startswith("10.99.") and crit["ARP_TPA"]["ip"].endswith(".254/32")
        assert por_mac or por_arp


# ---------- funciona ----------
@pytest.mark.parametrize("h", v.HOSTS, ids=lambda h: h["nombre"])
def test_el_gateway_de_prueba_contesta_el_arp_con_la_mac_de_prueba(h):
    r = armar(produccion() + v.flujos(LINKS))
    res = desde(r, h, arp_peticion({"mac": h["mac"], "ip": h["ip"]}, h["gw"]))
    assert res["entregado"] == {h["nombre"]: 1} and set(res["llegadas"]) == {h["device"]}
    resp = res["paquetes"][h["nombre"]][0]
    assert resp["eth_src"].upper() == v.GW_MAC and resp["arp_spa"] == h["gw"] and resp["arp_op"] == 2 and resp["arp_tpa"] == h["ip"]


@pytest.mark.parametrize("origen,destino", list(itertools.permutations(v.HOSTS, 2)), ids=lambda h: h["nombre"])
def test_el_trafico_de_prueba_entre_subredes_llega_con_el_ttl_esperado(origen, destino):
    r = armar(produccion() + v.flujos(LINKS))
    res = desde(r, origen, paquete_ip(origen, destino))
    assert res["entregado"] == {destino["nombre"]: 1} and not res["inunda"] and not res["cortado"]
    final = res["paquetes"][destino["nombre"]][0]
    ruta = c.camino(c.arbol_hacia(G, destino["device"], PREFERIDOS), origen["device"], destino["device"])
    assert final["ttl"] == 64 - (len(ruta) - 1) - 1 and final["eth_dst"].lower() == destino["mac"].lower() and final["eth_src"].upper() == v.GW_MAC


def test_una_ip_de_prueba_inexistente_se_descarta():
    r = armar(produccion() + v.flujos(LINKS))
    res = desde(r, H1, paquete_ip(H1, {"ip": "10.99.2.77"}))
    assert res["entregado"] == {} and not res["inunda"]


# ---------- NO altera producción ----------
def test_el_trafico_de_produccion_se_comporta_igual_con_y_sin_los_flujos_de_prueba():
    sin, con = armar(produccion()), armar(produccion() + v.flujos(LINKS))
    for h in v.HOSTS:
        arp = {"eth_src": h["mac"], "eth_dst": "ff:ff:ff:ff:ff:ff", "eth_type": 0x0806, "vlan": None, "arp_op": 1, "arp_sha": h["mac"], "arp_spa": "10.0.0.5",
               "arp_tha": "00:00:00:00:00:00", "arp_tpa": "10.0.0.10"}                                                   # un ARP de producción (IP que NO es de prueba)
        a, b = desde(sin, h, arp), desde(con, h, arp)
        assert a["entregado"] == b["entregado"] and a["llegadas"] == b["llegadas"] and a["controlador"] == b["controlador"]
    for origen, destino in itertools.permutations(v.HOSTS, 2):
        uni = paquete_ip(origen, destino, dst_mac=destino["mac"])                                                        # IPv4 unicast de producción: MAC destino = la del host
        uni["ip_dst"] = "10.0.0.77"
        a, b = desde(sin, origen, uni), desde(con, origen, uni)
        assert a["entregado"] == b["entregado"] == {destino["nombre"]: 1} and a["llegadas"] == b["llegadas"]


def test_un_arp_casi_igual_al_del_gateway_de_prueba_no_se_desvia():
    con = armar(produccion() + v.flujos(LINKS))
    res = desde(con, H1, arp_peticion({"mac": H1["mac"], "ip": H1["ip"]}, "10.99.1.253"))                                # casi, pero no es el gateway
    assert all(p["arp_op"] == 1 for ps in res["paquetes"].values() for p in ps)                                         # nadie le contesta como gateway
