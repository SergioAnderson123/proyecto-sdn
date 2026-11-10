"""Ascenso por el portal: un equipo con sesion 802.1X de rol Alumno sube a Docente cuando un profesor entra con SU usuario.

Condiciones (todas a la vez): la MAC tiene una sesion 802.1X sin ascenso previo; el rol base puede subir al rol del usuario
(`ascenso` en policy.yaml); el usuario no tiene otra sesion; la ubicacion que ONOS AAA tiene para la MAC coincide con la de la sesion. Cualquier otra combinacion sigue siendo el 409
de siempre, y la deteccion de suplantacion de 802.1X no se relaja.
"""
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

MAC = "fa:16:3e:39:6e:96"
DPID = "of:00001a749039894a"
PC = "alumno01@example.org"          # la identidad del equipo (rol Alumno)
PROFE = "profesor01"          # el portal registra el usuario tal como lo escribe la persona
GRUPOS = {"profesor01": ["profesor"], "alumno02": ["alumno"], "support01": ["soporteTI"], "profesor02": ["profesor"]}


@pytest.fixture
def mundo(monkeypatch):
    """Sin LDAP, ONOS ni red: registra los flujos que se instalan y se borran y las MAC que se bloquean."""
    r = {"inyectados": [], "borrados": [], "negras": [], "aaa": [(DPID, "2")], "n": 0}

    async def inyectar(dpid, port, mac, vlan):
        r["n"] += 1
        r["inyectados"].append((dpid, str(port), mac, vlan))
        return f"flujo-{r['n']}"

    async def borrar(dpid, flow_id):
        r["borrados"].append((dpid, flow_id))

    async def negra(dpid, port, mac, idle_timeout=300):
        r["negras"].append((dpid, port, mac, idle_timeout))

    async def ubicaciones(mac):          # lo que la tabla AAA de ONOS dice de esa MAC (dpid, puerto)
        return list(r["aaa"])

    async def sin_faltantes(*a, **k):
        return []

    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", inyectar)
    monkeypatch.setattr(pm, "eliminar_flujo_onos", borrar)
    monkeypatch.setattr(pm, "inyectar_blacklist_temporal", negra)
    monkeypatch.setattr(pm, "ubicaciones_aaa", ubicaciones)
    monkeypatch.setattr(pm.portal_auth, "autenticar_ldap", lambda u, c: GRUPOS.get(u) if c == "clave" else None)
    monkeypatch.setattr(pm.policy_tables, "faltantes", sin_faltantes)
    monkeypatch.setattr(pm, "AGENTE_TOKEN", "tok")
    session_store.remove_session(MAC)
    eventos.limpiar()
    pm._candados_por_usuario.clear()
    pm._cache_aaa_tabla = None
    pm._cache_aaa_hasta = 0.0
    r["http"] = TestClient(pm.app)
    yield r
    session_store.remove_session(MAC)


def _base(**kw):
    datos = dict(username=PC, mac=MAC, dpid=DPID, port="2", vlan=20, rol="Alumno", flow_id="flujo-base", metodo="802.1X")
    datos.update(kw)
    session_store.register_session(ActiveSession(**datos))
    return session_store.get_by_mac(MAC)


def _login(mundo, usuario="profesor01", clave="clave", mac=MAC):
    return mundo["http"].post("/portal/login", json={"usuario": usuario, "clave": clave, "mac": mac})


# ------------------------------------------------------------------ el ascenso permitido

def test_un_profesor_sube_el_equipo_de_alumno_a_docente(mundo):
    base = _base()
    r = _login(mundo)
    assert r.status_code == 200 and r.json()["rol"] == "Docente" and r.json()["tabla"] == 10
    ses = session_store.get_by_mac(MAC)
    assert ses.username == PROFE and ses.vlan == 10 and ses.metodo == "Portal" and ses.base is base
    assert ses.dpid == DPID and ses.port == "2" and ses.expira is not None
    assert mundo["borrados"] == [(DPID, "flujo-base")]               # primero se quita el flujo del rol base
    assert mundo["inyectados"] == [(DPID, "2", MAC, 10)]             # y se instala el del rol Docente en el mismo puerto
    assert session_store.get_by_username(PC) is None and session_store.get_by_username(PROFE) is ses
    assert eventos.listar()[-1]["tipo"] == "admitido" and "Alumno -> Docente" in eventos.listar()[-1]["detalle"]


def test_el_estado_del_portal_dice_a_que_roles_puede_subir_el_equipo(mundo):
    _base()
    s = mundo["http"].get("/portal/estado", params={"mac": MAC}).json()["sesion"]
    assert s["metodo"] == "802.1X" and s["ascenso_a"] == ["docente"] and s["base"] is None
    _login(mundo)
    s = mundo["http"].get("/portal/estado", params={"mac": MAC}).json()["sesion"]
    assert s["usuario"] == PROFE and s["ascenso_a"] == [] and s["base"] == {"usuario": PC, "rol": "Alumno"}


# ------------------------------------------------------------------ lo que NO se puede subir (siguen siendo 409)

def test_un_alumno_no_entra_por_el_portal_ni_sube_un_equipo(mundo):
    """El grupo alumno no esta en `portal.grupos`: un alumno usa un equipo registrado (802.1X), no el portal."""
    _base()
    r = _login(mundo, "alumno02")
    assert r.status_code == 403 and "no tiene acceso" in r.json()["detail"]
    assert session_store.get_by_mac(MAC).username == PC and not mundo["borrados"] and not mundo["inyectados"]
    assert "alumno" not in pm.POLITICA["portal"]["grupos"]


def test_el_administrador_no_entra_por_el_portal_ni_sube_un_equipo(mundo):
    """El grupo soporteTI no esta en `portal.grupos`: el administrador solo entra por 802.1X con el certificado de su equipo."""
    _base()
    r = _login(mundo, "support01")
    assert r.status_code == 403 and "no tiene acceso" in r.json()["detail"]
    assert session_store.get_by_mac(MAC).username == PC and not mundo["borrados"] and not mundo["inyectados"]
    assert eventos.listar()[-1]["tipo"] == "rechazado"
    assert "soporteTI" not in pm.POLITICA["portal"]["grupos"]


def test_la_sesion_base_de_otro_rol_no_sube(mundo):
    _base(vlan=10, rol="Docente", username="profesor02@example.org")       # el equipo ya es Docente: no hay ascenso desde Docente
    assert _login(mundo).status_code == 409 and not mundo["inyectados"]


def test_una_sesion_del_portal_no_se_sube(mundo):
    _base(metodo="Portal", username="guest01", vlan=50, rol="Invitado")
    assert _login(mundo).status_code == 409 and not mundo["inyectados"]


def test_no_se_sube_dos_veces_el_mismo_equipo(mundo):
    _base()
    assert _login(mundo).status_code == 200
    assert _login(mundo, "profesor02").status_code == 409          # ya es un ascenso: no se encadenan
    assert _login(mundo, "profesor01").status_code == 409          # ni la misma persona dos veces


def test_si_el_profesor_ya_tiene_sesion_en_otro_equipo_no_sube_este(mundo):
    otra = "fa:16:3e:00:00:99"
    session_store.register_session(ActiveSession(username=PROFE, mac=otra, dpid="of:0000ca272d265744", port="1", vlan=10,
                                                 rol="Docente", flow_id="otro", metodo="Portal"))
    try:
        _base()
        r = _login(mundo)
        assert r.status_code == 409 and session_store.get_by_mac(MAC).username == PC
    finally:
        session_store.remove_session(otra)


def test_credenciales_incorrectas_no_tocan_nada(mundo):
    _base()
    assert _login(mundo, clave="mala").status_code == 401
    assert session_store.get_by_mac(MAC).username == PC and not mundo["borrados"]


# ------------------------------------------------------------------ ubicacion (tabla AAA de ONOS contra el Session Manager)

def test_si_la_tabla_aaa_ve_al_equipo_en_otro_puerto_no_sube(mundo):
    _base()
    mundo["aaa"] = [(DPID, "3")]                                     # ONOS AAA lo tiene en otro puerto que el de su sesion
    r = _login(mundo)
    assert r.status_code == 409 and "ubicación" in r.json()["detail"]
    assert session_store.get_by_mac(MAC).username == PC and not mundo["inyectados"]
    assert eventos.listar()[-1]["tipo"] == "conflicto"


def test_si_la_tabla_aaa_ve_al_equipo_en_otro_switch_no_sube(mundo):
    _base()
    mundo["aaa"] = [("of:0000ca272d265744", "2")]
    assert _login(mundo).status_code == 409


def test_si_la_tabla_aaa_tiene_tambien_la_ubicacion_de_la_sesion_sube(mundo):
    _base()
    mundo["aaa"] = [(DPID, "1"), (DPID, "2")]                        # una entrada vieja y la vigente
    assert _login(mundo).status_code == 200


def test_si_onos_aaa_no_tiene_al_equipo_autorizado_no_sube_por_defecto(mundo):
    _base()
    mundo["aaa"] = []
    r = _login(mundo)
    assert r.status_code == 409 and "ONOS no tiene al equipo" in r.json()["detail"]
    assert session_store.get_by_mac(MAC).username == PC and not mundo["inyectados"]


def test_sin_exigir_la_ubicacion_sube_aunque_onos_aaa_no_tenga_al_equipo(mundo, monkeypatch):
    monkeypatch.setitem(pm.POLITICA, "ascenso_exigir_ubicacion", False)
    _base()
    mundo["aaa"] = []
    assert _login(mundo).status_code == 200


def test_sin_exigir_la_ubicacion_se_compara_si_onos_aaa_la_tiene(mundo, monkeypatch):
    monkeypatch.setitem(pm.POLITICA, "ascenso_exigir_ubicacion", False)
    _base()
    mundo["aaa"] = [(DPID, "3")]
    assert _login(mundo).status_code == 409


# --- lectura de la tabla AAA

def _tabla(monkeypatch, filas):
    async def tabla():
        return filas
    monkeypatch.setattr(pm, "_tabla_aaa", tabla)


def test_ubicaciones_aaa_solo_cuenta_las_entradas_autorizadas_de_esa_mac(monkeypatch):
    # el monkeypatch del fixture de otras pruebas no aplica aqui: se usa la funcion real
    filas = [
        {"username": PC, "connectPoint": f"{DPID}/2", "macAddress": MAC.upper(), "authState": "AUTHORIZED_STATE"},
        {"username": PC, "connectPoint": f"{DPID}/4", "macAddress": MAC.upper(), "authState": "IDLE_STATE"},          # ya hizo logoff
        {"username": PC, "connectPoint": f"{DPID}/5", "macAddress": MAC.upper(), "authState": "UNAUTHORIZED_STATE"},
        {"username": "otro", "connectPoint": f"{DPID}/3", "macAddress": "AA:BB:CC:00:00:01", "authState": "AUTHORIZED_STATE"},
        {"username": PC, "connectPoint": f"{DPID}/2", "macAddress": MAC.upper(), "state": "AUTHORIZED_STATE"},     # repetida: una sola vez
        {"username": PC, "subscriberId": "ens4", "macAddress": MAC.upper(), "authState": "AUTHORIZED_STATE"},      # sin dpid/puerto: no se usa
        "basura",
    ]
    _tabla(monkeypatch, filas)
    import importlib
    real = importlib.import_module("policy_mapper").ubicaciones_aaa
    assert asyncio.run(real(MAC)) == [(DPID, "2")]
    assert asyncio.run(real("aa:bb:cc:00:00:99")) == []


# ------------------------------------------------------------------ fallo al subir

def test_si_no_se_puede_instalar_el_flujo_alto_el_equipo_conserva_su_rol(mundo, monkeypatch):
    base = _base()
    llamadas = []

    async def inyectar(dpid, port, mac, vlan):
        llamadas.append(vlan)
        return None if vlan == 10 else "flujo-restaurado"
    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", inyectar)
    assert _login(mundo).status_code == 502
    ses = session_store.get_by_mac(MAC)
    assert ses is base and ses.username == PC and ses.flow_id == "flujo-restaurado" and llamadas == [10, 20]


# ------------------------------------------------------------------ volver al rol base

def test_cerrar_sesion_en_el_portal_devuelve_el_equipo_a_alumno(mundo):
    _base()
    _login(mundo)
    mundo["borrados"].clear(), mundo["inyectados"].clear()
    r = mundo["http"].post("/portal/logout", json={"mac": MAC})
    assert r.json() == {"status": "cerrada", "vuelve_a": "Alumno"}
    ses = session_store.get_by_mac(MAC)
    assert ses.username == PC and ses.vlan == 20 and ses.metodo == "802.1X" and ses.base is None
    assert mundo["borrados"] == [(DPID, "flujo-1")] and mundo["inyectados"] == [(DPID, "2", MAC, 20)]
    assert session_store.get_by_username(PROFE) is None


def test_al_vencer_el_plazo_el_equipo_vuelve_a_alumno(mundo, monkeypatch):
    _base()
    _login(mundo)
    session_store.get_by_mac(MAC).expira = 1.0                         # ya vencio
    vueltas = []

    async def sueno(s):
        vueltas.append(s)
        if len(vueltas) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(pm.asyncio, "sleep", sueno)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pm._expirar_sesiones_portal())
    ses = session_store.get_by_mac(MAC)
    assert ses.username == PC and ses.vlan == 20 and ses.metodo == "802.1X"


def test_el_aviso_del_agente_cierra_tambien_un_ascenso(mundo):
    _base()
    _login(mundo)
    r = mundo["http"].post("/sessions/logout", json={"mac": MAC}, headers={"X-Agente-Token": "tok"})
    assert r.json()["status"] == "cerrada" and session_store.get_by_mac(MAC) is None
    assert not mundo["negras"]                                          # no es una suplantacion: sin lista negra


def test_sin_ascenso_una_sesion_del_portal_sigue_fuera_del_aviso_del_agente(mundo):
    _base(metodo="Portal", username="guest01", vlan=50, rol="Invitado")
    r = mundo["http"].post("/sessions/logout", json={"mac": MAC}, headers={"X-Agente-Token": "tok"})
    assert r.json() == {"status": "no_es_802_1x"} and session_store.get_by_mac(MAC) is not None


# ------------------------------------------------------------------ 802.1X durante un ascenso: sin cruzarse con el antispoofing

class _Resp:
    status_code = 200

    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


def _onos_con(monkeypatch, usuario):
    entrada = {"username": usuario, "connectPoint": f"{DPID}/2", "macAddress": MAC.upper(), "authState": "AUTHORIZED_STATE"}

    async def get_falso(self, url, auth=None, timeout=None):
        return _Resp({"entries": [entrada]})
    monkeypatch.setattr(pm.httpx.AsyncClient, "get", get_falso)
    pm._cache_aaa_tabla = None
    pm._cache_aaa_hasta = 0.0


def _webhook(mundo, usuario):
    cuerpo = {"TLS-Client-Cert-Common-Name": {"type": "string", "value": [usuario]},
              "User-Name": {"type": "string", "value": [usuario]}}
    cabeceras = {"X-Rol-LDAP": "alumno", "X-MAC-LDAP": MAC, "X-DPID-LDAP": DPID, "X-Port-LDAP": "2"}
    return mundo["http"].post("/webhook", json=cuerpo, headers=cabeceras)


def test_si_el_propio_equipo_reautentica_durante_el_ascenso_se_anula_el_ascenso_sin_bloquear(mundo, monkeypatch):
    _base()
    _login(mundo)
    _onos_con(monkeypatch, PC)
    r = _webhook(mundo, PC)
    assert r.status_code == 200, r.text
    ses = session_store.get_by_mac(MAC)
    assert ses.username == PC and ses.vlan == 20 and ses.base is None          # el equipo vuelve a Alumno
    assert session_store.get_by_username(PROFE) is None
    assert not mundo["negras"]                                                  # y NO se trata como suplantacion
    assert (DPID, "flujo-1") in mundo["borrados"]                               # el flujo Docente se retiro


def test_otra_identidad_en_la_misma_mac_durante_un_ascenso_sigue_siendo_suplantacion(mundo, monkeypatch):
    _base()
    _login(mundo)
    _onos_con(monkeypatch, "alumno02@example.org")
    r = _webhook(mundo, "alumno02@example.org")
    assert r.status_code == 409
    assert mundo["negras"] and mundo["negras"][-1][3] == 600                    # lista negra como siempre
    assert session_store.get_by_mac(MAC).username == PROFE                      # el ascenso no se toca


def test_el_antispoofing_sin_ascenso_sigue_igual(mundo, monkeypatch):
    _base()                                                                     # sesion de alumno01 y entra alumno02 con la misma MAC
    _onos_con(monkeypatch, "alumno02@example.org")
    r = _webhook(mundo, "alumno02@example.org")
    assert r.status_code == 409 and mundo["negras"] and session_store.get_by_mac(MAC).username == PC


# ------------------------------------------------------------------ login directo (equipo sin sesion): solo `portal.login_directo`

@pytest.fixture
def sin_sesion(mundo, monkeypatch):
    async def ubicar(mac, url, auth):
        return (DPID, "2")
    monkeypatch.setattr(pm.portal_auth, "ubicar_host", ubicar)
    session_store.remove_session(MAC)
    return mundo


def test_un_profesor_no_inicia_sesion_directo_desde_un_equipo_sin_sesion(sin_sesion):
    r = _login(sin_sesion, "profesor01")
    assert r.status_code == 403 and "equipo ya autenticado" in r.json()["detail"]
    assert session_store.get_by_mac(MAC) is None and not sin_sesion["inyectados"]          # no se instalo ningun flujo
    ev = eventos.listar()[-1]
    assert ev["tipo"] == "rechazado" and "ascenso" in ev["detalle"]


def test_solo_el_invitado_entra_directo_desde_un_equipo_sin_sesion(sin_sesion, monkeypatch):
    monkeypatch.setitem(GRUPOS, "guest01", ["invitado"])
    assert pm.POLITICA["portal"]["login_directo"] == ["invitado"]
    r = _login(sin_sesion, "guest01")
    assert r.status_code == 200 and r.json()["rol"] == "Invitado" and r.json()["tabla"] == 50
    session_store.remove_session(MAC)
    for usuario in ("alumno02", "support01"):                                  # ni alumno ni administrador: equipos registrados por 802.1X
        r = _login(sin_sesion, usuario)
        assert r.status_code == 403 and session_store.get_by_mac(MAC) is None


def test_el_mismo_profesor_si_sube_un_equipo_ya_autenticado(mundo):
    _base()
    assert _login(mundo, "profesor01").status_code == 200                                  # el ascenso no se ve afectado


def test_sin_login_directo_configurado_vale_el_comportamiento_anterior(sin_sesion, monkeypatch):
    monkeypatch.delitem(pm.POLITICA["portal"], "login_directo")
    assert _login(sin_sesion, "profesor01").status_code == 200
