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
