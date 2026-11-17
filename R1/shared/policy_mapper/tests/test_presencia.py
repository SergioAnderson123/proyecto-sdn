"""Sesiones de equipos ausentes: POST /sessions/ausente (aviso de la app DHCP de ONOS tras el sondeo ARP) y el cierre de sesiones cuyo puerto perdio el enlace."""
import asyncio
import os
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventos  # noqa: E402
import policy_mapper as pm  # noqa: E402
from session_manager import ActiveSession, session_store  # noqa: E402

MAC = "fa:16:3e:b6:96:08"
DPID = "of:0000e6925d87354b"
CLAVE = "clave-presencia"


@pytest.fixture
def cliente(monkeypatch):
    borrados = []

    async def eliminar(dpid, flow_id):
        borrados.append((dpid, flow_id))
    monkeypatch.setattr(pm, "eliminar_flujo_onos", eliminar)
    monkeypatch.setattr(pm, "PRESENCIA_TOKEN", CLAVE)
    session_store.remove_session(MAC)
    eventos.limpiar()
    yield TestClient(pm.app), borrados
    session_store.remove_session(MAC)


def _sesion(metodo="802.1X", base=None, puerto="1"):
    s = ActiveSession(username="profesor01@example.org", mac=MAC, dpid=DPID, port=puerto, vlan=10, rol="Docente", flow_id="flujo-1", metodo=metodo, base=base)
    session_store.register_session(s)
    return s


def _ausente(http, clave=CLAVE, mac=MAC):
    return http.post("/sessions/ausente", json={"mac": mac}, headers={"X-Presencia-Token": clave} if clave is not None else {})


def test_cierra_la_sesion_y_su_flujo_sin_lista_negra(cliente, monkeypatch):
    http, borrados = cliente
    negra = []

    async def lista_negra(*a, **k):
        negra.append(a)
    monkeypatch.setattr(pm, "inyectar_blacklist_temporal", lista_negra)
    _sesion()
    r = _ausente(http)
    assert r.status_code == 200 and r.json() == {"status": "cerrada", "usuario": "profesor01@example.org"}
    assert session_store.get_by_mac(MAC) is None and borrados == [(DPID, "flujo-1")] and not negra


def test_cierra_tambien_una_sesion_del_portal_y_un_ascenso_sin_restaurar_el_rol_base(cliente):
    http, borrados = cliente
    base = ActiveSession(username="alumno01@example.org", mac=MAC, dpid=DPID, port="1", vlan=20, rol="Alumno", flow_id="base")
    _sesion(metodo="Portal", base=base)
    assert _ausente(http).json()["status"] == "cerrada"
    assert session_store.get_by_mac(MAC) is None and borrados == [(DPID, "flujo-1")]      # solo el flujo del ascenso; el base no se reinstala


@pytest.mark.parametrize("clave", [None, "", "otra"])
def test_sin_la_clave_correcta_no_cierra_nada(cliente, clave):
    http, borrados = cliente
    _sesion()
    assert _ausente(http, clave=clave).status_code == 403
    assert session_store.get_by_mac(MAC) is not None and not borrados


def test_sin_clave_configurada_esta_desactivado_y_la_del_agente_no_vale(cliente, monkeypatch):
    http, borrados = cliente
    _sesion()
    monkeypatch.setattr(pm, "PRESENCIA_TOKEN", "")
    assert _ausente(http, clave="").status_code == 403
    monkeypatch.setattr(pm, "PRESENCIA_TOKEN", CLAVE)
    monkeypatch.setattr(pm, "AGENTE_TOKEN", "clave-agente")
    assert http.post("/sessions/ausente", json={"mac": MAC}, headers={"X-Agente-Token": "clave-agente"}).status_code == 403
    assert session_store.get_by_mac(MAC) is not None and not borrados


def test_una_mac_sin_sesion_no_es_un_error(cliente):
    http, borrados = cliente
    assert _ausente(http).json() == {"status": "sin_sesion"} and not borrados


def test_acepta_la_mac_con_guiones_y_en_mayusculas(cliente):
    http, borrados = cliente
    _sesion()
    assert _ausente(http, mac="FA-16-3E-B6-96-08").json()["status"] == "cerrada" and borrados


# ---- puertos sin enlace ----
def test_puerto_sin_enlace_se_cierra_a_la_segunda_lectura():
    s = _sesion()
    try:
        cerrar, cont = pm.macs_con_puerto_caido([s], {DPID: {"1": False}}, {}, 2)
        assert cerrar == [] and cont == {MAC: 1}
        cerrar, cont = pm.macs_con_puerto_caido([s], {DPID: {"1": False}}, cont, 2)
        assert cerrar == [MAC]
    finally:
        session_store.remove_session(MAC)


def test_un_puerto_que_vuelve_reinicia_la_cuenta():
    s = _sesion()
    try:
        _, cont = pm.macs_con_puerto_caido([s], {DPID: {"1": False}}, {}, 2)
        cerrar, cont = pm.macs_con_puerto_caido([s], {DPID: {"1": True}}, cont, 2)
        assert cerrar == [] and cont == {}
    finally:
        session_store.remove_session(MAC)


def test_lo_que_onos_no_lista_no_se_toca():
    s = _sesion()
    try:
        assert pm.macs_con_puerto_caido([s], {}, {MAC: 5}, 2) == ([], {})                       # switch desconocido: no se sabe nada de el
        assert pm.macs_con_puerto_caido([s], {DPID: {"9": False}}, {MAC: 5}, 2) == ([], {})     # puerto no listado
    finally:
        session_store.remove_session(MAC)


def test_el_lazo_cierra_la_sesion_de_un_puerto_sin_enlace(monkeypatch):
    cerradas = []

    async def eliminar(dpid, flow_id):
        cerradas.append(flow_id)

    async def puertos(dpids):
        return {DPID: {"1": False}}
    monkeypatch.setattr(pm, "eliminar_flujo_onos", eliminar)
    monkeypatch.setattr(pm, "_puertos_sin_enlace", puertos)
    monkeypatch.setattr(pm, "PUERTO_REVISION_SEG", 0)
    monkeypatch.setattr(pm, "PUERTO_CAIDO_VECES", 2)
    pm._puerto_caido_veces = {}
    _sesion()

    async def correr():
        tarea = asyncio.create_task(pm._vigilar_puertos_de_sesion())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if session_store.get_by_mac(MAC) is None:
                break
        tarea.cancel()
    asyncio.run(correr())
    assert session_store.get_by_mac(MAC) is None and cerradas == ["flujo-1"]
