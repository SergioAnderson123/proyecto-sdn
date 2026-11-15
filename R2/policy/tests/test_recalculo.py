"""Pruebas de recalculo.py: topología viva, diferencia y, sobre todo, que el cambio nunca forma un lazo (sin red ni ONOS)."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caminos as c  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
import recalculo as rc  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402
from test_flujos_proactivos import ACCESO, HOSTS  # noqa: E402

TODOS = [B, C1, C2, A1, A2]
DISP = [{"id": d, "available": True} for d in TODOS]


def sin_cable(a, b):
    return [e for e in LINKS if {e["src"]["device"], e["dst"]["device"]} != {a, b}]


def sin_switch(x):
    return [e for e in LINKS if x not in (e["src"]["device"], e["dst"]["device"])], [d for d in DISP if d["id"] != x]


def deseados(links, disp):
    g = rc.grafo_vivo(links, disp)
    return g, rc.flujos_deseados(g, HOSTS, ACCESO, PREF)


# ---------- topología viva ----------
def test_grafo_vivo_ignora_enlaces_inactivos_switches_caidos_y_cables_de_un_solo_sentido():
    inactivo = [dict(e, state="INACTIVE") if {e["src"]["device"], e["dst"]["device"]} == {C1, A1} else e for e in LINKS]
    assert (A1, 5) not in rc.grafo_vivo(inactivo, DISP)[C1] and all(u != C1 for u, _ in rc.grafo_vivo(inactivo, DISP)[A1])
    caido = [dict(d, available=False) if d["id"] == C2 else d for d in DISP]
    g = rc.grafo_vivo(LINKS, caido)
    assert C2 not in g and all(u != C2 for v in g.values() for u, _ in v)
    un_sentido = [e for e in LINKS if not (e["src"]["device"] == A1 and e["dst"]["device"] == C1)]
    g2 = rc.grafo_vivo(un_sentido, DISP)
    assert all(u != C1 for u, _ in g2[A1]) and all(u != A1 for u, _ in g2[C1])        # el cable roto en un sentido no se usa en ninguno


def test_raiz_viva_y_conectividad():
    g = rc.grafo_vivo(LINKS, DISP)
    assert rc.raiz_viva(g, PREF) == C1 and rc.esta_conexa(g, C1)
    sin_c1 = rc.grafo_vivo(*sin_switch(C1))
    assert rc.raiz_viva(sin_c1, PREF) == C2 and rc.esta_conexa(sin_c1, C2)           # si cae core-1, la raíz pasa a core-2
    partida = rc.grafo_vivo([e for e in LINKS if A1 not in (e["src"]["device"], e["dst"]["device"])], DISP)       # acc-1 vivo pero sin cables
    assert not rc.esta_conexa(partida, C1)                                           # acc-1 sigue vivo pero aislado: no se recalcula


# ---------- diferencia ----------
def _como_onos(f, ident):
    """El mismo flujo como lo devuelve GET /flows de ONOS: ethType sin ceros, MAC en minúsculas, puertos en texto."""
    crit = []
    for cr in f["selector"]["criteria"]:
        if cr["type"] == "ETH_TYPE":
            crit.append({"type": "ETH_TYPE", "ethType": "0x806"})
        elif cr["type"] == "ETH_DST":
            crit.append({"type": "ETH_DST", "mac": cr["mac"].lower()})
        elif cr["type"] == "IN_PORT":
            crit.append({"type": "IN_PORT", "port": str(cr["port"])})
        else:
            crit.append(cr)
    return {"id": ident, "appId": "r2.proactivo", "deviceId": f["deviceId"], "tableId": f["tableId"], "priority": f["priority"],
            "selector": {"criteria": crit}, "treatment": {"instructions": f["treatment"]["instructions"], "deferred": []}}


def test_clave_es_igual_para_nuestro_formato_y_para_el_de_onos():
    _, des = deseados(LINKS, DISP)
    assert all(rc.clave(f) == rc.clave(_como_onos(f, str(i))) for i, f in enumerate(des))


def test_sin_cambios_la_diferencia_es_vacia():
    _, des = deseados(LINKS, DISP)
    inst = [_como_onos(f, str(i)) for i, f in enumerate(des)]
    dif = rc.diferencia(des, inst)
    assert dif == {"borrar": [], "agregar": []} and rc.resumen(dif) == "sin cambios" and rc.pasos_de_cambio(dif) == []


def test_al_caer_un_cable_solo_cambian_los_switches_afectados_y_la_diferencia_lleva_a_lo_deseado():
    _, viejos = deseados(LINKS, DISP)
    inst = [_como_onos(f, str(i)) for i, f in enumerate(viejos)]
    g2, nuevos = deseados(sin_cable(C1, A1), DISP)
    dif = rc.diferencia(nuevos, inst)
    assert dif["borrar"] and dif["agregar"]
    afectados = {f["deviceId"] for f in dif["borrar"] + dif["agregar"]}
    assert afectados and afectados <= set(TODOS)
    restantes = {rc.clave(f) for f in inst} - {rc.clave(f) for f in dif["borrar"]}
    assert restantes | {rc.clave(f) for f in dif["agregar"]} == {rc.clave(f) for f in nuevos}


def test_pasos_de_cambio_borra_antes_de_montar():
    _, viejos = deseados(LINKS, DISP)
    _, nuevos = deseados(sin_cable(C1, A1), DISP)
    pasos = rc.pasos_de_cambio(rc.diferencia(nuevos, viejos))
    assert [p[0] for p in pasos] == ["borrar", "agregar"]


# ---------- el cambio nunca forma un lazo ----------
def _fisico(grafo):
    """(dispositivo, puerto) -> (vecino, puerto del vecino) con los cables que siguen vivos."""
    m = {}
    for d, salidas in grafo.items():
        for nb, p in salidas:
            for v2, p2 in grafo[nb]:
                if v2 == d:
                    m[(d, p)] = (nb, p2)
    return m


def _difundir(flujos, fisico, origen, puerto, max_saltos=60):
    """Reparte un broadcast ARP que entra por (origen, puerto) siguiendo SOLO los flujos dados. Devuelve cuántas veces llegó a cada switch."""
    por_sw = {}
    for f in flujos:
        crit = {cr["type"]: cr for cr in f["selector"]["criteria"]}
        if f["priority"] == fp.PRIO_DIFUSION and "IN_PORT" in crit:
            por_sw[(f["deviceId"], crit["IN_PORT"]["port"])] = [int(i["port"]) for i in f["treatment"]["instructions"]]
    cola, llegadas, pasos = [(origen, puerto)], {}, 0
    while cola:
        pasos += 1
        if pasos > max_saltos:
            return llegadas, True
        dev, entrada = cola.pop(0)
        llegadas[dev] = llegadas.get(dev, 0) + 1
        for p in por_sw.get((dev, entrada), []):
            sig = fisico.get((dev, p))
            if sig:
                cola.append(sig)
    return llegadas, False


def _escenarios():
    import itertools
    cables = sorted({frozenset((e["src"]["device"], e["dst"]["device"])) for e in LINKS}, key=sorted)
    for a, b in cables:
        yield f"cable {sorted((a, b))}", sin_cable(a, b), DISP
    for x in TODOS:
        yield f"switch {x}", *sin_switch(x)
    for k1, k2 in itertools.combinations(cables, 2):                                # fallos dobles que dejan la red conexa
        links = [e for e in LINKS if frozenset((e["src"]["device"], e["dst"]["device"])) not in (k1, k2)]
        g = rc.grafo_vivo(links, DISP)
        if rc.esta_conexa(g, rc.raiz_viva(g, PREF)):
            yield f"cables {sorted(k1)} + {sorted(k2)}", links, DISP


def test_ningun_estado_intermedio_del_cambio_duplica_el_broadcast_en_ningun_escenario():
    azar = random.Random(7)
    comprobados = 0
    for nombre, links, disp in _escenarios():
        _, viejos = deseados(LINKS, DISP)
        g2, nuevos = deseados(links, disp)
        assert rc.esta_conexa(g2, rc.raiz_viva(g2, PREF)), nombre                 # cada fallo simple deja la red conexa
        dif = rc.diferencia(nuevos, viejos)
        borrar = {rc.clave(f) for f in dif["borrar"]}
        fisico = _fisico(g2)
        estados = []
        # fase 1: cualquier subconjunto de los borrados (se prueban muchos órdenes al azar)
        for _ in range(25):
            k = azar.randint(0, len(dif["borrar"]))
            quitados = {rc.clave(f) for f in azar.sample(dif["borrar"], k)}
            estados.append([f for f in viejos if rc.clave(f) not in quitados])
        # fase 2: todo borrado y un subconjunto cualquiera de los añadidos
        base = [f for f in viejos if rc.clave(f) not in borrar]
        for _ in range(25):
            k = azar.randint(0, len(dif["agregar"]))
            estados.append(base + azar.sample(dif["agregar"], k))
        estados.append(nuevos)
        origenes = [(d, p) for d, ps in ACCESO.items() if d in g2 for p in ps]
        for flujos in estados:
            for d, p in origenes:
                llegadas, cortado = _difundir(flujos, fisico, d, p)
                assert not cortado and all(v == 1 for v in llegadas.values()), (nombre, d, p, llegadas)
                comprobados += 1
        # al terminar, el broadcast llega a TODOS los switches vivos
        for d, p in origenes:
            llegadas, _ = _difundir(nuevos, fisico, d, p)
            assert set(llegadas) == set(g2), (nombre, d, p, llegadas)
    assert comprobados > 1000


def test_noaction_se_trata_como_descarte_vacio():
    """ONOS puede devolver el descarte con una instrucción NOACTION: no debe verse como un flujo distinto del nuestro (falso positivo visto en vivo)."""
    _, des = deseados(LINKS, DISP)
    descarte = [f for f in des if f["priority"] == fp.PRIO_DESCARTE][0]
    como_onos = _como_onos(descarte, "1")
    como_onos["treatment"] = {"instructions": [{"type": "NOACTION"}], "deferred": []}
    assert rc.clave(como_onos) == rc.clave(descarte)
    assert rc.diferencia([descarte], [como_onos]) == {"borrar": [], "agregar": []}


def test_describir_dice_exactamente_que_flujo_es():
    _, des = deseados(LINKS, DISP)
    d = [f for f in des if f["priority"] == fp.PRIO_DESCARTE][0]
    assert "p1" in rc.describir(d) and "descarte" in rc.describir(d)
    b = [f for f in des if f["priority"] == fp.PRIO_DIFUSION][0]
    txt = rc.describir(b, nombre=lambda x: "sw")
    assert txt.startswith("sw t60 p200") and "IN_PORT=" in txt and "salidas [" in txt
