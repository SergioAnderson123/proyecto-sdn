import unittest
from dataclasses import replace
from unittest.mock import Mock
from mab_vm import MabVM, Parent, Denied

DPID = "of:00001a749039894a"
MAC = "02:00:00:00:00:01"


class Tests(unittest.TestCase):
    def setUp(self):
        self.parent = Parent(
            "fa:16:3e:6b:69:bb", DPID, "2", 20,
            "Alumno", "sesion-r1-001", True
        )
        self.source = Mock(return_value=self.parent)
        self.install = Mock(return_value="flow-001")
        self.delete = Mock(return_value=True)
        self.service = MabVM(
            self.source, self.install, self.delete,
            {(DPID, "2")}, quota=1
        )

    def test_vlan_heredada(self):
        child = self.service.admit(MAC, DPID, "2")
        self.assertEqual(child.vlan, 20)
        self.assertEqual(child.parent_id, self.parent.session_id)

    def test_sin_padre(self):
        self.source.return_value = None
        with self.assertRaises(Denied):
            self.service.admit(MAC, DPID, "2")
        self.install.assert_not_called()

    def test_padre_no_autorizado(self):
        self.source.return_value = replace(self.parent, authorized=False)
        with self.assertRaises(Denied):
            self.service.admit(MAC, DPID, "2")

    def test_puerto_no_permitido(self):
        with self.assertRaises(Denied):
            self.service.admit(MAC, DPID, "3")

    def test_duplicado_no_consume_cuota(self):
        first = self.service.admit(MAC, DPID, "2")
        second = self.service.admit(MAC.upper(), DPID, "2")
        self.assertIs(first, second)
        self.install.assert_called_once()

    def test_limite_cuota(self):
        self.service.admit(MAC, DPID, "2")
        with self.assertRaisesRegex(Denied, "VM_QUOTA_EXCEEDED"):
            self.service.admit("02:00:00:00:00:02", DPID, "2")

    def test_fallo_instalacion(self):
        self.install.return_value = None
        with self.assertRaises(Denied):
            self.service.admit(MAC, DPID, "2")
        self.assertEqual(self.service.children, {})

    def test_conflicto_con_otro_padre(self):
        self.service.admit(MAC, DPID, "2")
        self.source.return_value = replace(self.parent, session_id="otra")
        with self.assertRaisesRegex(Denied, "SESSION_CONFLICT"):
            self.service.admit(MAC, DPID, "2")

    def test_revocacion(self):
        self.service.admit(MAC, DPID, "2")
        self.assertEqual(
            self.service.revoke_parent(self.parent.session_id), 1)
        self.assertEqual(self.service.children, {})

    def test_reintento_revocacion(self):
        self.service.admit(MAC, DPID, "2")
        self.delete.return_value = False
        with self.assertRaises(Denied):
            self.service.revoke_parent(self.parent.session_id)
        self.assertEqual(self.service.children[MAC].state, "REVOKING")
        self.delete.return_value = True
        self.service.revoke_parent(self.parent.session_id)
        self.assertEqual(self.service.children, {})


if __name__ == "__main__":
    unittest.main()
