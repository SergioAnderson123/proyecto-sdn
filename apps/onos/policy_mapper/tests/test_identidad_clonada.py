"""Identidad clonada: el mismo usuario negociando EAP en dos puertos a la vez (MAC spoofing + certificado copiado).

FreeRADIUS no recibe Calling-Station-Id ni NAS-Port-Id del AAA de ONOS (verificado en vivo con el log de
depuración de FreeRADIUS: el Access-Request solo trae User-Name, NAS-IP-Address, State, EAP-Message y
Message-Authenticator), así que el webhook no sabe por sí solo desde qué puerto físico llama cada vez que se
invoca. ONOS sí distingue los puertos internamente (`subscriberId` separado por dpid/puerto).

La política, entonces: con la ubicación fija de LDAP (`X-Dpid-LDAP`/`X-Port-LDAP`) como referencia -no depende
de qué llamada se está procesando-, se admite SOLO la ubicación que coincide y se rechaza cada una de las demás
por su cuenta, con su propio motivo; dos llamadas del mismo usuario a la vez se procesan una tras otra (candado
por usuario), sin bloquear a otros usuarios autenticando en paralelo.
"""
import asyncio
import os
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eventos  # noqa: E402
import policy_mapper as pm  # noqa: E402
from session_manager import session_store  # noqa: E402

USUARIO = "profesor01@example.org"
MAC = "00:00:00:00:00:11"
DPID = "of:0000000000000001"


class _RespuestaFalsa:
    def __init__(self, cuerpo):
        self.status_code = 200
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


def _onos_con(entradas, monkeypatch):
    async def get_falso(self, url, auth=None, timeout=None):
        return _RespuestaFalsa({"users": entradas})
    monkeypatch.setattr(pm.httpx.AsyncClient, "get", get_falso)
    pm._cache_aaa_tabla = None   # cada reconfiguración debe verse de inmediato, no la de la caché de la llamada anterior
    pm._cache_aaa_hasta = 0.0


def _entrada(subscriber_id, mac=MAC, estado="AUTHORIZED_STATE", username=USUARIO):
    return {"username": username, "subscriberId": subscriber_id, "macAddress": mac, "state": estado}


# ---------- candidatos_onos: ahora devuelve TODAS las ubicaciones, sin elegir ----------

def test_un_solo_puerto_da_una_sola_ubicacion(monkeypatch):
    _onos_con([_entrada(f"{DPID}/1")], monkeypatch)
    candidatos = asyncio.run(pm.candidatos_onos(USUARIO))
    assert len(candidatos) == 1 and candidatos[0]["port"] == "1"


def test_el_mismo_usuario_en_dos_puertos_da_dos_ubicaciones(monkeypatch):
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE"),
               _entrada(f"{DPID}/2", estado="UNAUTHORIZED_STATE")], monkeypatch)
    candidatos = asyncio.run(pm.candidatos_onos(USUARIO))
    assert {c["port"] for c in candidatos} == {"1", "2"}


def test_varios_estados_en_el_mismo_puerto_siguen_siendo_una_sola_ubicacion(monkeypatch):
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE"),
               _entrada(f"{DPID}/1", estado="AUTHORIZED_STATE")], monkeypatch)
    candidatos = asyncio.run(pm.candidatos_onos(USUARIO))
    assert len(candidatos) == 1 and candidatos[0]["port"] == "1"


def test_otros_usuarios_no_se_mezclan(monkeypatch):
    _onos_con([_entrada(f"{DPID}/1", username="alumno02@example.org"),
               _entrada(f"{DPID}/2", username=USUARIO)], monkeypatch)
    candidatos = asyncio.run(pm.candidatos_onos(USUARIO))
    assert len(candidatos) == 1 and candidatos[0]["port"] == "2"


# ---------- ubicación real: connectPoint (VNRT: SADIS solo ve el nombre del puerto, "ens5", repetido entre switches) ----------

SW2 = "of:00001a749039894a"


def test_usa_el_connect_point_y_no_el_subscriber_id_inventado_por_sadis(monkeypatch):
    # Formato real de ONOS 2.7 (/onos/aaa/app/users): "entries", "authState", "connectPoint"
    async def get_falso(self, url, auth=None, timeout=None):
        return _RespuestaFalsa({"entries": [{
            "connectPoint": f"{SW2}/2", "authState": "AUTHORIZED_STATE", "macAddress": "FA:16:3E:6B:69:BB",
            "subscriberId": "of:0000000000000en5/1", "username": USUARIO}]})
    monkeypatch.setattr(pm.httpx.AsyncClient, "get", get_falso)
    pm._cache_aaa_tabla = None
    pm._cache_aaa_hasta = 0.0
    candidatos = asyncio.run(pm.candidatos_onos(USUARIO))
    assert candidatos == [{"dpid": SW2, "port": "2", "mac_onos": "fa:16:3e:6b:69:bb",
                           "username_onos": USUARIO, "state": "AUTHORIZED_STATE"}]


def test_sin_connect_point_se_sigue_usando_el_subscriber_id(monkeypatch):
    _onos_con([_entrada(f"{DPID}/3")], monkeypatch)
    candidatos = asyncio.run(pm.candidatos_onos(USUARIO))
    assert candidatos[0]["dpid"] == DPID and candidatos[0]["port"] == "3"


# ---------- el webhook completo: admite al que coincide con LDAP, rechaza a los demás por su cuenta ----------

HEADERS = {"X-Rol-LDAP": "docente", "X-MAC-LDAP": MAC, "X-DPID-LDAP": DPID, "X-Port-LDAP": "1"}


def _cuerpo(usuario=USUARIO):
    return {"TLS-Client-Cert-Common-Name": {"type": "string", "value": [usuario]},
            "User-Name": {"type": "string", "value": [usuario]}}


@pytest.fixture
def cliente(monkeypatch):
    bloqueados = []

    async def lista_negra(dpid, port, mac, idle_timeout=300):
        bloqueados.append((dpid, port, mac, idle_timeout))
    monkeypatch.setattr(pm, "inyectar_blacklist_temporal", lista_negra)

    async def inyectar(dpid, port, mac, vlan_id):
        return "flow-falso"
    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", inyectar)

    async def sin_faltantes(*a, **kw):
        return []
    monkeypatch.setattr(pm.policy_tables, "faltantes", sin_faltantes)

    pm._candados_por_usuario.clear()
    pm._cache_aaa_tabla = None
    pm._cache_aaa_hasta = 0.0
    session_store.remove_session(MAC)
    eventos.limpiar()
    yield TestClient(pm.app), bloqueados
    session_store.remove_session(MAC)


def test_admite_al_que_esta_en_el_puerto_correcto_segun_ldap(monkeypatch, cliente):
    cli, _ = cliente
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE")], monkeypatch)
    r = cli.post("/webhook", json=_cuerpo(), headers=HEADERS)
    assert r.status_code == 200 and r.json()["Tunnel-Private-Group-Id"] == "10"
    assert session_store.get_by_mac(MAC).dpid == DPID and session_store.get_by_mac(MAC).port == "1"


def test_con_dos_puertos_a_la_vez_admite_el_correcto_y_bloquea_solo_el_otro(monkeypatch, cliente):
    cli, bloqueados = cliente
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE"),
               _entrada(f"{DPID}/2", estado="UNAUTHORIZED_STATE")], monkeypatch)
    r = cli.post("/webhook", json=_cuerpo(), headers=HEADERS)
    assert r.status_code == 200 and r.json()["Tunnel-Private-Group-Id"] == "10"
    assert session_store.get_by_mac(MAC).port == "1"          # el ganador: coincide con LDAP (puerto 1)
    assert bloqueados == [(DPID, "2", MAC, 600)]               # solo el perdedor (puerto 2) queda bloqueado
    tipos = [e["tipo"] for e in eventos.listar(5)]
    assert tipos.count("conflicto") == 1 and tipos.count("admitido") == 1   # un evento propio por cada uno, no uno compartido
    conflicto = next(e for e in eventos.listar(5) if e["tipo"] == "conflicto")
    assert conflicto["port"] == "2" and "2" in conflicto["detalle"]        # el evento nombra SU puerto, no los dos


def test_sin_ubicacion_fija_en_ldap_no_se_puede_resolver_y_no_se_admite_a_nadie(monkeypatch, cliente):
    cli, bloqueados = cliente
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE"),
               _entrada(f"{DPID}/2", estado="UNAUTHORIZED_STATE")], monkeypatch)
    r = cli.post("/webhook", json=_cuerpo(), headers={"X-Rol-LDAP": "docente", "X-MAC-LDAP": MAC})  # sin X-DPID/Port-LDAP
    assert r.status_code == 409
    assert session_store.get_by_mac(MAC) is None and bloqueados == []


def test_una_segunda_llamada_del_mismo_ganador_no_vuelve_a_bloquear_nada(monkeypatch, cliente):
    """Dos llamadas por la MISMA negociación ya admitida (reintento, o la segunda de las dos físicas una vez
    resuelta la ambigüedad): la segunda confirma la sesión existente, no la trata como un conflicto nuevo."""
    cli, bloqueados = cliente
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE"),
               _entrada(f"{DPID}/2", estado="UNAUTHORIZED_STATE")], monkeypatch)
    r1 = cli.post("/webhook", json=_cuerpo(), headers=HEADERS)
    assert r1.status_code == 200
    bloqueados.clear()
    r2 = cli.post("/webhook", json=_cuerpo(), headers=HEADERS)
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert bloqueados == []                                    # no se vuelve a bloquear el puerto legítimo


def test_una_mac_distinta_en_otro_puerto_sigue_siendo_conflicto_real(monkeypatch, cliente):
    """Si ya hay sesión en el puerto correcto y aparece otra en una ubicación distinta, sigue siendo CASO A."""
    cli, bloqueados = cliente
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE")], monkeypatch)
    r1 = cli.post("/webhook", json=_cuerpo(), headers=HEADERS)
    assert r1.status_code == 200
    bloqueados.clear()
    _onos_con([_entrada(f"{DPID}/3", estado="UNAUTHORIZED_STATE")], monkeypatch)   # ahora solo aparece el puerto 3
    r2 = cli.post("/webhook", json=_cuerpo(), headers=HEADERS)
    assert r2.status_code == 409 and bloqueados == [(DPID, "3", MAC, 600)]


def test_otra_identidad_con_la_misma_mac_y_puerto_no_se_confirma_como_sesion_propia(monkeypatch, cliente):
    from session_manager import ActiveSession
    http, bloqueados = cliente
    _onos_con([_entrada(f"{DPID}/1", username="otro@example.org")], monkeypatch)
    session_store.register_session(ActiveSession(username=USUARIO, mac=MAC, dpid=DPID, port="1", vlan=10, rol="Docente"))
    r = http.post("/webhook", json=_cuerpo("otro@example.org"), headers=HEADERS)
    assert r.status_code == 409
    assert bloqueados
