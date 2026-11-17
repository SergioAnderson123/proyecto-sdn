"""Contrato AAA de ONOS observado en VNRT; todas las dependencias son simuladas."""
import unittest
from unittest.mock import patch

import server

DPID = "of:0000e6925d87354b"
MAC = "02:11:22:33:44:55"


class ParentContractTests(unittest.TestCase):
    def resolve(self, entry):
        sessions = {"sessions": {MAC: {
            "dpid": DPID, "port": "1", "rol": "Docente", "vlan": 10,
            "flow_id": "flow-parent", "state": "ACTIVE", "session_type": "USER",
        }}}
        with patch.object(server, "request", side_effect=[
            (sessions, {}), ({"entries": [entry]}, {}),
        ]):
            return server.get_parent(DPID, "1")

    def entry(self, **overrides):
        return {"macAddress": MAC, "connectPoint": f"{DPID}/1",
                "subscriberId": "of:0000000000000en4/1",
                "authState": "AUTHORIZED_STATE", **overrides}

    def test_vnrt_connect_point_and_authorized_state(self):
        parent = self.resolve(self.entry())
        self.assertIsNotNone(parent)
        self.assertEqual((parent.vlan, parent.port, parent.role), (10, "1", "Docente"))

    def test_wrong_connect_point_cannot_use_matching_subscriber_id(self):
        self.assertIsNone(self.resolve(self.entry(
            connectPoint=f"{DPID}/2", subscriberId=f"{DPID}/1")))

    def test_legacy_subscriber_id_and_state_remain_supported(self):
        entry = self.entry(subscriberId=f"{DPID}/1", state="AUTHORIZED")
        del entry["connectPoint"]
        self.assertIsNotNone(self.resolve(entry))

    def test_unauthorized_state_is_rejected(self):
        self.assertIsNone(self.resolve(self.entry(authState="UNAUTHORIZED_STATE")))

    def test_wrong_mac_is_rejected(self):
        self.assertIsNone(self.resolve(self.entry(macAddress="02:11:22:33:44:66")))

    def test_other_switch_is_rejected(self):
        self.assertIsNone(self.resolve(self.entry(connectPoint="of:0000000000000001/1")))

    def test_parent_mac_cannot_replace_the_r1_flow(self):
        from unittest.mock import Mock
        from mab_vm import MabVM, Denied
        parent = self.resolve(self.entry())
        install = Mock()
        service = MabVM(Mock(return_value=parent), install, Mock(), {(DPID, "1")}, quota=2)
        with self.assertRaisesRegex(Denied, "CHILD_MAC_IS_PARENT"):
            service.admit(MAC, DPID, "1")
        install.assert_not_called()


class FlowConfirmationTests(unittest.TestCase):
    def test_waits_for_onos_statistics_cycle(self):
        pending = ({"flows": [{"state": "PENDING_ADD"}]}, {})
        added = ({"flows": [{"state": "ADDED"}]}, {})
        replies = [({}, {"Location": f"/flows/{DPID}/123"})] + [pending] * 10 + [added]
        with patch.object(server, "request", side_effect=replies), patch.object(server.time, "sleep"):
            self.assertEqual(server.install_flow(DPID, "1", MAC, 10), "123")

    def test_unconfirmed_flow_is_removed(self):
        pending = ({"flows": [{"state": "PENDING_ADD"}]}, {})
        replies = [({}, {"Location": f"/flows/{DPID}/123"})] + [pending] * 12
        with patch.object(server, "request", side_effect=replies), patch.object(server.time, "sleep"), patch.object(server, "delete_flow", return_value=True) as delete:
            with self.assertRaisesRegex(RuntimeError, "no confirmo"):
                server.install_flow(DPID, "1", MAC, 10)
            delete.assert_called_once_with(DPID, "123")


if __name__ == "__main__":
    unittest.main()
