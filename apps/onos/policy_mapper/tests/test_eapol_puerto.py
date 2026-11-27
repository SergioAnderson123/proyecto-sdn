"""El EAPOL de un host con sesion debe seguir llegando a ONOS.

El flujo de sesion (Prio 40001, in_port + eth_src) es mayor que la regla de ONOS AAA para el EAPOL (40000): sin una regla
propia por encima, el EAPOL-Logoff y el EAPOL-Start de un host ya autenticado se van a la tabla de rol y se descartan, el
host espera 60 s y falla, y la sesion no se limpia. Medido en VNRT: la regla de EAPOL del switch se quedaba en n_packets=0.
"""
import asyncio
import os
import sys

import pytest

pytest.importorskip("fastapi")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy_mapper as pm  # noqa: E402

DPID = "of:00001a749039894a"
MAC = "fa:16:3e:39:6e:96"


class _Resp:
    status_code = 201
    headers = {"Location": "/onos/v1/flows/of:1/123"}
    text = ""


@pytest.fixture
def posts(monkeypatch):
    enviados = []

    async def post_falso(self, url, json=None, auth=None, timeout=None):
        enviados.append((url, json))
        return _Resp()

    monkeypatch.setattr(pm.httpx.AsyncClient, "post", post_falso)
    return enviados


def test_instalar_la_sesion_instala_tambien_la_regla_eapol_del_puerto(posts):
    flow_id = asyncio.run(pm.inyectar_flujo_tabla_0(DPID, "2", MAC, 10))
    assert flow_id == "123"
    sesion, eapol = [p for _, p in posts]
    assert sesion["priority"] == 40001
    assert eapol["priority"] == pm.P_EAPOL_PUERTO == 50000
    assert eapol["deviceId"] == DPID and eapol["tableId"] == 0 and eapol["isPermanent"] is True
    assert {"type": "IN_PORT", "port": 2} in eapol["selector"]["criteria"]
    assert {"type": "ETH_TYPE", "ethType": "0x888e"} in eapol["selector"]["criteria"]
    assert eapol["treatment"]["instructions"] == [{"type": "OUTPUT", "port": "CONTROLLER"}]


def test_la_regla_eapol_gana_a_la_sesion_y_a_la_regla_de_onos_aaa(posts):
    asyncio.run(pm.inyectar_flujo_tabla_0(DPID, "2", MAC, 10))
    sesion, eapol = [p for _, p in posts]
    assert eapol["priority"] > sesion["priority"] > 40000   # 40000 = la regla del EAPOL de ONOS AAA


def test_si_falla_la_regla_eapol_la_sesion_se_instala_igual(monkeypatch):
    async def post_falso(self, url, json=None, auth=None, timeout=None):
        if json["priority"] == pm.P_EAPOL_PUERTO:
            raise RuntimeError("ONOS no responde")
        return _Resp()

    monkeypatch.setattr(pm.httpx.AsyncClient, "post", post_falso)
    assert asyncio.run(pm.inyectar_flujo_tabla_0(DPID, "2", MAC, 10)) == "123"
