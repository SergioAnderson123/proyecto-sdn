"""Pruebas de flujos_proactivos.py sobre la topología de 14 VM (5 switches con lazos)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caminos as c  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402

DEVS = [B, C1, C2, A1, A2]
MAC1, MAC2 = "fa:16:3e:b6:96:08", "fa:16:3e:71:08:66"
HOSTS = [{"mac": MAC1, "device": A1, "port": 1}, {"mac": MAC2, "device": A2, "port": 1}]
G = c.grafo_desde_links(LINKS)


def salida(flujos, dev, mac):
    r = [f for f in flujos if f["deviceId"] == dev and f["selector"]["criteria"] == [{"type": "ETH_DST", "mac": mac.upper()}]]
    assert len(r) == 1, (dev, mac, len(r))
    return r[0]["treatment"]["instructions"][0]["port"]


def test_invariante_ningun_flujo_inunda():
    todos = fp.flujos_unicast(HOSTS, G, DEVS, PREF) + fp.flujos_descarte(DEVS)
    assert todos and c.flujos_que_inundan(todos) == []


def test_un_flujo_por_switch_y_host():
    assert len(fp.flujos_unicast(HOSTS, G, DEVS, PREF)) == len(DEVS) * len(HOSTS)


def test_puertos_siguen_el_arbol():
    f = fp.flujos_unicast(HOSTS, G, DEVS, PREF)
    assert salida(f, A1, MAC1) == "1"                       # en su propio switch: el puerto de acceso
    assert salida(f, C1, MAC1) == "5" and salida(f, B, MAC1) == "1"      # core-1 -> acc1; borde -> core-1
    assert salida(f, A2, MAC1) == "6"                       # acc2 sube a core-1 (sin pasar por core-2)


def test_siguiendo_los_flujos_desde_cualquier_switch_se_llega_al_host_sin_repetir_switch():
    f = fp.flujos_unicast(HOSTS, G, DEVS, PREF)
    por_puerto = {(a, int(p)): b for a, v in G.items() for b, p in v}
    for h in HOSTS:
        for origen in DEVS:
            actual, vistos = origen, []
            while True:
                assert actual not in vistos, ("lazo", origen, h["mac"], vistos)
                vistos.append(actual)
                p = int(salida(f, actual, h["mac"]))
                if actual == h["device"]:
                    assert p == h["port"]
                    break
                actual = por_puerto[(actual, p)]


def test_host_en_switch_desconocido_se_omite_y_sin_camino_no_hay_flujo():
    assert fp.flujos_unicast([{"mac": MAC1, "device": "of:x", "port": 1}], G, DEVS, PREF) == []
    aislado = c.grafo_desde_links([e for e in LINKS if A1 not in (e["src"]["device"], e["dst"]["device"])])
    aislado[A1] = []
    f = fp.flujos_unicast(HOSTS[:1], aislado, DEVS, PREF)
    assert [x["deviceId"] for x in f] == [A1]               # solo el switch del host: el resto no llega, descarta


def test_determinista_y_todo_en_la_tabla_de_reenvio():
    a = fp.flujos_unicast(HOSTS, G, DEVS, PREF)
    assert a == fp.flujos_unicast(list(reversed(HOSTS)), G, list(reversed(DEVS)), PREF)
    assert {x["tableId"] for x in a} == {fp.TABLA_REENVIO}


# ---- difusión ARP por árbol ----
ACCESO = {A1: [1], A2: [1], C1: [1, 8], C2: [1, 8], B: [3, 4]}      # puertos de acceso (hosts, sensores, server) de esta topología de prueba


def _enlaces_por_puerto():
    """(dispositivo, puerto) -> (vecino, puerto del vecino), con los cables de la topología."""
    m = {}
    for d, salidas in G.items():
        for vecino, p in salidas:
            for v2, p2 in G[vecino]:
                if v2 == d:
                    m[(d, p)] = (vecino, p2)
    return m


def _propagar(flujos, origen, puerto, max_saltos=50):
    """Entrega un broadcast ARP que entra por (origen, puerto). Devuelve cuántas veces llegó a cada switch y si se cortó por exceso de saltos."""
    por_puerto = _enlaces_por_puerto()
    cola = [(origen, puerto)]
    llegadas, pasos = {}, 0
    while cola:
        pasos += 1
        if pasos > max_saltos:
            return llegadas, True
        dev, entrada = cola.pop(0)
        llegadas[dev] = llegadas.get(dev, 0) + 1
        for f in flujos:
            crit = {c["type"]: c for c in f["selector"]["criteria"]}
            if f["deviceId"] == dev and crit.get("IN_PORT", {}).get("port") == entrada and crit.get("ETH_TYPE", {}).get("ethType") == "0x0806":
                for ins in f["treatment"]["instructions"]:
                    sig = por_puerto.get((dev, int(ins["port"])))
                    if sig:
                        cola.append(sig)
    return llegadas, False


def test_la_difusion_llega_una_sola_vez_a_cada_switch_desde_cualquier_puerto_de_acceso_y_termina():
    f = fp.flujos_difusion(G, C1, ACCESO, PREF)
    assert c.flujos_que_inundan(f) == []
    for dev, puertos in ACCESO.items():
        for p in puertos:
            llegadas, cortado = _propagar(f, dev, p)
            assert not cortado, (dev, p)
            assert set(llegadas) == set(DEVS), (dev, p, llegadas)               # llega a los 5
            assert all(v == 1 for v in llegadas.values()), (dev, p, llegadas)   # y una sola vez a cada uno


def test_la_difusion_no_sale_por_el_puerto_de_entrada_ni_por_enlaces_fuera_del_arbol():
    f = fp.flujos_difusion(G, C1, ACCESO, PREF)
    arbol = fp.puertos_del_arbol(G, C1, PREF)
    for x in f:
        d = x["deviceId"]
        entrada = next(cr["port"] for cr in x["selector"]["criteria"] if cr["type"] == "IN_PORT")
        salidas = {int(i["port"]) for i in x["treatment"]["instructions"]}
        assert entrada not in salidas
        assert salidas <= arbol[d] | set(ACCESO.get(d, ()))
    assert len(fp.puertos_del_arbol(G, C1, PREF)[A1]) == 1                      # acc-1 solo sube a core-1: su otro uplink queda fuera
    assert not any(cr["port"] == 6 for x in f if x["deviceId"] == A1 for cr in x["selector"]["criteria"] if cr["type"] == "IN_PORT")


def test_el_simulador_detecta_una_inundacion_real_con_lazos():
    """Sanidad del simulador: reenviando por TODOS los puertos troncales (como NORMAL) el broadcast se multiplica y se corta por saltos."""
    todos = []
    for d, salidas in G.items():
        troncales = {p for _, p in salidas} | set(ACCESO.get(d, ()))
        for e in troncales:
            todos.append(fp._base(d, fp.TABLA_REENVIO, 200, [{"type": "IN_PORT", "port": e}, {"type": "ETH_TYPE", "ethType": "0x0806"}],
                                  [{"type": "OUTPUT", "port": str(p)} for p in sorted(troncales - {e})]))
    llegadas, cortado = _propagar(todos, A1, 1)
    assert cortado or any(v > 1 for v in llegadas.values())


def test_la_difusion_sobrevive_a_la_caida_de_un_enlace():
    sin = [e for e in LINKS if {e["src"]["device"], e["dst"]["device"]} != {C1, A1}]
    g2 = c.grafo_desde_links(sin)
    global G
    original, G = G, g2
    try:
        f = fp.flujos_difusion(g2, C1, ACCESO, PREF)
        llegadas, cortado = _propagar(f, A2, 1)
        assert not cortado and set(llegadas) == set(DEVS) and all(v == 1 for v in llegadas.values())
    finally:
        G = original


def test_un_puerto_troncal_pasado_como_acceso_se_ignora():
    """Si por error se declara de acceso un puerto que es un enlace (aquí core-2/5, cable a acc-2, fuera del árbol), no se difunde por él."""
    mal = {**ACCESO, C2: [1, 8, 5], C1: [1, 8, 5]}
    f = fp.flujos_difusion(G, C1, mal, PREF)
    llegadas, cortado = _propagar(f, A1, 1)
    assert not cortado and set(llegadas) == set(DEVS) and all(v == 1 for v in llegadas.values())
