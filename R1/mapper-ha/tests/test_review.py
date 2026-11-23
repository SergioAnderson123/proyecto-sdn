"""Regression tests without cluster credentials or live ONOS requests."""
import asyncio
import copy
from types import SimpleNamespace
import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import eventos
import ha_store
import ha_runtime
import ha_onos

class MemoryEtcd:
    def __init__(self):
        self.values = {}
        self.revision = 0
    def get(self, key):
        return copy.deepcopy(self.values.get(key)), self.revision
    def cas(self, key, value, revision, owner=None):
        if revision != self.revision:
            raise ha_store.Unavailable('stale revision')
        self.revision += 1
        self.values[key] = copy.deepcopy(value)
        return self.revision

@pytest.fixture
def quorum(monkeypatch):
    client = MemoryEtcd()
    monkeypatch.setattr(ha_store, 'ENABLED', True)
    monkeypatch.setattr(ha_store, 'STORE', SimpleNamespace(client=client))
    monkeypatch.setattr(ha_store, 'fence', lambda: None)
    monkeypatch.setattr(ha_store, 'owner', lambda: 'test-owner')
    return client

def test_events_recovered_by_another_replica_and_restart(quorum, monkeypatch):
    eventos.registrar('admitido', 'usuario', 'AA:BB:CC:DD:EE:FF')
    # New process-local queue and another manager using the same quorum.
    eventos._eventos.clear()
    monkeypatch.setattr(ha_store, 'STORE', SimpleNamespace(client=quorum))
    assert eventos.listar()[0]['mac'] == 'aa:bb:cc:dd:ee:ff'
    assert eventos.listar()[0]['usuario'] == 'usuario'

def test_events_bounded_and_ordered(quorum):
    for n in range(eventos.MAX+10):
        eventos.registrar('admitido', str(n))
    assert len(eventos.listar(1000)) == eventos.MAX
    assert [e['usuario'] for e in eventos.listar(2)] == ['209', '208']
    eventos.limpiar()
    assert eventos.listar() == []

def test_events_do_not_fallback_to_local_on_quorum_loss(quorum, monkeypatch):
    def fail(*args): raise ha_store.Unavailable('no quorum')
    monkeypatch.setattr(quorum, 'get', fail)
    with pytest.raises(ha_store.Unavailable): eventos.listar()
    with pytest.raises(ha_store.Unavailable): eventos.registrar('admitido')

def test_events_require_writer_ownership(quorum, monkeypatch):
    def fail(): raise ha_store.Unavailable('lost lease')
    monkeypatch.setattr(ha_store, 'fence', fail)
    with pytest.raises(ha_store.Unavailable): eventos.registrar('admitido')
    assert quorum.values == {}

def test_events_http_reads_without_writer_lock_and_returns_503(quorum, monkeypatch):
    eventos.registrar('admitido', 'usuario')
    mapper = SimpleNamespace(app=FastAPI(), session_store=SimpleNamespace(client=quorum))
    ha_runtime.install(mapper)
    client = TestClient(mapper.app)
    assert client.get('/events').json()['events'][0]['usuario'] == 'usuario'
    assert client.get('/events?limit=abc').status_code == 422
    def fail(*args): raise ha_store.Unavailable('no quorum')
    monkeypatch.setattr(quorum, 'get', fail)
    assert client.get('/events').status_code == 503
    assert client.get('/sessions').status_code == 503
    assert client.get('/health/ha').status_code == 503

def test_onos_get_retries_another_controller(monkeypatch):
    monkeypatch.setattr(ha_onos, 'ENDPOINTS', ['http://one', 'http://two'])
    monkeypatch.setattr(ha_onos, 'SOURCE', 'one')
    visited=[]
    def reply(request):
        visited.append(request.url.host)
        if request.url.host == 'one': raise httpx.ConnectError('down', request=request)
        return httpx.Response(200, json={'devices': []})
    async def run():
        async with ha_onos.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            assert (await client.get('http://one/onos/v1/devices')).status_code == 200
    asyncio.run(run())
    assert visited == ['one', 'two']

def test_onos_post_not_replayed_after_ambiguous_timeout(monkeypatch):
    monkeypatch.setattr(ha_onos, 'ENDPOINTS', ['http://one', 'http://two'])
    monkeypatch.setattr(ha_onos, 'SOURCE', 'one')
    monkeypatch.setattr(ha_onos, '_MASTER_CACHE', {})
    monkeypatch.setattr(ha_store, 'fence', lambda: None)
    visited=[]
    def reply(request):
        visited.append((request.method, request.url.host, request.url.path))
        if request.url.path.endswith('/master'):
            return httpx.Response(200, json={'nodeId':'one'})
        raise httpx.ReadTimeout('response lost', request=request)
    async def run():
        async with ha_onos.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.post('http://one/onos/v1/flows/of:1', json={})
    asyncio.run(run())
    assert [x for x in visited if x[0] == 'POST'] == [('POST', 'one', '/onos/v1/flows/of:1')]


def test_lectura_de_flujos_se_dirige_al_maestro_del_switch(monkeypatch):
    monkeypatch.setattr(ha_onos, 'ENDPOINTS', ['http://one:8181', 'http://two:8181'])
    monkeypatch.setattr(ha_onos, 'SOURCE', 'one')
    monkeypatch.setattr(ha_onos, '_MASTER_CACHE', {})
    visited=[]
    def reply(request):
        visited.append((request.method, request.url.host, request.url.path))
        if request.url.path.endswith('/master'):
            return httpx.Response(200, json={'nodeId':'two'})
        return httpx.Response(200, json={'flows': []})
    async def run():
        async with ha_onos.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            response=await client.get('http://one:8181/onos/v1/flows/of:device', auth=('onos','rocks'))
            assert response.status_code == 200
    asyncio.run(run())
    assert visited == [
        ('GET', 'one', '/onos/v1/mastership/of:device/master'),
        ('GET', 'two', '/onos/v1/flows/of:device'),
    ]


def test_master_desconocido_conserva_el_failover_ordinario(monkeypatch):
    monkeypatch.setattr(ha_onos, 'ENDPOINTS', ['http://one:8181', 'http://two:8181'])
    monkeypatch.setattr(ha_onos, 'SOURCE', 'one')
    monkeypatch.setattr(ha_onos, '_MASTER_CACHE', {})
    visited=[]
    def reply(request):
        visited.append((request.method, request.url.host))
        if request.url.path.endswith('/master'):
            return httpx.Response(404)
        if request.url.host == 'one':
            raise httpx.ConnectError('controller down', request=request)
        return httpx.Response(200, json={'flows': []})
    async def run():
        async with ha_onos.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            response=await client.get('http://one:8181/onos/v1/flows/of:device')
            assert response.status_code == 200
    asyncio.run(run())
    assert visited[-2:] == [('GET', 'one'), ('GET', 'two')]
