"""Quorum-backed mapper state. No session data is attached to an expiring lease."""
import asyncio
import base64
import contextvars
import json
import logging
import os
import ssl
import threading
import time
import urllib.request
import urllib.error
import uuid
from contextlib import asynccontextmanager

class Unavailable(RuntimeError):
    pass

def b64(s):
    return base64.b64encode(s.encode()).decode()

class Etcd:
    def __init__(self, endpoints=None, prefix=None):
        self.endpoints = endpoints or os.environ['SESSION_ETCD_ENDPOINTS'].split(',')
        self.prefix = prefix or os.getenv('SESSION_ETCD_PREFIX', '/sdn/mapper/v1')
        self.context = ssl.create_default_context(cafile=os.environ['SESSION_CA'])
        self.context.load_cert_chain(os.environ['SESSION_CERT'], os.environ['SESSION_KEY'])
        self.preferred = 0

    def call(self, path, data):
        last = None
        for i in range(len(self.endpoints)):
            n = (self.preferred+i) % len(self.endpoints)
            req = urllib.request.Request(self.endpoints[n].rstrip('/')+'/v3/'+path,
                json.dumps(data).encode(), {'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req, context=self.context, timeout=2) as r:
                    result = json.loads(r.readline())
                if result.get('error') or result.get('code'):
                    raise Unavailable('etcd rejected request')
                self.preferred = n
                return result
            except (OSError, ValueError, urllib.error.URLError) as e:
                last = type(e).__name__
        raise Unavailable('Session quorum unavailable: '+str(last))

    def get(self, name):
        d = self.call('kv/range', {'key':b64(self.prefix+'/'+name)})
        kv = d.get('kvs', [])
        if not kv: return None, 0
        return json.loads(base64.b64decode(kv[0]['value'])), int(kv[0]['mod_revision'])

    def cas(self, name, value, revision, owner=None):
        compare = [{'key':b64(self.prefix+'/'+name), 'target':'MOD', 'result':'EQUAL', 'mod_revision':str(revision)}]
        if owner:
            compare.append({'key':b64(self.prefix+'/lock'), 'target':'VALUE','result':'EQUAL','value':b64(json.dumps(owner))})
        d = self.call('kv/txn', {'compare':compare, 'success':[{'request_put':{'key':b64(self.prefix+'/'+name),'value':b64(json.dumps(value,ensure_ascii=False))}}]})
        if not d.get('succeeded'): raise Unavailable('Session revision or ownership changed')
        return int(d['header']['revision'])

    def acquire(self):
        owner = uuid.uuid4().hex
        lease = str(self.call('lease/grant', {'TTL':30})['ID'])
        d = self.call('kv/txn', {'compare':[{'key':b64(self.prefix+'/lock'),'target':'VERSION','result':'EQUAL','version':'0'}],
          'success':[{'request_put':{'key':b64(self.prefix+'/lock'),'value':b64(json.dumps(owner)),'lease':lease}}]})
        if not d.get('succeeded'):
            self.call('lease/revoke', {'ID':lease})
            return None
        return owner, lease

    def renew(self, lease):
        d = self.call('lease/keepalive', {'ID':lease}).get('result', {})
        if int(d.get('TTL',0)) <= 0: raise Unavailable('Writer lease expired')

    def release(self, lease):
        self.call('lease/revoke', {'ID':lease})

ENABLED = bool(os.getenv('SESSION_ETCD_ENDPOINTS'))
STORE = None
_gate = asyncio.Lock()
_current = contextvars.ContextVar('session_transaction', default=None)

class Transaction:
    def __init__(self, client, owner, lease):
        self.client, self.owner, self.lease = client, owner, lease
        self.stop = threading.Event()
        self.failed = False
        self.thread = threading.Thread(target=self._renew, daemon=True)
    def _renew(self):
        while not self.stop.wait(5):
            try: self.client.renew(self.lease)
            except Exception:
                self.failed=True
                return
    def verify(self):
        if self.failed: raise Unavailable('Lost session writer lease')
        owner,_ = self.client.get('lock')
        if owner != self.owner: raise Unavailable('No longer session writer')

@asynccontextmanager
async def transaction():
    if not ENABLED:
        yield
        return
    if _current.get() is not None:
        _current.get().verify()
        yield
        return
    queued = time.monotonic()
    async with _gate:
        deadline=time.monotonic()+8
        lease=None
        while lease is None:
            lease=await asyncio.to_thread(STORE.client.acquire)
            if lease is None:
                if time.monotonic()>deadline: raise Unavailable('Another mapper is processing a session')
                await asyncio.sleep(.07)
        acquired = time.monotonic()
        owner, lease_id=lease
        t=Transaction(STORE.client,owner,lease_id)
        token=_current.set(t)
        t.thread.start()
        try:
            await asyncio.to_thread(STORE.refresh)
            yield
            t.verify()
        finally:
            t.stop.set()
            _current.reset(token)
            try: await asyncio.to_thread(STORE.client.release,lease_id)
            except Unavailable: pass
            logging.getLogger("uvicorn.error").info(
                "transaction wait_ms=%.2f hold_ms=%.2f",
                (acquired-queued)*1000, (time.monotonic()-acquired)*1000)

def fence():
    if not ENABLED: return
    t=_current.get()
    if t is None: raise Unavailable('Write outside a session transaction')
    t.verify()

def owner():
    fence()
    return _current.get().owner
