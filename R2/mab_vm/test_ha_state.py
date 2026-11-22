"""Tests for R2 shared-state fencing, quota serialization, and restart recovery."""
import threading
import unittest
from unittest.mock import Mock, patch

import ha_state
from mab_vm import MabVM, Parent, Denied


DPID = "of:00001a749039894a"
PARENT = Parent("fa:16:3e:6b:69:bb", DPID, "2", 30, "Alumno", "parent-1", True)


class FakeEtcd:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = None
        self.revision = 0

    def acquire(self, timeout=8):
        self.lock.acquire(timeout=timeout)
        return "test-owner", "test-lease"

    def renew(self, lease):
        return None

    def verify_owner(self, owner):
        if owner != "test-owner":
            raise ha_state.StateUnavailable("bad owner")

    def get(self, name):
        if name == "state":
            return self.state, self.revision
        if name == "lock":
            return "test-owner", 1
        raise AssertionError(name)

    def commit(self, snapshot, revision, owner):
        if owner != "test-owner" or revision != self.revision:
            raise ha_state.StateUnavailable("stale transaction")
        self.state = snapshot
        self.revision += 1

    def call(self, path, body):
        if path == "lease/revoke":
            self.lock.release()
            return {}
        raise AssertionError(path)


def make_service(mac_suffix):
    return MabVM(
        Mock(return_value=PARENT),
        Mock(return_value="flow-" + mac_suffix),
        Mock(return_value=True),
        {(DPID, "2")}, quota=1,
    )


class SharedStateTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeEtcd()
        self.client_patch = patch.object(ha_state, "CLIENT", self.fake)
        self.client_patch.start()
        self.enable_patch = patch.object(ha_state, "ENABLED", True)
        self.enable_patch.start()
        self.init_patch = patch.dict("os.environ", {"R2_ETCD_INIT_EMPTY": "1"})
        self.init_patch.start()

    def tearDown(self):
        self.init_patch.stop()
        self.enable_patch.stop()
        self.client_patch.stop()

    def test_two_replicas_share_quota_under_concurrent_admission(self):
        services = [make_service("a"), make_service("b")]
        macs = ["02:00:00:00:00:0a", "02:00:00:00:00:0b"]
        barrier = threading.Barrier(2)
        results = []

        def admit(service, mac):
            barrier.wait()
            try:
                with ha_state.transaction(service):
                    child = service.admit(mac, DPID, "2")
                results.append(("ok", child.mac))
            except Denied as error:
                results.append(("denied", str(error)))

        threads = [threading.Thread(target=admit, args=pair)
                   for pair in zip(services, macs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([result[0] for result in results].count("ok"), 1)
        self.assertEqual([result[0] for result in results].count("denied"), 1)
        self.assertEqual(len(self.fake.state["children"]), 1)

    def test_new_replica_recovers_sessions_from_shared_snapshot(self):
        service = make_service("a")
        with ha_state.transaction(service):
            service.admit("02:00:00:00:00:0a", DPID, "2")

        restarted = make_service("b")
        with ha_state.transaction(restarted):
            self.assertIn("02:00:00:00:00:0a", restarted.children)
            self.assertEqual(restarted.children["02:00:00:00:00:0a"].flow_id, "flow-a")

    def test_missing_store_does_not_overwrite_nonempty_local_state(self):
        service = make_service("a")
        service.children["02:00:00:00:00:0a"] = Mock(
            mac="02:00:00:00:00:0a", parent_id="parent-1", dpid=DPID,
            port="2", vlan=30, flow_id="existing-flow", state="ACTIVE",
        )
        with self.assertRaises(ha_state.StateUnavailable):
            with ha_state.transaction(service):
                pass
        self.assertIsNotNone(service.children["02:00:00:00:00:0a"])
        self.assertIsNone(self.fake.state)


if __name__ == "__main__":
    unittest.main()
