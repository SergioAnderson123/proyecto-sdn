"""Portal cautivo con la capa 3: el portal ya no ve la MAC del cliente por ARP (el cliente esta detras del gateway), asi que manda la IP de origen de la conexion y el mapper
resuelve la MAC con los hosts de ONOS (`portal_auth.mac_de_ip`). Con MAC explicita todo sigue como antes (cliente en el mismo segmento L2 que el portal).

Caso real (23-nov-2026): el ARP del server solo tenia las IP viejas de h1/h2 y los gateways; h1 (10.0.1.100, profesor01 por 802.1X) veia el formulario en vez de su sesion, y un invitado
no podia entrar (`No se pudo identificar su equipo`)."""
import os
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventos  # noqa: E402
import policy_mapper as pm  # noqa: E402
from session_manager import ActiveSession, session_store  # noqa: E402

MAC_H1 = "fa:16:3e:b6:96:08"
IP_H1 = "10.0.1.100"
IP_H2 = "10.0.2.100"
DPID = "of:0000e6925d87354b"
TOKEN = {"X-Portal-Token": "t0ken"}


@pytest.fixture
def mundo(monkeypatch):
    r = {"inyectados": [], "hosts": {IP_H1: MAC_H1}, "preguntas_ip": []}

    async def inyectar(dpid, port, mac, vlan):
        r["inyectados"].append((dpid, str(port), mac, vlan))
        return f"flujo-{len(r['inyectados'])}"

    async def mac_de_ip(ip, onos_url, auth, reloj=None):
        r["preguntas_ip"].append(ip)
        return r["hosts"].get(ip)

    async def ubicar(mac, onos_url, auth):
        return (DPID, "1") if mac == MAC_H1 else None

    async def sin_faltantes(*a, **k):
        return []

    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", inyectar)
    monkeypatch.setattr(pm.portal_auth, "mac_de_ip", mac_de_ip)
    monkeypatch.setattr(pm.portal_auth, "ubicar_host", ubicar)
    monkeypatch.setattr(pm.portal_auth, "autenticar_ldap", lambda u, c: ["invitado"] if (u, c) == ("guest01", "guest123") else None)
    monkeypatch.setattr(pm.policy_tables, "faltantes", sin_faltantes)
    monkeypatch.setattr(pm, "PORTAL_TOKEN", "t0ken")
    session_store.remove_session(MAC_H1)
    eventos.limpiar()
    r["http"] = TestClient(pm.app)
    yield r
    session_store.remove_session(MAC_H1)


def _login(mundo, **cuerpo):
    return mundo["http"].post("/portal/login", json={"usuario": "guest01", "clave": "guest123", **cuerpo}, headers=TOKEN)


# ------------------------------------------------------------------ login
def test_un_invitado_de_otra_subred_entra_con_la_mac_que_onos_tiene_para_su_ip(mundo):
    r = _login(mundo, ip=IP_H1)
    assert r.status_code == 200 and r.json()["rol"] == "Invitado"
    assert mundo["preguntas_ip"] == [IP_H1] and mundo["inyectados"] == [(DPID, "1", MAC_H1, 50)]
    ses = session_store.get_by_mac(MAC_H1)
    assert ses.username == "guest01" and ses.metodo == "Portal" and ses.expira is not None


def test_con_mac_explicita_todo_sigue_como_antes_y_no_se_pregunta_por_la_ip(mundo):
    assert _login(mundo, mac=MAC_H1.upper().replace(":", "-")).status_code == 200
    assert mundo["preguntas_ip"] == [] and session_store.get_by_mac(MAC_H1) is not None


def test_si_vienen_la_mac_y_la_ip_manda_la_mac(mundo):
    assert _login(mundo, mac=MAC_H1, ip=IP_H2).status_code == 200          # IP_H2 no figura: no se mira
    assert mundo["preguntas_ip"] == []


def test_sin_mac_ni_ip_no_se_identifica_y_se_registra_el_rechazo(mundo):
    r = _login(mundo)
    assert r.status_code == 409 and "identificar" in r.json()["detail"]
    assert not mundo["inyectados"] and eventos.listar()[-1]["tipo"] == "rechazado" and "identificar" in eventos.listar()[-1]["detalle"]


def test_una_ip_que_onos_no_conoce_no_se_identifica(mundo):
    r = _login(mundo, ip="10.0.9.9")
    assert r.status_code == 409 and not mundo["inyectados"] and session_store.get_by_mac(MAC_H1) is None


def test_no_se_identifica_antes_de_validar_la_contrasena(mundo, monkeypatch):
    """Un equipo sin identificar no llega a LDAP: no sirve para probar contrasenas."""
    llamadas = []
    monkeypatch.setattr(pm.portal_auth, "autenticar_ldap", lambda u, c: llamadas.append(u) or None)
    assert _login(mundo, ip="10.0.9.9").status_code == 409 and llamadas == []


def test_contrasena_mala_con_ip_conocida_sigue_siendo_401(mundo):
    r = mundo["http"].post("/portal/login", json={"usuario": "guest01", "clave": "mala", "ip": IP_H1}, headers=TOKEN)
    assert r.status_code == 401 and not mundo["inyectados"]


def test_el_token_sigue_siendo_obligatorio(mundo):
    r = mundo["http"].post("/portal/login", json={"usuario": "guest01", "clave": "guest123", "ip": IP_H1})
    assert r.status_code == 403 and not mundo["inyectados"] and mundo["preguntas_ip"] == []


# ------------------------------------------------------------------ estado
def _sesion_802_1x():
    session_store.register_session(ActiveSession(username="profesor01@example.org", mac=MAC_H1, dpid=DPID, port="1", vlan=10, rol="Docente",
                                                 flow_id="f-base", metodo="802.1X"))


def test_el_estado_por_ip_muestra_la_sesion_del_equipo_con_el_token(mundo):
    _sesion_802_1x()
    r = mundo["http"].get("/portal/estado", params={"ip": IP_H1}, headers=TOKEN)
    assert r.status_code == 200 and r.json()["sesion"]["usuario"] == "profesor01@example.org" and r.json()["sesion"]["metodo"] == "802.1X"


def test_el_estado_por_ip_sin_token_no_se_atiende(mundo):
    _sesion_802_1x()
    assert mundo["http"].get("/portal/estado", params={"ip": IP_H1}).status_code == 403 and mundo["preguntas_ip"] == []


def test_el_estado_por_ip_desconocida_o_sin_parametros_es_sin_sesion(mundo):
    assert mundo["http"].get("/portal/estado", params={"ip": "10.0.9.9"}, headers=TOKEN).json() == {"sesion": None}
    assert mundo["http"].get("/portal/estado").json() == {"sesion": None}
    assert mundo["http"].get("/portal/estado", params={"mac": ""}).json() == {"sesion": None}


def test_el_estado_por_mac_sigue_sin_token_como_antes(mundo):
    _sesion_802_1x()
    r = mundo["http"].get("/portal/estado", params={"mac": MAC_H1})
    assert r.status_code == 200 and r.json()["sesion"]["rol"] == "Docente"


def test_con_mac_y_con_ip_el_estado_usa_la_mac(mundo):
    _sesion_802_1x()
    r = mundo["http"].get("/portal/estado", params={"mac": MAC_H1, "ip": IP_H2})
    assert r.json()["sesion"]["usuario"] == "profesor01@example.org" and mundo["preguntas_ip"] == []


# ------------------------------------------------------------------ logout
def test_el_logout_por_ip_cierra_la_sesion_del_portal(mundo):
    assert _login(mundo, ip=IP_H1).status_code == 200
    borrados = []

    async def borrar(dpid, flow_id):
        borrados.append((dpid, flow_id))
    pm.eliminar_flujo_onos, original = borrar, pm.eliminar_flujo_onos
    try:
        r = mundo["http"].post("/portal/logout", json={"ip": IP_H1}, headers=TOKEN)
    finally:
        pm.eliminar_flujo_onos = original
    assert r.json() == {"status": "cerrada"} and session_store.get_by_mac(MAC_H1) is None and borrados == [(DPID, "flujo-1")]


def test_el_logout_por_ip_desconocida_o_sin_datos_es_sin_sesion(mundo):
    assert mundo["http"].post("/portal/logout", json={"ip": "10.0.9.9"}, headers=TOKEN).json() == {"status": "sin_sesion"}
    assert mundo["http"].post("/portal/logout", json={}, headers=TOKEN).json() == {"status": "sin_sesion"}


def test_el_logout_no_toca_las_sesiones_8021x(mundo):
    _sesion_802_1x()
    assert mundo["http"].post("/portal/logout", json={"ip": IP_H1}, headers=TOKEN).json() == {"status": "sin_sesion"}
    assert session_store.get_by_mac(MAC_H1) is not None
