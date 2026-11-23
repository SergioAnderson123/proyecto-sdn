"""One-time, non-destructive import of an existing R2 local snapshot into etcd.

The source file is only read. The shared key is created with a compare-and-set
against an absent key while holding the same distributed lease as live writers.
An existing etcd snapshot is never overwritten.
"""
import argparse
import json
from pathlib import Path

import ha_state
from mab_vm import MabVM, Denied


REQUIRED_CHILD_FIELDS = {"mac", "parent_id", "dpid", "port", "vlan", "flow_id", "state"}


def read_snapshot(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or set(data) != {"children", "revoking"}:
        raise ValueError("snapshot must contain exactly children and revoking")
    children = data["children"]
    revoking = data["revoking"]
    if not isinstance(children, list) or not isinstance(revoking, list):
        raise ValueError("children and revoking must be lists")

    seen = set()
    for child in children:
        if not isinstance(child, dict) or set(child) != REQUIRED_CHILD_FIELDS:
            raise ValueError("invalid child record; snapshot was not imported")
        mac = child["mac"]
        if not isinstance(mac, str) or mac.lower() in seen:
            raise ValueError("invalid or duplicate child MAC; snapshot was not imported")
        try:
            if MabVM.normalize(mac) != mac:
                raise ValueError("child MAC must be normalized")
        except Denied as error:
            raise ValueError("invalid child MAC") from error
        seen.add(mac.lower())
        if child["state"] not in {"ACTIVE", "REVOKING"}:
            raise ValueError("invalid child state; snapshot was not imported")
        if type(child["vlan"]) is not int or not 1 <= child["vlan"] <= 4094:
            raise ValueError("invalid child VLAN; snapshot was not imported")
        if not all(isinstance(child[key], str) and child[key] for key in
                   ("parent_id", "dpid", "port", "flow_id")):
            raise ValueError("invalid child identifiers; snapshot was not imported")
    if any(not isinstance(value, str) or not value for value in revoking):
        raise ValueError("invalid revoking entry; snapshot was not imported")

    return {"children": children, "revoking": sorted(set(revoking))}


def import_snapshot(snapshot, client=None):
    """Create the shared state once; refuse if etcd already has any snapshot."""
    if not ha_state.ENABLED and client is None:
        raise ha_state.StateUnavailable("R2_ETCD_ENDPOINTS is required")
    client = client or ha_state.CLIENT
    owner, lease_id = client.acquire()
    try:
        current, revision = client.get("state")
        if current is not None or revision:
            raise RuntimeError("etcd already contains R2 state; refusing to overwrite it")
        client.verify_owner(owner)
        client.commit(snapshot, revision, owner)
        return len(snapshot["children"])
    finally:
        try:
            client.call("lease/revoke", {"ID": lease_id})
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="existing /state/sessions.json")
    parser.add_argument("--apply", action="store_true", help="write once to the configured etcd prefix")
    args = parser.parse_args()
    snapshot = read_snapshot(args.snapshot)
    if not args.apply:
        print(f"Valid snapshot: {len(snapshot['children'])} child session(s), "
              f"{len(snapshot['revoking'])} revocation(s). No changes made. Use --apply to import.")
        return
    count = import_snapshot(snapshot)
    print(f"Imported {count} child session(s). Source file was left unchanged.")


if __name__ == "__main__":
    main()
