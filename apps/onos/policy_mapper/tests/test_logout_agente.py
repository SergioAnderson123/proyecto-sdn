"""POST /sessions/logout: el agente del host cierra su sesion 802.1X.

ONOS AAA 2.7.0 pasa de AUTHORIZED a IDLE al recibir el EAPOL-Logoff pero no envia Accounting-Request a RADIUS (sus contadores no
tienen accounting): el aviso del agente es el unico cierre de una sesion 802.1X en el Policy Mapper.
"""
import os
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventos  # noqa: E402
import policy_mapper as pm  # noqa: E402
from session_manager import ActiveSession, session_store  # noqa: E402

MAC = "fa:16:3e:39:6e:96"
CLAVE = "clave-de-prueba"


@pytest.fixture
def cliente(monkeypatch):
    borrados = []

    async def eliminar(dpid, flow_id):
        borrados.append((dpid, flow_id))
    monkeypatch.setattr(pm, "eliminar_flujo_onos", eliminar)
    monkeypatch.setattr(pm, "AGENTE_TOKEN", CLAVE)
    session_store.remove_session(MAC)
    eventos.limpiar()
    yield TestClient(pm.app), borrados
    session_store.remove_session(MAC)


def _sesion(metodo="802.1X"):
    session_store.register_session(ActiveSession(username="profesor01@example.org", mac=MAC, dpid="of:00001a749039894a",
                                                 port="2", vlan=10, rol="Docente", flow_id="flujo-1", metodo=metodo))


def _logout(http, mac=MAC, clave=CLAVE):
    return http.post("/sessions/logout", json={"mac": mac}, headers={"X-Agente-Token": clave} if clave is not None else {})


def test_cierra_la_sesion_y_borra_su_flujo_sin_lista_negra(cliente, monkeypatch):
    http, borrados = cliente
    lista_negra = []

    async def negra(*a, **k):
        lista_negra.append(a)
    monkeypatch.setattr(pm, "inyectar_blacklist_temporal", negra)
    _sesion()
    r = _logout(http)
    assert r.status_code == 200 and r.json() == {"status": "cerrada", "usuario": "profesor01@example.org"}
    assert session_store.get_by_mac(MAC) is None
    assert borrados == [("of:00001a749039894a", "flujo-1")]
    assert not lista_negra                     # a diferencia de /sessions/revoke, no bloquea al usuario


def test_registra_el_evento_de_logout(cliente):
    http, _ = cliente
    _sesion()
    _logout(http)
    ultimo = eventos.listar()[-1] if hasattr(eventos, "listar") else None
    if ultimo is not None:
        assert ultimo["tipo"] == "logout" and ultimo["mac"] == MAC


def test_acepta_la_mac_con_guiones_y_en_mayusculas(cliente):
    http, borrados = cliente
    _sesion()
    assert _logout(http, mac="FA-16-3E-39-6E-96").json()["status"] == "cerrada"
    assert borrados


@pytest.mark.parametrize("clave", [None, "", "otra-clave"])
def test_sin_la_clave_correcta_no_cierra_nada(cliente, clave):
    http, borrados = cliente
    _sesion()
    assert _logout(http, clave=clave).status_code == 403
    assert session_store.get_by_mac(MAC) is not None and not borrados


def test_sin_clave_configurada_el_endpoint_esta_desactivado(cliente, monkeypatch):
    http, borrados = cliente
    monkeypatch.setattr(pm, "AGENTE_TOKEN", "")
    _sesion()
    assert _logout(http, clave="").status_code == 403       # una cabecera vacia no puede igualar una clave vacia
    assert session_store.get_by_mac(MAC) is not None and not borrados


def test_una_mac_sin_sesion_no_es_un_error(cliente):
    http, borrados = cliente
    assert _logout(http).json() == {"status": "sin_sesion"} and not borrados


def test_no_toca_las_sesiones_del_portal(cliente):
    http, borrados = cliente
    _sesion(metodo="Portal")
    assert _logout(http).json() == {"status": "no_es_802_1x"}
    assert session_store.get_by_mac(MAC) is not None and not borrados
