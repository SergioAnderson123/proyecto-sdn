"""The HA mapper must recognize the exact session rule before confirming a repeated login."""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy_mapper as pm  # noqa: E402
from session_manager import ActiveSession  # noqa: E402


def _session():
    return ActiveSession(
        username="profesor01@example.org", mac="fa:16:3e:b6:96:08",
        dpid="of:0000e6925d87354b", port="1", vlan=10, rol="Docente",
        flow_id="50665496667556007",
    )


def _flow(**overrides):
    flow = {
        "id": "50665496667556007", "priority": 40001, "tableId": 0, "state": "ADDED",
        "selector": {"criteria": [
            {"type": "IN_PORT", "port": "1"},
            {"type": "ETH_SRC", "mac": "FA:16:3E:B6:96:08"},
        ]},
        "treatment": {"instructions": [
            {"type": "L2MODIFICATION", "subtype": "VLAN_PUSH", "ethernetType": "0x8100"},
            {"type": "L2MODIFICATION", "subtype": "VLAN_ID", "vlanId": 10},
            {"type": "TABLE", "tableId": 10},
        ]},
    }
    flow.update(overrides)
    return flow


def test_acepta_la_regla_de_sesion_exacta():
    assert pm._flujo_cubre_sesion(_flow(), _session())


def test_rechaza_flujo_40001_ausente_removido_o_de_otro_match():
    session = _session()
    assert not pm._flujo_cubre_sesion(_flow(state="REMOVED"), session)
    assert not pm._flujo_cubre_sesion(_flow(state="PENDING_ADD"), session)
    assert not pm._flujo_cubre_sesion(_flow(priority=40000), session)
    assert not pm._flujo_cubre_sesion(_flow(selector={"criteria": [
        {"type": "IN_PORT", "port": "2"},
        {"type": "ETH_SRC", "mac": session.mac},
    ]}), session)


def test_rechaza_regla_con_vlan_incorrecta():
    flow = _flow()
    flow["treatment"]["instructions"][1]["vlanId"] = 20
    flow["treatment"]["instructions"][2]["tableId"] = 20
    assert not pm._flujo_cubre_sesion(flow, _session())


class _Response:
    def __init__(self, flows):
        self._flows = flows

    def raise_for_status(self):
        return None

    def json(self):
        return {"flows": self._flows}


class _Client:
    def __init__(self, sequence):
        self.sequence = sequence

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        return _Response(self.sequence.pop(0))


def test_login_repetido_no_reinstala_si_el_flujo_ya_esta(monkeypatch):
    sesion = _session()
    monkeypatch.setattr(pm, "HaAsyncClient", lambda: _Client([[_flow()]]))
    instalaciones = []

    async def instalar(*args):
        instalaciones.append(args)
        return sesion.flow_id

    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", instalar)
    import asyncio
    asyncio.run(pm._confirmar_flujo_sesion(sesion))
    assert instalaciones == []


def test_login_repetido_reinstala_y_verifica_el_flujo_ausente(monkeypatch):
    sesion = _session()
    respuestas = [[], [_flow()]]
    monkeypatch.setattr(pm, "HaAsyncClient", lambda: _Client(respuestas))
    instalaciones = []

    async def instalar(*args):
        instalaciones.append(args)
        return sesion.flow_id

    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", instalar)
    import asyncio
    asyncio.run(pm._confirmar_flujo_sesion(sesion))
    assert instalaciones == [(sesion.dpid, sesion.port, sesion.mac, sesion.vlan)]


def test_no_autoriza_si_ons_no_muestra_el_flujo_reinstalado(monkeypatch):
    sesion = _session()
    monkeypatch.setattr(pm, "HaAsyncClient", lambda: _Client([[], []]))

    async def instalar(*args):
        return sesion.flow_id

    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", instalar)
    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pm._confirmar_flujo_sesion(sesion))
    assert exc.value.status_code == 503


def test_reparacion_espera_pending_add_hasta_added(monkeypatch):
    import asyncio
    sesion = _session()
    respuestas = [[], [_flow(state="PENDING_ADD")], [_flow()]]
    monkeypatch.setattr(pm, "HaAsyncClient", lambda: _Client(respuestas))
    instalaciones = []

    async def instalar(*args):
        instalaciones.append(args)
        return sesion.flow_id

    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", instalar)
    asyncio.run(pm._confirmar_flujo_sesion(sesion))
    assert len(instalaciones) == 1
    assert not respuestas


def test_espera_acotada_si_onos_no_publica_flujo(monkeypatch):
    import asyncio

    async def ausente(sesion):
        return False

    monkeypatch.setattr(pm, "_flujo_sesion_presente", ausente)
    assert not asyncio.run(pm._esperar_flujo_sesion(_session(), timeout=0.02))


def test_espera_cancela_consulta_onos_atascada(monkeypatch):
    import asyncio
    cancelada = []

    async def atascada(sesion):
        try:
            await asyncio.sleep(60)
        finally:
            cancelada.append(True)

    monkeypatch.setattr(pm, "_flujo_sesion_presente", atascada)
    assert not asyncio.run(pm._esperar_flujo_sesion(_session(), timeout=0.02))
    assert cancelada == [True]
