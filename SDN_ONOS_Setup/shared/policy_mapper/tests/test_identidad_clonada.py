"""obtener_datos_onos debe rechazar por ambigüedad cuando el mismo usuario negocia EAP en más de un puerto a la vez

(MAC/identidad clonada), en vez de adivinar cuál de las dos ubicaciones es la real.

FreeRADIUS no recibe Calling-Station-Id ni NAS-Port-Id del AAA de ONOS (verificado en vivo con el log de depuración
de FreeRADIUS: el Access-Request solo trae User-Name, NAS-IP-Address, State, EAP-Message y Message-Authenticator),
así que la única fuente de la ubicación física es `/onos/aaa/app/users`, y esa es la que hay que leer sin adivinar.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy_mapper as pm  # noqa: E402

USUARIO = "profesor01@example.org"


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


def _entrada(subscriber_id, mac="00:00:00:00:00:11", estado="AUTHORIZED_STATE", username=USUARIO):
    return {"username": username, "subscriberId": subscriber_id, "macAddress": mac, "state": estado}


def test_un_solo_puerto_se_resuelve_normalmente(monkeypatch):
    _onos_con([_entrada("of:0000000000000001/1")], monkeypatch)
    info = asyncio.run(pm.obtener_datos_onos(USUARIO))
    assert info == {"dpid": "of:0000000000000001", "port": "1", "mac_onos": "00:00:00:00:00:11",
                     "username_onos": USUARIO, "state": "AUTHORIZED_STATE"}


def test_el_mismo_usuario_en_dos_puertos_a_la_vez_es_ambiguo(monkeypatch):
    _onos_con([_entrada("of:0000000000000001/1", estado="UNAUTHORIZED_STATE"),
               _entrada("of:0000000000000001/2", estado="UNAUTHORIZED_STATE")], monkeypatch)
    info = asyncio.run(pm.obtener_datos_onos(USUARIO))
    assert info["ambiguo"] is True
    assert {u["port"] for u in info["ubicaciones"]} == {"1", "2"}


def test_varios_estados_en_el_mismo_puerto_no_son_ambiguos(monkeypatch):
    # reconexión normal del mismo usuario en el mismo puerto: varias entradas, una sola ubicación
    _onos_con([_entrada("of:0000000000000001/1", estado="UNAUTHORIZED_STATE"),
               _entrada("of:0000000000000001/1", estado="AUTHORIZED_STATE")], monkeypatch)
    info = asyncio.run(pm.obtener_datos_onos(USUARIO))
    assert "ambiguo" not in info and info["port"] == "1"


def test_otros_usuarios_no_generan_ambiguedad(monkeypatch):
    _onos_con([_entrada("of:0000000000000001/1", username="alumno02@example.org"),
               _entrada("of:0000000000000001/2", username=USUARIO)], monkeypatch)
    info = asyncio.run(pm.obtener_datos_onos(USUARIO))
    assert "ambiguo" not in info and info["port"] == "2"


@pytest.mark.skipif(not pytest.importorskip("fastapi", reason="fastapi no instalado") or False, reason="")
def test_el_webhook_rechaza_con_409_y_registra_conflicto(monkeypatch):
    from fastapi.testclient import TestClient
    import eventos

    _onos_con([_entrada("of:0000000000000001/1", estado="UNAUTHORIZED_STATE"),
               _entrada("of:0000000000000001/2", estado="UNAUTHORIZED_STATE")], monkeypatch)
    eventos.limpiar()
    cliente = TestClient(pm.app)
    r = cliente.post("/webhook", json={"TLS-Client-Cert-Common-Name": {"type": "string", "value": [USUARIO]},
                                       "User-Name": {"type": "string", "value": [USUARIO]}},
                      headers={"X-Rol-LDAP": "docente", "X-MAC-LDAP": "00:00:00:00:00:11"})
    assert r.status_code == 409
    ev = eventos.listar(1)[0]
    assert ev["tipo"] == "conflicto" and "puerto" in ev["detalle"].lower() or "más de un puerto" in ev["detalle"]
