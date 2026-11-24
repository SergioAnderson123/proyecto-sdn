"""Caché de /onos/aaa/app/users: con varios logins a la vez (medido en el laboratorio: desde 5 negociaciones

simultáneas), cada webhook volvía a pedirle a ONOS la tabla COMPLETA por su cuenta. Se cachea un instante muy
corto (AAA_CACHE_TTL_S) y se comparte entre todas las llamadas que caigan dentro de esa ventana."""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy_mapper as pm  # noqa: E402


class _RespuestaFalsa:
    def __init__(self, cuerpo):
        self.status_code = 200
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


@pytest.fixture(autouse=True)
def _tabla_limpia():
    pm._cache_aaa_tabla = None
    pm._cache_aaa_hasta = 0.0
    pm._cache_aaa_peticiones_reales = 0
    yield
    pm._cache_aaa_tabla = None
    pm._cache_aaa_hasta = 0.0


def _onos_contador(monkeypatch, demora_s=0.0, entradas=None):
    llamadas = {"n": 0}

    async def get_falso(self, url, auth=None, timeout=None):
        llamadas["n"] += 1
        if demora_s:
            await asyncio.sleep(demora_s)
        return _RespuestaFalsa({"users": entradas or []})
    monkeypatch.setattr(pm.httpx.AsyncClient, "get", get_falso)
    return llamadas


def test_varias_llamadas_a_la_vez_comparten_una_sola_peticion_real(monkeypatch):
    llamadas = _onos_contador(monkeypatch, demora_s=0.01)

    async def veinte_a_la_vez():
        return await asyncio.gather(*[pm._tabla_aaa() for _ in range(20)])

    resultados = asyncio.run(veinte_a_la_vez())
    assert llamadas["n"] == 1                       # una sola petición HTTP sirvió a las 20 llamadas
    assert all(r == [] for r in resultados)


def test_llamadas_separadas_por_mas_del_ttl_si_vuelven_a_pedirla(monkeypatch):
    llamadas = _onos_contador(monkeypatch)
    pm.AAA_CACHE_TTL_S, anterior = 0.01, pm.AAA_CACHE_TTL_S
    try:
        asyncio.run(pm._tabla_aaa())
        time.sleep(0.02)
        asyncio.run(pm._tabla_aaa())
    finally:
        pm.AAA_CACHE_TTL_S = anterior
    assert llamadas["n"] == 2


def test_dentro_del_ttl_no_se_vuelve_a_pedir(monkeypatch):
    llamadas = _onos_contador(monkeypatch)
    pm.AAA_CACHE_TTL_S, anterior = 1.0, pm.AAA_CACHE_TTL_S
    try:
        asyncio.run(pm._tabla_aaa())
        asyncio.run(pm._tabla_aaa())
        asyncio.run(pm._tabla_aaa())
    finally:
        pm.AAA_CACHE_TTL_S = anterior
    assert llamadas["n"] == 1


def test_candidatos_onos_usa_la_tabla_cacheada_sin_pedirla_de_nuevo(monkeypatch):
    llamadas = _onos_contador(monkeypatch, entradas=[
        {"username": "profesor01@example.org", "subscriberId": "of:0000000000000001/1",
         "macAddress": "00:00:00:00:00:11", "state": "AUTHORIZED_STATE"},
        {"username": "alumno02@example.org", "subscriberId": "of:0000000000000001/2",
         "macAddress": "00:00:00:00:00:22", "state": "AUTHORIZED_STATE"},
    ])
    pm.AAA_CACHE_TTL_S, anterior = 1.0, pm.AAA_CACHE_TTL_S
    try:
        c1 = asyncio.run(pm.candidatos_onos("profesor01@example.org"))
        c2 = asyncio.run(pm.candidatos_onos("alumno02@example.org"))
    finally:
        pm.AAA_CACHE_TTL_S = anterior
    assert llamadas["n"] == 1                        # ambas consultas (usuarios distintos) comparten la misma tabla
    assert c1[0]["port"] == "1" and c2[0]["port"] == "2"
