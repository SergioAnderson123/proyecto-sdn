"""Generador de policy.local.yaml de la topología de 14 VM y activación del reenvío proactivo de a un switch."""
import json
import os
import sys

import pytest
import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import generar_politica_local as g  # noqa: E402
import policy_tables as pt  # noqa: E402
from plan_arboles import NOMBRES  # noqa: E402
from test_caminos import LINKS as _LINKS_PRUEBA  # noqa: E402,F401

POR = {v: k for k, v in NOMBRES.items()}
B, C1, C2, A1, A2 = (POR[x] for x in ("borde", "core-1", "core-2", "acc-1", "acc-2"))


def enlace(a, pa, b, pb):
    def un(x, px, y, py):
        return {"src": {"device": x, "port": str(px)}, "dst": {"device": y, "port": str(py)}, "state": "ACTIVE"}
    return [un(a, pa, b, pb), un(b, pb, a, pa)]


# Los 7 cables de la topología real (ver TOPOLOGIA.md): puertos de ONOS de cada extremo.
LINKS = (enlace(B, 2, C1, 6) + enlace(B, 3, C2, 6) + enlace(C1, 4, C2, 4) + enlace(A1, 2, C1, 2) + enlace(A1, 3, C2, 3)
         + enlace(A2, 2, C1, 3) + enlace(A2, 3, C2, 2))
BASE = yaml.safe_load(open(os.path.join(os.path.dirname(AQUI), "policy.yaml"), encoding="utf-8"))


def generar(**kw):
    return g.construir(LINKS, BASE, "10.0.0.10", "52:54:00:aa:bb:01", **kw)


def test_la_confianza_son_los_troncales_y_en_los_cores_ademas_el_puerto_del_server():
    t = generar()["trusted"]
    assert t[A1] == [2, 3] and t[A2] == [2, 3] and t[B] == [2, 3]
    assert t[C1] == [2, 3, 4, 5, 6] and t[C2] == [2, 3, 4, 5, 6]            # 2,3 accesos; 4 el otro core; 5 el server; 6 el borde
    assert 1 not in t[A1] and 1 not in t[B]                                  # los puertos de equipos (host-jhon, usuarios) NO son de confianza


def test_los_cores_no_tienen_usuarios_y_jhon_es_externo_y_el_portal_va_en_acceso_y_borde():
    d = generar()
    assert d["sin_usuarios"] == sorted([C1, C2]) and d["puertos_externos"] == {B: [1]}
    assert d["portal"]["switches"] == sorted([A1, A2, B]) and d["portal"]["desvios"] == []
    assert d["portal"]["ip"] == "10.0.0.10" and d["portal"]["puerto"] == 8443 and d["portal"]["mac"] == "52:54:00:aa:bb:01"


def test_todos_los_servicios_apuntan_a_la_ip_unica_y_conservan_sus_puertos():
    d = generar()
    assert set(d["services"]) == set(BASE["services"])
    for n, s in d["services"].items():
        assert s["ip"] == "10.0.0.10" and s["tcp"] == BASE["services"][n]["tcp"]


def test_con_la_red_incompleta_o_un_switch_ausente_no_genera_nada():
    with pytest.raises(ValueError):
        g.construir(LINKS[2:], BASE, "10.0.0.10", "52:54:00:aa:bb:01")        # falta un cable
    with pytest.raises(ValueError):
        g.construir(LINKS, BASE, "10.0.0.10", "52:54:00:aa:bb:01", server={C1: [4]})   # el puerto del server sería un cable entre switches
    with pytest.raises(ValueError):
        g.construir(LINKS, BASE, "10.0.0.10", "52:54:00:aa:bb:01", externos={C1: [4]})  # un puerto externo no puede ser de confianza


def test_los_parametros_de_texto():
    assert g.parsear_puertos("core-1:5,core-2:5") == {C1: [5], C2: [5]}
    assert g.parsear_puertos("") == {}
    assert g.parsear_reenvio("normal") == "normal" and g.parsear_reenvio("Proactivo") == "proactivo"
    assert g.parsear_reenvio("core-1, borde") == sorted([C1, B])
    for mal in ("nube:1", "core-1"):
        with pytest.raises(ValueError):
            g.parsear_puertos(mal)
    with pytest.raises(ValueError):
        g.parsear_reenvio("nube")


def test_el_yaml_generado_se_puede_leer_y_la_politica_resultante_sale_bien(tmp_path):
    (tmp_path / "policy.yaml").write_text(open(os.path.join(os.path.dirname(AQUI), "policy.yaml"), encoding="utf-8").read(), encoding="utf-8")
    (tmp_path / "policy.local.yaml").write_text(g.a_yaml(generar(reenvio="proactivo")), encoding="utf-8")
    p = pt.cargar_politica(str(tmp_path / "policy.yaml"))
    assert p["reenvio"] == "proactivo" and p["trusted"][A1] == [2, 3] and p["puertos_externos"] == {B: [1]}
    assert all(s["ip"] == "10.0.0.10" for s in p["services"].values()) and p["portal"]["puerto"] == 8443
    assert pt.flujos_dispositivo(p, A1) and pt.flujos_dispositivo(p, C1)


# ---------- activar de a un switch ----------
def _usa_normal(flujos):
    return any(i.get("port") == "NORMAL" for f in flujos for i in f["treatment"]["instructions"])


def _usa_tabla_60(flujos):
    return any(i.get("type") == "TABLE" and i.get("tableId") == 60 for f in flujos for i in f["treatment"]["instructions"])


def _politica(reenvio):
    p = pt.cargar_politica()
    p.update(generar(reenvio=reenvio))
    return p


def test_con_una_lista_solo_esos_switches_usan_la_tabla_60_y_los_demas_siguen_con_normal():
    p = _politica([C1, B])
    for d in (C1, B):
        assert _usa_tabla_60(pt.flujos_dispositivo(p, d)) and not _usa_normal(pt.flujos_dispositivo(p, d)), NOMBRES[d]
    for d in (C2, A1, A2):
        assert _usa_normal(pt.flujos_dispositivo(p, d)) and not any(f["priority"] == 40100 for f in pt.flujos_dispositivo(p, d)), NOMBRES[d]


def test_la_lista_vacia_equivale_a_normal_y_proactivo_a_todos():
    normal, todos, vacia = _politica("normal"), _politica("proactivo"), _politica([])
    for d in NOMBRES:
        assert json.dumps(pt.flujos_dispositivo(vacia, d), sort_keys=True) == json.dumps(pt.flujos_dispositivo(normal, d), sort_keys=True)
        assert _usa_tabla_60(pt.flujos_dispositivo(todos, d))
    assert pt.reenvio_proactivo(_politica([C1])) is False                  # la comprobación global solo es cierta con la palabra proactivo
