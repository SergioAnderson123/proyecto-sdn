"""Pruebas de caminos.py: árboles sin lazos sobre la topología de 14 VM (5 switches con lazos), sin red ni ONOS."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caminos as c  # noqa: E402

B, C1, C2, A1, A2 = "borde", "core1", "core2", "acc1", "acc2"


def enlace(a, pa, b, pb):
    """Un cable entre a (puerto pa) y b (puerto pb) = dos entradas, como en GET /onos/v1/links."""
    return [{"src": {"device": a, "port": str(pa)}, "dst": {"device": b, "port": str(pb)}},
            {"src": {"device": b, "port": str(pb)}, "dst": {"device": a, "port": str(pa)}}]


# La topología con lazos: borde a los dos cores, core-core, y cada acceso a los dos cores.
LINKS = (enlace(B, 1, C1, 9) + enlace(B, 2, C2, 9) + enlace(C1, 7, C2, 7)
         + enlace(C1, 5, A1, 5) + enlace(C1, 6, A2, 6) + enlace(C2, 6, A1, 6) + enlace(C2, 5, A2, 5))
PREF = [C1, C2]


def test_el_grafo_usa_los_dos_sentidos_y_ordena_de_forma_estable():
    g = c.grafo_desde_links(LINKS)
    assert set(g) == {B, C1, C2, A1, A2}
    assert (C1, 7) in g[C2] or (C2, 7) in g[C1]
    assert g == c.grafo_desde_links(list(reversed(LINKS)))               # el orden de entrada no cambia el resultado


def test_hacia_el_core_1_cada_switch_tiene_un_solo_puerto_de_salida_y_no_hay_lazos():
    arbol = c.arbol_hacia(c.grafo_desde_links(LINKS), C1, PREF)
    assert set(arbol) == {B, C2, A1, A2}                                    # todos menos el destino, y cada uno UNA vez
    assert arbol[A1] == (C1, 5) and arbol[A2] == (C1, 6)                   # los accesos suben directo a core-1, no dan la vuelta por core-2
    assert arbol[B] == (C1, 1) and arbol[C2] == (C1, 7)
    assert c.es_arbol(arbol, C1)


def test_hacia_cualquier_destino_el_resultado_es_un_arbol_aunque_la_topologia_tenga_lazos():
    g = c.grafo_desde_links(LINKS)
    for destino in (B, C1, C2, A1, A2):
        arbol = c.arbol_hacia(g, destino, PREF)
        assert len(arbol) == 4 and c.es_arbol(arbol, destino), destino      # 5 switches -> 4 aristas: sin ciclos


def test_si_cae_el_enlace_directo_el_acceso_da_la_vuelta_por_el_otro_core():
    sin_a1_c1 = [e for e in LINKS if not ({e["src"]["device"], e["dst"]["device"]} == {C1, A1})]
    arbol = c.arbol_hacia(c.grafo_desde_links(sin_a1_c1), C1, PREF)
    assert arbol[A1] == (C2, 6) and arbol[C2] == (C1, 7)                   # acc1 -> core2 -> core1
    assert c.camino(arbol, A1, C1) == [A1, C2, C1] and c.es_arbol(arbol, C1)


def test_un_enlace_caido_en_un_solo_sentido_no_se_usa():
    medio = [e for e in LINKS if not (e["src"]["device"] == A1 and e["dst"]["device"] == C1)]    # acc1 -> core1 caído, core1 -> acc1 vivo
    arbol = c.arbol_hacia(c.grafo_desde_links(medio), C1, PREF)
    assert arbol[A1][0] == C2                                               # para llegar a core-1 desde acc1 hay que usar la salida que sí funciona


def test_un_dispositivo_sin_camino_no_figura_y_camino_devuelve_none():
    solo_acc2 = [e for e in LINKS if A1 not in (e["src"]["device"], e["dst"]["device"])]
    arbol = c.arbol_hacia(c.grafo_desde_links(solo_acc2), C1, PREF)
    assert A1 not in arbol and c.camino(arbol, A1, C1) is None


def test_el_desempate_prefiere_el_core_indicado():
    """Un acceso conectado a los dos cores, destino en el acceso 2: el camino desde el borde pasa por core-1 si es el preferido."""
    g = c.grafo_desde_links(LINKS)
    por_c1 = c.arbol_hacia(g, A2, [C1, C2])
    por_c2 = c.arbol_hacia(g, A2, [C2, C1])
    assert c.camino(por_c1, B, A2) == [B, C1, A2] and c.camino(por_c2, B, A2) == [B, C2, A2]


def test_enlaces_paralelos_conservan_el_de_menor_puerto():
    g = c.grafo_desde_links(enlace(C1, 8, C2, 8) + enlace(C1, 7, C2, 7))
    assert (C2, 7) in g[C1] and (C2, 8) not in g[C1]


def test_es_arbol_detecta_un_ciclo():
    ciclo = {A1: (A2, 1), A2: (A1, 1)}
    assert not c.es_arbol(ciclo, C1)


def test_flujos_que_inundan_encuentra_normal_flood_y_all_y_deja_pasar_la_salida_explicita():
    def f(puerto):
        return {"treatment": {"instructions": [{"type": "L2MODIFICATION", "subtype": "VLAN_POP"}, {"type": "OUTPUT", "port": puerto}]}}
    assert len(c.flujos_que_inundan([f("NORMAL"), f("FLOOD"), f("all"), f("CONTROLLER"), f("5"), f("IN_PORT")])) == 3
    assert c.flujos_que_inundan([f("5"), f("CONTROLLER")]) == []
    assert c.flujos_que_inundan([{"treatment": {"instructions": []}}]) == []               # DROP explícito: no inunda
