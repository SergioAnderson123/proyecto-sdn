"""Prueba del endpoint /sessions/revoke del Policy Mapper (I11, lo llama el integrador de R5). Sin ONOS: se sustituyen las llamadas REST."""
import os
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy_mapper as pm  # noqa: E402
from session_manager import ActiveSession, session_store  # noqa: E402

MAC = "00:00:00:00:00:11"


@pytest.fixture
def cliente(monkeypatch):
    llamadas = {"borrados": [], "lista_negra": []}

    async def borrar(dpid, flow_id):
        llamadas["borrados"].append((dpid, flow_id))

    async def lista_negra(dpid, port, mac, idle_timeout=300):
        llamadas["lista_negra"].append((dpid, port, mac, idle_timeout))

    monkeypatch.setattr(pm, "eliminar_flujo_onos", borrar)
    monkeypatch.setattr(pm, "inyectar_blacklist_temporal", lista_negra)
    session_store.remove_session(MAC)
    session_store.register_session(ActiveSession(username="profesor01@example.org", mac=MAC, dpid="of:0000000000000001",
                                                 port="1", vlan=10, rol="Docente", flow_id="777"))
    yield TestClient(pm.app), llamadas
    session_store.remove_session(MAC)


def test_revoca_la_sesion_borra_su_flujo_y_la_deja_en_lista_negra(cliente):
    c, llamadas = cliente
    r = c.post("/sessions/revoke", json={"mac": "00-00-00-00-00-11", "motivo": "R5", "segundos": 120})
    assert r.status_code == 200 and r.json() == {"status": "revocada", "usuario": "profesor01@example.org"}
    assert llamadas["borrados"] == [("of:0000000000000001", "777")]
    assert llamadas["lista_negra"] == [("of:0000000000000001", "1", MAC, 120)]
    assert session_store.get_by_mac(MAC) is None


def test_acepta_la_mac_con_guiones_o_dos_puntos_y_mayusculas(cliente):
    c, _ = cliente
    assert c.post("/sessions/revoke", json={"mac": MAC.upper()}).json()["status"] == "revocada"


def test_sin_sesion_no_toca_ONOS(cliente):
    c, llamadas = cliente
    r = c.post("/sessions/revoke", json={"mac": "00:00:00:00:00:99"})
    assert r.json() == {"status": "sin_sesion"}
    assert llamadas["borrados"] == [] and llamadas["lista_negra"] == []


def test_revocar_dos_veces_la_segunda_no_hace_nada(cliente):
    c, llamadas = cliente
    c.post("/sessions/revoke", json={"mac": MAC})
    assert c.post("/sessions/revoke", json={"mac": MAC}).json() == {"status": "sin_sesion"}
    assert len(llamadas["borrados"]) == 1


# ---------- bitácora de eventos (alimenta el panel de accesos) ----------
import eventos  # noqa: E402


def test_la_revocacion_queda_en_la_bitacora(cliente):
    c, _ = cliente
    eventos.limpiar()
    c.post("/sessions/revoke", json={"mac": MAC, "motivo": "R5", "segundos": 60})
    ev = c.get("/events").json()["events"]
    assert ev[0]["tipo"] == "revocado" and ev[0]["usuario"] == "profesor01@example.org" and ev[0]["mac"] == MAC
    assert "R5" in ev[0]["detalle"]


def test_events_devuelve_lo_mas_reciente_primero_y_respeta_el_limite(cliente):
    c, _ = cliente
    eventos.limpiar()
    for i in range(5):
        eventos.registrar("admitido", f"u{i}", "00:00:00:00:00:0%d" % i)
    r = c.get("/events", params={"limit": 3}).json()["events"]
    assert [e["usuario"] for e in r] == ["u4", "u3", "u2"]


def test_la_bitacora_tiene_tope():
    eventos.limpiar()
    for i in range(eventos.MAX + 25):
        eventos.registrar("logout", f"u{i}")
    assert len(eventos.listar(10_000)) == eventos.MAX
