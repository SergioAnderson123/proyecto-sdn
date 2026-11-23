"""portal_auth.mac_de_ip: la MAC de un cliente de otra subred (capa 3) a partir de su IP, con los hosts que ONOS tiene (los rellena la app DHCP).

El portal ya no es vecino de capa 2 del cliente, así que su tabla ARP no lo tiene (23-nov: el ARP del server solo traía las IP viejas de h1 y h2 y los gateways)."""
import asyncio
import os
import sys

import pytest

pytest.importorskip("httpx")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import portal_auth as pa  # noqa: E402

HOSTS = [
    {"mac": "FA:16:3E:B6:96:08", "ipAddresses": ["10.0.1.100"], "locations": [{"elementId": "of:0000e6925d87354b", "port": "1"}]},
    {"mac": "FA:16:3E:71:08:66", "ipAddresses": ["10.0.2.100"], "locations": [{"elementId": "of:0000766a0c84c741", "port": "1"}]},
    {"mac": "02:00:00:00:00:99", "ipAddresses": []},
]


# ---------- elegir_mac (pura)
def test_la_mac_sale_del_unico_host_con_esa_ip_en_minusculas():
    assert pa.elegir_mac(HOSTS, "10.0.1.100") == "fa:16:3e:b6:96:08"
    assert pa.elegir_mac(HOSTS, "10.0.2.100") == "fa:16:3e:71:08:66"


def test_una_ip_que_ningun_host_tiene_no_se_identifica():
    assert pa.elegir_mac(HOSTS, "10.0.3.100") is None and pa.elegir_mac([], "10.0.1.100") is None and pa.elegir_mac(None, "10.0.1.100") is None


def test_si_dos_hosts_distintos_tienen_la_misma_ip_no_se_adivina():
    assert pa.elegir_mac(HOSTS + [{"mac": "FA:16:3E:00:00:01", "ipAddresses": ["10.0.1.100"]}], "10.0.1.100") is None


def test_el_mismo_host_listado_dos_veces_cuenta_como_uno():
    assert pa.elegir_mac(HOSTS + [HOSTS[0]], "10.0.1.100") == "fa:16:3e:b6:96:08"


def test_entradas_raras_de_onos_no_rompen():
    raras = [None, {}, {"ipAddresses": ["10.0.2.100"]}, {"mac": 5, "ipAddresses": ["10.0.2.100"]}, {"mac": "aa", "ipAddresses": None}]
    assert pa.elegir_mac(raras + HOSTS, "10.0.2.100") == "fa:16:3e:71:08:66"


# ---------- mac_de_ip (con un ONOS de mentira)
class _Resp:
    def __init__(self, codigo, cuerpo):
        self.status_code, self._c = codigo, cuerpo

    def json(self):
        return self._c


@pytest.fixture
def onos(monkeypatch):
    """Sustituye httpx.AsyncClient por uno que contesta con `estado['resp']` (o lanza `estado['error']`) y cuenta las peticiones."""
    estado = {"resp": _Resp(200, {"hosts": HOSTS}), "error": None, "pedidas": []}

    class Cliente:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, auth=None, timeout=None):
            estado["pedidas"].append((url, auth))
            if estado["error"]:
                raise estado["error"]
            return estado["resp"]
    monkeypatch.setattr(pa.httpx, "AsyncClient", Cliente)
    monkeypatch.setattr(pa, "HA_ENABLED", False)
    monkeypatch.setitem(pa._hosts_cache, "t", None)
    monkeypatch.setitem(pa._hosts_cache, "hosts", [])
    return estado


def _mac(ip, reloj=lambda: 100.0):
    return asyncio.run(pa.mac_de_ip(ip, "http://onos:8181/onos/v1", ("onos", "rocks"), reloj))


def test_se_resuelve_la_mac_por_la_ip_con_la_url_y_la_autenticacion_esperadas(onos):
    assert _mac("10.0.1.100") == "fa:16:3e:b6:96:08"
    assert onos["pedidas"] == [("http://onos:8181/onos/v1/hosts", ("onos", "rocks"))]


def test_una_ip_con_espacios_o_basura_no_llega_a_ONOS(onos):
    assert _mac(" 10.0.1.100 ") == "fa:16:3e:b6:96:08"
    for mala in ("", "no-es-una-ip", "10.0.1", "::1", None, "10.0.1.100/24"):
        assert _mac(mala) is None
    assert len(onos["pedidas"]) == 1                      # solo la primera, la valida


def test_la_lista_se_guarda_unos_segundos_y_caduca(onos):
    _mac("10.0.1.100", lambda: 100.0)
    _mac("10.0.2.100", lambda: 103.0)
    assert len(onos["pedidas"]) == 1
    _mac("10.0.2.100", lambda: 106.0)
    assert len(onos["pedidas"]) == 2


def test_si_onos_falla_no_se_identifica_y_no_se_martillea(onos):
    onos["error"] = pa.httpx.ConnectError("caido")
    assert _mac("10.0.1.100", lambda: 100.0) is None
    assert _mac("10.0.1.100", lambda: 101.0) is None
    assert len(onos["pedidas"]) == 1


def test_un_codigo_distinto_de_200_o_un_cuerpo_raro_no_se_identifica(onos):
    onos["resp"] = _Resp(500, {})
    assert _mac("10.0.1.100", lambda: 100.0) is None
    onos["resp"] = _Resp(200, {"otra_cosa": []})
    assert _mac("10.0.1.100", lambda: 200.0) is None


def test_un_host_nuevo_se_ve_pasado_el_tiempo_de_la_cache(onos):
    onos["resp"] = _Resp(200, {"hosts": []})
    assert _mac("10.0.1.100", lambda: 100.0) is None
    onos["resp"] = _Resp(200, {"hosts": HOSTS})
    assert _mac("10.0.1.100", lambda: 101.0) is None      # todavia la cache
    assert _mac("10.0.1.100", lambda: 106.0) == "fa:16:3e:b6:96:08"
