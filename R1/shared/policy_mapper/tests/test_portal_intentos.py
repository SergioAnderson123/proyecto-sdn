"""Límite de intentos fallidos en /portal/login (requisito P5 de docs/DISENO_EXCEPCIONES_POR_SESION.md).

intentos.py ya existía como módulo puro (Lab 7, R1-03) pero NO estaba conectado al login del portal: se podían probar contraseñas sin límite."""
import os
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventos  # noqa: E402
import intentos  # noqa: E402
import policy_mapper as pm  # noqa: E402
from session_manager import session_store  # noqa: E402

MAC1, MAC2 = "fa:16:3e:b6:96:08", "fa:16:3e:71:08:66"
DPID = "of:0000e6925d87354b"
TOKEN = {"X-Portal-Token": "t0ken"}
CLAVE_BUENA = "buena-clave"


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def mundo(monkeypatch):
    reloj = Reloj()
    ldap = {"llamadas": []}

    def autenticar(usuario, clave):
        ldap["llamadas"].append((usuario, clave))
        return ["invitado"] if (usuario, clave) == ("guest01", CLAVE_BUENA) else None

    async def mac_de_ip(ip, onos_url, auth, reloj=None):
        return {"10.0.1.100": MAC1, "10.0.2.100": MAC2}.get(ip)

    async def ubicar(mac, onos_url, auth):
        return DPID, "1"

    async def inyectar(dpid, port, mac, vlan):
        return f"flujo-{mac}"

    async def sin_faltantes(*a, **k):
        return []

    monkeypatch.setattr(pm.portal_auth, "autenticar_ldap", autenticar)
    monkeypatch.setattr(pm.portal_auth, "mac_de_ip", mac_de_ip)
    monkeypatch.setattr(pm.portal_auth, "ubicar_host", ubicar)
    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", inyectar)
    monkeypatch.setattr(pm.policy_tables, "faltantes", sin_faltantes)
    monkeypatch.setattr(pm, "PORTAL_TOKEN", "t0ken")
    monkeypatch.setattr(pm, "LIMITE_PORTAL_CUENTA", intentos.LimiteIntentos(5, 300, 900, reloj=reloj))
    monkeypatch.setattr(pm, "LIMITE_PORTAL_EQUIPO", intentos.LimiteIntentos(15, 300, 900, reloj=reloj))
    for mac in (MAC1, MAC2):
        session_store.remove_session(mac)
    eventos.limpiar()
    yield {"http": TestClient(pm.app), "reloj": reloj, "ldap": ldap}
    for mac in (MAC1, MAC2):
        session_store.remove_session(mac)


def login(m, usuario="guest01", clave=CLAVE_BUENA, ip="10.0.1.100"):
    return m["http"].post("/portal/login", json={"usuario": usuario, "clave": clave, "ip": ip}, headers=TOKEN)


def fallar(m, n, usuario="guest01", ip="10.0.1.100"):
    return [login(m, usuario, "mala", ip).status_code for _ in range(n)]


def test_las_claves_incorrectas_dan_401_hasta_el_quinto_intento_que_bloquea(mundo):
    assert fallar(mundo, 5) == [401] * 5
    assert login(mundo).status_code == 429                       # la sexta, ya bloqueada


def test_un_bloqueo_rechaza_tambien_la_clave_correcta_y_no_consulta_ldap(mundo):
    fallar(mundo, 5)
    llamadas = len(mundo["ldap"]["llamadas"])
    r = login(mundo)                                             # la clave BUENA
    assert r.status_code == 429 and "Demasiados intentos" in r.json()["detail"]
    assert len(mundo["ldap"]["llamadas"]) == llamadas            # ni siquiera se probó contra LDAP


def test_el_bloqueo_termina_y_la_cuenta_vuelve_a_entrar(mundo):
    fallar(mundo, 5)
    mundo["reloj"].t += 899
    assert login(mundo).status_code == 429
    mundo["reloj"].t += 2
    assert login(mundo).status_code == 200


def test_cuatro_fallas_y_un_acceso_correcto_empiezan_de_cero(mundo):
    fallar(mundo, 4)
    assert login(mundo).status_code == 200
    session_store.remove_session(MAC1)
    assert fallar(mundo, 4) == [401] * 4                         # no suman a las 4 de antes
    assert login(mundo).status_code == 200


def test_las_fallas_viejas_fuera_de_la_ventana_no_cuentan(mundo):
    fallar(mundo, 4)
    mundo["reloj"].t += 301
    assert fallar(mundo, 4) == [401] * 4
    assert login(mundo).status_code == 200


def test_el_bloqueo_de_una_cuenta_no_afecta_a_otra(mundo):
    fallar(mundo, 5, usuario="profesor02")
    assert login(mundo, usuario="profesor02").status_code == 429
    assert login(mundo, usuario="guest01").status_code == 200


def test_la_cuenta_se_bloquea_aunque_cambie_el_equipo_o_las_mayusculas(mundo):
    fallar(mundo, 3, usuario="Profesor02", ip="10.0.1.100")
    fallar(mundo, 2, usuario="profesor02 ", ip="10.0.2.100")
    assert login(mundo, usuario="PROFESOR02", ip="10.0.2.100").status_code == 429


def test_un_equipo_que_prueba_muchos_usuarios_se_bloquea_aunque_ninguna_cuenta_llegue_a_5(mundo):
    for i in range(15):
        assert login(mundo, usuario=f"usuario{i}", clave="mala").status_code == 401
    r = login(mundo, usuario="guest01")                          # otra cuenta, la clave buena, desde el mismo equipo
    assert r.status_code == 429
    assert login(mundo, usuario="guest01", ip="10.0.2.100").status_code == 200   # otro equipo sigue entrando


def test_el_mensaje_es_el_mismo_exista_o_no_la_cuenta(mundo):
    fallar(mundo, 5, usuario="guest01")
    fallar(mundo, 5, usuario="no_existe", ip="10.0.2.100")
    a = login(mundo, usuario="guest01").json()
    b = login(mundo, usuario="no_existe", ip="10.0.2.100").json()
    assert a == b


def test_un_equipo_sin_identificar_no_cuenta_ni_prueba_claves(mundo):
    assert login(mundo, ip="10.9.9.9").status_code == 409
    assert mundo["ldap"]["llamadas"] == []


def test_el_bloqueo_queda_en_la_bitacora_sin_la_clave(mundo):
    fallar(mundo, 5)
    login(mundo)
    textos = [(e["tipo"], e["detalle"]) for e in eventos.listar()] if hasattr(eventos, "listar") else []
    if not textos:
        textos = [(e["tipo"], e["detalle"]) for e in mundo["http"].get("/events").json().get("events", [])]
    assert any("bloqueo temporal activado" in d for _, d in textos) and any("bloqueado temporalmente" in d for _, d in textos)
    assert not any("mala" in d or CLAVE_BUENA in d for _, d in textos)


def test_por_defecto_son_5_intentos_por_cuenta_y_15_por_equipo():
    assert pm.LIMITE_PORTAL_CUENTA.max_intentos == 5 and pm.LIMITE_PORTAL_EQUIPO.max_intentos == 15
    assert pm.LIMITE_PORTAL_CUENTA.bloqueo_s == 900 and pm.LIMITE_PORTAL_CUENTA.ventana_s == 300
