"""Safety tests for the one-time R2 state importer."""
import json
import tempfile
import unittest
from pathlib import Path

import ha_state
from migrate_state import import_snapshot, read_snapshot


CHILD = {
    "mac": "02:00:00:00:00:31", "parent_id": "parent-1",
    "dpid": "of:00001a749039894a", "port": "2", "vlan": 30,
    "flow_id": "flow-1", "state": "ACTIVE",
}


class FakeStore:
    def __init__(self, current=None, revision=0):
        self.current, self.revision = current, revision
        self.owner = None
        self.writes = 0
        self.revokes = 0

    def acquire(self):
        self.owner = "test-owner"
        return self.owner, "test-lease"

    def get(self, key):
        if key == "state":
            return self.current, self.revision
        raise AssertionError(key)

    def verify_owner(self, owner):
        if owner != self.owner:
            raise ha_state.StateUnavailable("lock owner mismatch")

    def commit(self, snapshot, revision, owner):
        self.verify_owner(owner)
        if revision != self.revision or self.current is not None:
            raise ha_state.StateUnavailable("state already exists")
        self.current, self.revision = snapshot, 1
        self.writes += 1

    def call(self, path, body):
        if path == "lease/revoke":
            self.revokes += 1
            return {}
        raise AssertionError(path)


class MigrationTests(unittest.TestCase):
    def test_dry_snapshot_read_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            original = {"children": [CHILD], "revoking": ["parent-1"]}
            path.write_text(json.dumps(original))
            parsed = read_snapshot(path)
            self.assertEqual(parsed, original)
            self.assertEqual(json.loads(path.read_text()), original)

    def test_import_creates_absent_snapshot_once(self):
        store = FakeStore()
        count = import_snapshot({"children": [CHILD], "revoking": []}, store)
        self.assertEqual(count, 1)
        self.assertEqual(store.current["children"], [CHILD])
        self.assertEqual(store.writes, 1)
        self.assertEqual(store.revokes, 1)

    def test_existing_snapshot_is_never_overwritten(self):
        existing = {"children": [], "revoking": []}
        store = FakeStore(existing, revision=4)
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            import_snapshot({"children": [CHILD], "revoking": []}, store)
        self.assertEqual(store.current, existing)
        self.assertEqual(store.writes, 0)
        self.assertEqual(store.revokes, 1)

    def test_missing_fields_cannot_be_mistaken_for_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            for malformed in ({}, {"children": []}, {"revoking": []}):
                path.write_text(json.dumps(malformed))
                with self.assertRaises(ValueError):
                    read_snapshot(path)

    def test_unknown_child_fields_cannot_break_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            path.write_text(json.dumps({"children": [{**CHILD, "unknown": 1}], "revoking": []}))
            with self.assertRaises(ValueError):
                read_snapshot(path)

    def test_invalid_record_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            path.write_text(json.dumps({"children": [{"mac": "bad"}], "revoking": []}))
            with self.assertRaisesRegex(ValueError, "invalid child record"):
                read_snapshot(path)


if __name__ == "__main__":
    unittest.main()
