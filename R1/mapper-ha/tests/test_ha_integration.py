"""Uses an isolated etcd prefix only. Never calls the ONOS data plane."""
import asyncio, os, time, concurrent.futures, subprocess, sys
import pytest
# Never allow this mutating suite to use the production prefix.
if os.getenv('SESSION_ETCD_ENDPOINTS') and not os.getenv('SESSION_ETCD_PREFIX', '').startswith('/sdn/mapper/test/'):
 pytest.skip('requires an explicit /sdn/mapper/test/ prefix', allow_module_level=True)
import ha_store
from session_manager import session_store as store,ActiveSession
from ha_sessions import decode

pytestmark=pytest.mark.skipif(not ha_store.ENABLED,reason='requires isolated quorum')

def run(fn):
 async def op():
  async with ha_store.transaction(): return fn()
 return asyncio.run(op())

def test_01_registered_on_one_process_recovered_on_another():
 run(lambda:store.register_session(ActiveSession('ha-test','02:00:00:fe:01:01','test-switch','1',10,'Docente','test-flow')))
 other=type(store)();other.refresh()
 assert other.get_by_mac('02:00:00:fe:01:01').username=='ha-test'
 assert other.get_by_port('test-switch','1').vlan==10
 assert other.get_by_username('HA-TEST').flow_id=='test-flow'

def test_02_restart_preserves_nested_base_and_expiry():
 base=ActiveSession('parent','02:00:00:fe:01:02','test-switch','2',20,'Alumno','base-flow')
 end=time.time()+180
 run(lambda:store.register_session(ActiveSession('raised',base.mac,base.dpid,base.port,10,'Docente','raised-flow','Portal',end,base)))
 other=type(store)();other.refresh();s=other.get_by_mac(base.mac)
 assert s.base.username=='parent' and s.expira==end
 assert other.vencidas(end+1)[0].mac==base.mac

def test_03_revocation_survives_refresh_and_records_deletion():
 run(lambda:store.remove_session('02:00:00:fe:01:01'))
 other=type(store)();other.refresh()
 assert other.get_by_mac('02:00:00:fe:01:01') is None
 assert 'test-switch/test-flow' in other.outbox

def test_04_old_revision_cannot_overwrite_new_state():
 def op():
  before=store.revision;state=store.snapshot()
  store.register_session(ActiveSession('new','02:00:00:fe:01:03','test-switch','3',20,'Alumno','new-flow'))
  with pytest.raises(ha_store.Unavailable):store.client.cas('state',state,before,ha_store.owner())
 run(op)

def test_05_write_without_lease_is_rejected():
 with pytest.raises(ha_store.Unavailable):store.register_session(ActiveSession('bad','02:00:00:fe:01:ff','test-switch','99',10,'Docente'))

def test_06_lost_lease_cannot_write():
 async def op():
  async with ha_store.transaction():
   t=ha_store._current.get();t.client.release(t.lease)
   with pytest.raises(ha_store.Unavailable):store.remove_session('02:00:00:fe:01:03')
 with pytest.raises(ha_store.Unavailable):asyncio.run(op())

def test_07_mutations_from_separate_processes_do_not_lose_updates():
 def register(n):
  code='''import asyncio\nfrom session_manager import session_store,ActiveSession\nfrom ha_store import transaction\nasync def go():\n async with transaction():\n  session_store.register_session(ActiveSession("worker-N","02:00:00:fe:02:NN","test-switch","NN",20,"Alumno","flow-N"))\nasyncio.run(go())'''.replace('NN',f'{n:02x}').replace('worker-N',f'worker-{n}').replace('flow-N',f'flow-{n}')
  subprocess.run([sys.executable,'-c',code],check=True,timeout=30)
 with concurrent.futures.ThreadPoolExecutor(3) as ex:list(ex.map(register,[1,2,3]))
 other=type(store)();other.refresh()
 assert all(other.get_by_username(f'worker-{n}') for n in [1,2,3])

def test_08_replacement_clears_previous_location_index():
 def op():
  store.register_session(ActiveSession('moved','02:00:00:fe:01:03','new-switch','4',20,'Alumno','newer-flow'))
  assert store.get_by_port('test-switch','3') is None
 run(op)

def test_09_admission_intent_and_blacklist_are_durable():
 def op():
  store.plan_admission('new-switch','4','02:00:00:fe:01:03',20)
  store.plan_block('02:00:00:fe:01:03',60)
 run(op)
 other=type(store)();other.refresh()
 assert other.intents['02:00:00:fe:01:03']['vlan']==20
 assert not other.blocks['02:00:00:fe:01:03']['applied']

def test_10_other_replicas_observe_identical_state():
 state,rev=store.client.get('state')
 for endpoint in store.client.endpoints:
  s,r=ha_store.Etcd([endpoint]).get('state')
  assert s==state and r==rev
