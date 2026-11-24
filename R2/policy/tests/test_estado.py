"""El /estado del vigilante (estado.py + su integración): topología con el uso de cada cable, historial de eventos (cable caído o restaurado, cambio detectado, aplicado, red partida) y su
publicación por HTTP. Con los datos REALES de la slice (14 enlaces de ONOS, plan de subredes) y con el ONOS simulado de las pruebas del vigilante. Solo registra: no cambia ninguna decisión."""
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
sys.path.insert(0, AQUI)
import capa3 as l3  # noqa: E402
import estado as es  # noqa: E402
import plan_arboles as pa  # noqa: E402
import recalculo as rc  # noqa: E402
import vigilante as vg  # noqa: E402
from test_caminos import LINKS, A1, C1, C2  # noqa: E402
from test_vigilante import nuevo, cae_el_cable_core1_acc1  # noqa: E402

N = lambda d: pa.NOMBRES.get(d, d)                                                              # noqa: E731
POR = {v: k for k, v in pa.NOMBRES.items()}


def _real(quitar=()):
    """Grafo vivo, dispositivos y plan reales; `quitar` = nombres de switches caídos."""
    links = json.load(open(os.path.join(RAIZ, "links.json"), encoding="utf-8"))["links"]
    disp = [{"id": d, "available": N(d) not in quitar} for d in pa.NOMBRES]
    plan = l3.cargar_plan(json.load(open(os.path.join(RAIZ, "planes", "vnrt_topologia14.json"), encoding="utf-8")), nombres=pa.NOMBRES)
    return links, disp, plan


def _topologia(links, disp, plan, conocidos=None):
    g = rc.grafo_vivo(links, disp)
    raiz = rc.raiz_viva(g, pa.PREFERIDOS)
    con = {} if conocidos is None else conocidos
    es.recordar_enlaces(con, g)
    return es.construir(g, con, disp, raiz, es.destinos_de_arbol(plan, raiz), pa.PREFERIDOS, N, plan.salida.dispositivo if plan.salida else None), g, con


# ---------- topología con datos reales ----------
def test_la_red_sana_tiene_7_cables_todos_en_uso_y_la_raiz_es_core_1():
    top, _, _ = _topologia(*_real())
    assert top["raiz"]["nombre"] == "core-1" and top["conexa"] and top["aislados"] == []
    assert len(top["enlaces"]) == 7 and {e["estado"] for e in top["enlaces"]} == {"arbol"}
    cable = next(e for e in top["enlaces"] if e["a_nombre"] == "acc-1" and e["b_nombre"] == "core-1")
    assert (cable["a_puerto"], cable["b_puerto"]) == (2, 2)


def test_cada_switch_trae_su_nivel_y_su_padre_en_el_arbol_hacia_la_raiz():
    top, _, _ = _topologia(*_real())
    s = {x["nombre"]: x for x in top["switches"]}
    assert s["core-1"]["es_raiz"] and s["core-1"]["nivel"] == 0 and s["core-1"]["padre"] is None
    for nombre in ("core-2", "acc-1", "acc-2", "borde"):
        assert s[nombre]["nivel"] == 1 and s[nombre]["padre"]["nombre"] == "core-1" and s[nombre]["disponible"]


def test_cada_switch_trae_su_capa_para_dibujarlo_cores_arriba_accesos_abajo_y_el_borde_aparte():
    top, _, _ = _topologia(*_real())
    assert {s["nombre"]: s["capa"] for s in top["switches"]} == {"core-1": "nucleo", "core-2": "nucleo", "acc-1": "acceso", "acc-2": "acceso", "borde": "borde"}


def test_la_capa_sale_de_la_configuracion_y_no_de_los_nombres_ni_de_la_raiz_viva():
    links, disp, plan = _real()
    g = rc.grafo_vivo(links, disp)
    raiz = rc.raiz_viva(g, pa.PREFERIDOS)
    renombrado = es.construir(g, {}, disp, raiz, [raiz], pa.PREFERIDOS, lambda d: "sw-" + d[-4:], plan.salida.dispositivo)
    assert sorted(s["capa"] for s in renombrado["switches"]) == ["acceso", "acceso", "borde", "nucleo", "nucleo"]
    sin_salida = es.construir(g, {}, disp, raiz, [raiz], pa.PREFERIDOS, N)                  # sin salida al exterior el borde es un acceso más
    assert {s["nombre"]: s["capa"] for s in sin_salida["switches"]}["borde"] == "acceso"
    top, _, _ = _topologia(links, [dict(d, available=N(d["id"]) != "core-1") for d in disp], plan)
    assert {s["nombre"]: s["capa"] for s in top["switches"]}["core-1"] == "nucleo"             # aunque caiga, sigue siendo del núcleo: el dibujo no se mueve


def test_un_cable_caido_sigue_apareciendo_como_caido_con_sus_puertos_gracias_a_la_memoria():
    links, disp, plan = _real()
    _, _, con = _topologia(links, disp, plan)                                                    # la red sana: se aprenden los 7 cables
    menos = [e for e in links if not ({e["src"]["device"], e["dst"]["device"]} == {POR["core-1"], POR["acc-1"]})]
    top, _, _ = _topologia(menos, disp, plan, con)
    caidos = [e for e in top["enlaces"] if e["estado"] == "caido"]
    assert len(caidos) == 1 and (caidos[0]["a_nombre"], caidos[0]["b_nombre"], caidos[0]["a_puerto"], caidos[0]["b_puerto"]) == ("acc-1", "core-1", 2, 2)
    assert len(top["enlaces"]) == 7 and top["conexa"]
    acc1 = next(x for x in top["switches"] if x["nombre"] == "acc-1")
    assert acc1["padre"]["nombre"] == "core-2"                                                   # sale por el otro core


def test_si_cae_core_1_la_raiz_pasa_a_core_2_y_sus_cables_salen_caidos():
    links, disp, plan = _real()
    _, _, con = _topologia(links, disp, plan)
    top, _, _ = _topologia(links, [dict(d, available=N(d["id"]) != "core-1") for d in disp], plan, con)
    assert top["raiz"]["nombre"] == "core-2" and top["conexa"]
    assert next(x for x in top["switches"] if x["nombre"] == "core-1")["disponible"] is False
    assert {e["estado"] for e in top["enlaces"] if "core-1" in (e["a_nombre"], e["b_nombre"])} == {"caido"}


def test_la_red_partida_lista_los_aislados():
    links, disp, plan = _real()
    solo = [e for e in links if POR["acc-1"] not in (e["src"]["device"], e["dst"]["device"])]
    top, _, _ = _topologia(solo, disp, plan)
    assert not top["conexa"] and [a["nombre"] for a in top["aislados"]] == ["acc-1"]


def test_un_cable_vivo_que_ningun_arbol_usa_es_de_reserva():
    g = {"A": [("B", 1), ("C", 2)], "B": [("A", 1), ("C", 2)], "C": [("A", 2), ("B", 3)]}
    con = {}
    es.recordar_enlaces(con, g)
    top = es.construir(g, con, [{"id": i} for i in g], "A", ["A"], [])
    estados = {(e["a"], e["b"]): e["estado"] for e in top["enlaces"]}
    assert estados == {("A", "B"): "arbol", ("A", "C"): "arbol", ("B", "C"): "reserva"}


def test_los_destinos_de_los_arboles_son_la_raiz_las_subredes_y_la_salida_sin_repetir():
    _, _, plan = _real()
    ds = es.destinos_de_arbol(plan, POR["core-1"])
    assert [N(d) for d in ds] == ["core-1", "acc-1", "acc-2", "borde"]                          # core-1 (raíz y subred servicios) una sola vez; la salida es el borde, ya está
    assert es.destinos_de_arbol(None, "r") == ["r"] and es.destinos_de_arbol(None, None) == []


def test_el_estado_no_lleva_hosts_ni_mac_ni_ip():
    top, _, _ = _topologia(*_real())
    texto = json.dumps(top)
    assert '"mac"' not in texto and '"ip"' not in texto and "ipAddresses" not in texto


# ---------- eventos entre dos fotos ----------
def _foto(g):
    return (set(g), es.pares_de_enlaces(g))


def test_sin_foto_anterior_no_hay_eventos():
    _, g, _ = _topologia(*_real())
    assert es.eventos_de_topologia(None, _foto(g), N) == []


def test_un_cable_que_cae_y_vuelve_genera_sus_dos_eventos_con_nombres_y_puertos():
    links, disp, plan = _real()
    _, g0, _ = _topologia(links, disp, plan)
    menos = [e for e in links if {e["src"]["device"], e["dst"]["device"]} != {POR["core-1"], POR["acc-1"]}]
    _, g1, _ = _topologia(menos, disp, plan)
    caida = es.eventos_de_topologia(_foto(g0), _foto(g1), N)
    assert [e["tipo"] for e in caida] == ["enlace_caido"] and "acc-1/2 ↔ core-1/2" in caida[0]["detalle"]
    vuelta = es.eventos_de_topologia(_foto(g1), _foto(g0), N)
    assert [e["tipo"] for e in vuelta] == ["enlace_restaurado"]


def test_si_cae_un_switch_se_dice_del_switch_y_no_de_cada_uno_de_sus_cables():
    links, disp, plan = _real()
    _, g0, _ = _topologia(links, disp, plan)
    _, g1, _ = _topologia(links, [dict(d, available=N(d["id"]) != "core-2") for d in disp], plan)
    ev = es.eventos_de_topologia(_foto(g0), _foto(g1), N)
    assert [e["tipo"] for e in ev] == ["switch_caido"] and "core-2" in ev[0]["detalle"]
    assert [e["tipo"] for e in es.eventos_de_topologia(_foto(g1), _foto(g0), N)] == ["switch_restaurado"]


def test_sin_cambios_no_hay_eventos():
    _, g, _ = _topologia(*_real())
    assert es.eventos_de_topologia(_foto(g), _foto(g), N) == []


# ---------- historial ----------
def test_el_historial_devuelve_lo_mas_nuevo_primero_y_tiene_tope():
    h = es.Historial(maximo=5)
    for i in range(1, 9):
        h.agregar(i, "aplicado", f"n{i}")
    assert [e["detalle"] for e in h.recientes()] == ["n8", "n7", "n6", "n5", "n4"]
    assert len(h.recientes(2)) == 2 and len(h.recientes(0)) == 1
    assert h.agregar(9, "aplicado", "x", {"borde": {"quitar": 0, "agregar": 4}})["cambios"] == {"borde": {"quitar": 0, "agregar": 4}}


def test_los_cambios_se_cuentan_por_switch_con_su_nombre():
    dif = {"borrar": [{"deviceId": POR["core-1"]}], "agregar": [{"deviceId": POR["borde"]}] * 4 + [{"deviceId": POR["core-1"]}]}
    assert es.cambios_por_switch(dif, N) == {"borde": {"quitar": 0, "agregar": 4}, "core-1": {"quitar": 1, "agregar": 1}}


# ---------- integración con el vigilante ----------
def tipos(v):
    return [e["tipo"] for e in v.estado_json()["historial"]]


def test_antes_del_primer_ciclo_el_estado_existe_y_dice_que_no_hay_topologia_aun():
    v, *_ = nuevo()
    e = v.estado_json()
    assert e["topologia"] is None and e["historial"] == [] and e["rol"] == "principal" and e["modo"] == "capa2" and e["estado"] == "iniciando"


def test_tras_un_ciclo_sano_hay_topologia_y_ningun_evento():
    v, *_ = nuevo()
    v.ciclo()
    e = v.estado_json()
    assert e["topologia"]["conexa"] and e["topologia"]["raiz"] and e["historial"] == []
    assert {x["estado"] for x in e["topologia"]["enlaces"]} <= {"arbol", "reserva"} and len(e["topologia"]["enlaces"]) == len(LINKS) // 2


def test_cae_un_cable_se_ve_caido_y_el_historial_cuenta_la_caida_la_deteccion_y_el_recalculo():
    v, onos, reloj, _ = nuevo()
    v.ciclo()
    cae_el_cable_core1_acc1(onos)
    assert v.ciclo() == "esperando"
    assert tipos(v) == ["cambio_detectado", "enlace_caido"]                                       # lo más nuevo primero
    assert [x["estado"] for x in v.estado_json()["topologia"]["enlaces"]].count("caido") == 1
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"
    ev = v.estado_json()["historial"]
    assert ev[0]["tipo"] == "aplicado" and ev[0]["cambios"] and all({"quitar", "agregar"} == set(c) for c in ev[0]["cambios"].values())
    assert ev[1]["tipo"] == "cambio_detectado" and "tras una caída" in ev[1]["detalle"]


def test_si_el_cable_vuelve_se_registra_la_restauracion_y_el_cable_deja_de_estar_caido():
    v, onos, reloj, _ = nuevo()
    v.ciclo()
    cae_el_cable_core1_acc1(onos)
    v.ciclo()
    onos.enlaces = list(LINKS)
    v.ciclo()
    assert "enlace_restaurado" in tipos(v) and [x["estado"] for x in v.estado_json()["topologia"]["enlaces"]].count("caido") == 0


def test_la_red_partida_y_su_recuperacion_quedan_en_el_historial_una_sola_vez():
    v, onos, reloj, _ = nuevo()
    v.ciclo()
    onos.enlaces = [e for e in LINKS if A1 not in (e["src"]["device"], e["dst"]["device"])]
    assert v.ciclo() == "partida" and v.ciclo() == "partida"
    assert tipos(v).count("red_partida") == 1 and not v.estado_json()["topologia"]["conexa"]
    onos.enlaces = list(LINKS)
    v.ciclo()
    assert tipos(v).count("red_conexa") == 1


def test_un_fallo_al_armar_el_estado_no_cambia_lo_que_hace_el_vigilante(monkeypatch):
    v, onos, reloj, logs = nuevo()
    monkeypatch.setattr(es, "construir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("roto")))
    assert v.ciclo() == "sin cambios"
    cae_el_cable_core1_acc1(onos)
    assert v.ciclo() == "esperando"
    reloj.pasar(16)
    assert v.ciclo() == "aplicado" and onos.borrados > 0
    assert sum("no se pudo actualizar /estado" in x for x in logs) == 1                           # avisa una sola vez


def test_el_estado_no_filtra_ni_las_mac_ni_las_ip_de_los_hosts():
    v, onos, *_ = nuevo()
    v.ciclo()
    texto = json.dumps(v.estado_json())
    for h in onos.hosts:
        assert h["mac"].lower() not in texto.lower()
    assert "ipAddresses" not in texto


def test_el_vigilante_publica_la_capa_de_cada_switch_segun_sus_preferidos():
    v, *_ = nuevo()
    v.ciclo()
    capas = {s["id"]: s["capa"] for s in v.estado_json()["topologia"]["switches"]}
    assert capas[C1] == capas[C2] == "nucleo" and capas[A1] == "acceso" and set(capas.values()) <= {"nucleo", "acceso", "borde"}


def test_el_estado_dice_que_es_el_respaldo_y_que_no_esta_activo_si_el_principal_vive():
    v, *_ = nuevo(salud_principal=lambda: True)
    v.ciclo()
    e = v.estado_json()
    assert e["rol"] == "respaldo" and e["activo"] is False


# ---------- publicación por HTTP ----------
def _pedir(puerto, ruta):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}{ruta}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture()
def servidores():
    lista = []
    yield lista
    for s in lista:
        s.shutdown()
        s.server_close()


def test_salud_sigue_igual_y_estado_se_sirve_solo_si_se_pide(servidores):
    v, *_ = nuevo()
    v.ciclo()
    con = vg.servir_salud(0, lambda: {"ultimo_ciclo": v.ultimo_ciclo}, v.estado_json)
    sin = vg.servir_salud(0, lambda: {"ultimo_ciclo": 1})
    servidores += [con, sin]
    p_con, p_sin = con.server_address[1], sin.server_address[1]
    c, cuerpo = _pedir(p_con, "/estado")
    assert c == 200 and json.loads(cuerpo)["topologia"]["conexa"]
    assert _pedir(p_con, "/estado?limite=3")[0] == 200
    assert _pedir(p_con, "/salud")[0] == 200 and "ultimo_ciclo" in json.loads(_pedir(p_con, "/salud")[1])
    assert _pedir(p_sin, "/estado")[0] == 404                                                  # sin estado_json no existe la ruta
    assert _pedir(p_sin, "/salud")[0] == 200
    assert _pedir(p_con, "/otra")[0] == 404


def test_si_armar_el_estado_falla_el_servidor_contesta_500_y_sigue_vivo(servidores):
    def malo():
        raise RuntimeError("roto")
    srv = vg.servir_salud(0, lambda: {"ultimo_ciclo": 1}, malo)
    servidores.append(srv)
    p = srv.server_address[1]
    assert _pedir(p, "/estado")[0] == 500 and _pedir(p, "/salud")[0] == 200
