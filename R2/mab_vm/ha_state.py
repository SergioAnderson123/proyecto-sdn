"""Optional quorum-backed state for replicated R2 MAB instances.

Disabled by default. When enabled, each mutating/read transaction reloads the
same snapshot while holding a leased etcd lock, then commits with a revision
and lock-owner compare. A missing snapshot fails closed unless an operator
explicitly enables empty initialization and the local state is empty.
"""
import base64
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager


class StateUnavailable(RuntimeError):
    pass


def _b64(value):
    return base64.b64encode(value.encode()).decode()


class EtcdV3:
    def __init__(self):
        self.endpoints = [x.rstrip("/") for x in os.environ["R2_ETCD_ENDPOINTS"].split(",") if x.strip()]
        if not self.endpoints:
            raise StateUnavailable("R2_ETCD_ENDPOINTS is empty")
        self.prefix = os.getenv("R2_ETCD_PREFIX", "/sdn/r2-mab/prod").rstrip("/")
        if not self.prefix.startswith("/"):
            raise StateUnavailable("R2_ETCD_PREFIX must be an absolute key prefix")
        context = ssl.create_default_context(cafile=os.environ["R2_ETCD_CA"])
        context.load_cert_chain(os.environ["R2_ETCD_CERT"], os.environ["R2_ETCD_KEY"])
        self.context = context
        self.preferred = 0

    def call(self, path, body):
        failure = None
        for offset in range(len(self.endpoints)):
            index = (self.preferred + offset) % len(self.endpoints)
            request = urllib.request.Request(
                self.endpoints[index] + "/v3/" + path,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, context=self.context, timeout=2) as response:
                    result = json.loads(response.read() or b"{}")
                if result.get("error") or result.get("code"):
                    raise StateUnavailable("etcd rejected request")
                self.preferred = index
                return result
            except (OSError, ValueError, urllib.error.URLError, StateUnavailable) as error:
                failure = type(error).__name__
        raise StateUnavailable("R2 etcd quorum unavailable: " + str(failure))

    def get(self, name):
        response = self.call("kv/range", {"key": _b64(self.prefix + "/" + name)})
        values = response.get("kvs", [])
        if not values:
            return None, 0
        return json.loads(base64.b64decode(values[0]["value"])), int(values[0]["mod_revision"])

    def acquire(self, timeout=8):
        deadline = time.monotonic() + timeout
        owner = uuid.uuid4().hex
        while time.monotonic() < deadline:
            lease = str(self.call("lease/grant", {"TTL": 30})["ID"])
            try:
                response = self.call("kv/txn", {
                    "compare": [{"key": _b64(self.prefix + "/lock"), "target": "VERSION", "result": "EQUAL", "version": "0"}],
                    "success": [{"request_put": {"key": _b64(self.prefix + "/lock"), "value": _b64(json.dumps(owner)), "lease": lease}}],
                })
                if response.get("succeeded"):
                    return owner, lease
            except StateUnavailable:
                # A timed-out response can still mean the transaction committed.
                # Reuse the lease only if etcd confirms this exact owner.
                try:
                    if self.get("lock")[0] == owner:
                        return owner, lease
                except StateUnavailable:
                    pass
                raise
            self.call("lease/revoke", {"ID": lease})
            time.sleep(0.07)
        raise StateUnavailable("R2 shared-state writer is busy")

    def renew(self, lease):
        response = self.call("lease/keepalive", {"ID": lease}).get("result", {})
        if int(response.get("TTL", 0)) <= 0:
            raise StateUnavailable("R2 writer lease expired")

    def verify_owner(self, owner):
        current, _ = self.get("lock")
        if current != owner:
            raise StateUnavailable("R2 writer lock was lost")

    def commit(self, snapshot, revision, owner):
        compare = [{"key": _b64(self.prefix + "/lock"), "target": "VALUE", "result": "EQUAL", "value": _b64(json.dumps(owner))}]
        if revision:
            compare.append({"key": _b64(self.prefix + "/state"), "target": "MOD", "result": "EQUAL", "mod_revision": str(revision)})
        else:
            compare.append({"key": _b64(self.prefix + "/state"), "target": "VERSION", "result": "EQUAL", "version": "0"})
        result = self.call("kv/txn", {
            "compare": compare,
            "success": [{"request_put": {"key": _b64(self.prefix + "/state"), "value": _b64(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))}}],
        })
        if not result.get("succeeded"):
            raise StateUnavailable("R2 state revision or lock owner changed")


class _Lease:
    def __init__(self, client, lease):
        self.client, self.lease = client, lease
        self.stop = threading.Event()
        self.failed = False
        self.thread = threading.Thread(target=self._renew, daemon=True)

    def _renew(self):
        while not self.stop.wait(5):
            try:
                self.client.renew(self.lease)
            except Exception:
                self.failed = True
                return

    def verify(self, owner):
        if self.failed:
            raise StateUnavailable("R2 writer lease renewal failed")
        self.client.verify_owner(owner)


_local = threading.local()
CLIENT = None
ENABLED = bool(os.getenv("R2_ETCD_ENDPOINTS"))
if ENABLED:
    CLIENT = EtcdV3()


def _snapshot(service):
    return {
        "children": [vars(child).copy() for child in service.children.values()],
        "revoking": sorted(service.revoking),
    }


def _restore(service, snapshot):
    from mab_vm import Child
    service.children = {item["mac"]: Child(**item) for item in snapshot.get("children", [])}
    service.revoking = set(snapshot.get("revoking", []))


@contextmanager
def transaction(service):
    """Serialize an operation across local threads and all R2 replicas."""
    if not ENABLED:
        with service.lock:
            yield
        return
    if getattr(_local, "owner", None) is not None:
        yield
        return

    with service.lock:
        owner, lease_id = CLIENT.acquire()
        lease = _Lease(CLIENT, lease_id)
        _local.owner = owner
        lease.thread.start()
        revision = 0
        before = None
        try:
            snapshot, revision = CLIENT.get("state")
            if snapshot is None:
                local_state = _snapshot(service)
                explicitly_empty = os.getenv("R2_ETCD_INIT_EMPTY") == "1"
                if not explicitly_empty or local_state["children"] or local_state["revoking"]:
                    raise StateUnavailable("R2 shared state is not initialized; refusing to discard local sessions")
                snapshot = local_state
            _restore(service, snapshot)
            before = json.dumps(_snapshot(service), sort_keys=True)
            try:
                yield
            except BaseException:
                # Reconcile may have marked children REVOKING before a network
                # failure. Persist that intent so another replica can retry it.
                lease.verify(owner)
                after_snapshot = _snapshot(service)
                if json.dumps(after_snapshot, sort_keys=True) != before:
                    CLIENT.commit(after_snapshot, revision, owner)
                raise
            else:
                lease.verify(owner)
                after_snapshot = _snapshot(service)
                if json.dumps(after_snapshot, sort_keys=True) != before or revision == 0:
                    CLIENT.commit(after_snapshot, revision, owner)
        finally:
            lease.stop.set()
            _local.owner = None
            try:
                CLIENT.call("lease/revoke", {"ID": lease_id})
            except Exception:
                pass
