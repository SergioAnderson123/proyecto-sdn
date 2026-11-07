import base64
import json
import os
import threading
import time
import urllib.request
import urllib.error
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mab_vm import MabVM, Parent, Child, Denied

R1 = os.getenv("R1_URL", "http://policy_mapper:5000").rstrip("/")
ONOS = os.getenv("ONOS_URL", "http://onos:8181").rstrip("/")
USER = os.getenv("ONOS_USER", "onos")
PASSWORD = os.getenv("ONOS_PASSWORD", "rocks")
AUTH = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
STATE = Path("/state/sessions.json")
ALLOWED = {
    tuple(value.strip().rsplit("/", 1))
    for value in os.getenv("VM_ACCESS_PORTS", "").split(",")
    if "/" in value
}
QUOTA = int(os.getenv("VM_MAX_CHILDREN", "2"))


def request(method, url, body=None, onos=False):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if onos:
        headers["Authorization"] = "Basic " + AUTH
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        raw = response.read()
        return (
            json.loads(raw) if raw else {},
            response.headers,
        )


def get_parent(dpid, port):
    sessions, _ = request("GET", R1 + "/sessions")

    try:
        aaa, _ = request(
            "GET",
            ONOS + "/onos/aaa/app/users",
            onos=True,
        )
    except urllib.error.HTTPError as error:
        if error.code == 404:
            aaa = {"entries": []}
        else:
            raise
    entries = aaa.get("entries", aaa.get("users", []))
    candidates = []

    for mac, session in sessions.get("sessions", {}).items():
        if (
            session.get("dpid") != dpid
            or str(session.get("port")) != str(port)
            or session.get("rol", "").lower() not in ("docente", "alumno")
            or session.get("session_type", "USER") != "USER"
            or session.get("state", "ACTIVE") != "ACTIVE"
        ):
            continue

        flow = session.get("flow_id")
        if not flow or flow == "active_flow":
            continue

        authorized = any(
            str(entry.get("macAddress", "")).lower() == mac.lower()
            and entry.get("subscriberId") == f"{dpid}/{port}"
            and str(entry.get(
                "state", entry.get("authState", "")
            )).upper() == "AUTHORIZED"
            for entry in entries
        )
        if not authorized:
            continue

        candidates.append(Parent(
            mac=mac.lower(),
            dpid=dpid,
            port=str(port),
            vlan=int(session["vlan"]),
            role=session["rol"],
            session_id=f"{dpid}/{port}/{mac.lower()}/{flow}",
            authorized=True,
        ))

    # Un segmento con varios padres no permite atribuir una VM.
    return candidates[0] if len(candidates) == 1 else None


def delete_flow(dpid, flow_id):
    url = f"{ONOS}/onos/v1/flows/{dpid}/{flow_id}"
    try:
        request("DELETE", url, onos=True)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return True
        raise

    # No olvidar la sesión hasta verificar que la regla desapareció.
    for _ in range(6):
        try:
            data, _ = request("GET", url, onos=True)
            flows = data.get("flows", [])
            if isinstance(flows, list) and (
                not flows or all(f.get("state") == "REMOVED" for f in flows)
            ):
                return True
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return True
            raise
        time.sleep(0.5)
    return False


def install_flow(dpid, port, mac, vlan):
    # Temporal para limitar reglas huérfanas si el servicio deja de funcionar.
    payload = {
        "priority": 40001,
        "timeout": 60,
        "isPermanent": False,
        "deviceId": dpid,
        "tableId": 0,
        "selector": {"criteria": [
            {"type": "IN_PORT", "port": int(port)},
            {"type": "ETH_SRC", "mac": mac},
        ]},
        "treatment": {"instructions": [
            {"type": "L2MODIFICATION", "subtype": "VLAN_PUSH",
             "ethernetType": "0x8100"},
            {"type": "L2MODIFICATION", "subtype": "VLAN_ID",
             "vlanId": vlan},
            {"type": "TABLE", "tableId": vlan},
        ]},
    }
    _, headers = request(
        "POST", f"{ONOS}/onos/v1/flows/{dpid}", payload, onos=True
    )
    location = headers.get("Location", "")
    if not location:
        raise RuntimeError("ONOS no devolvio Location; revisar sus flujos")
    flow_id = location.rstrip("/").split("/")[-1]

    for _ in range(8):
        data, _ = request(
            "GET", f"{ONOS}/onos/v1/flows/{dpid}/{flow_id}", onos=True
        )
        if any(f.get("state") == "ADDED" for f in data.get("flows", [])):
            return flow_id
        time.sleep(0.5)

    delete_flow(dpid, flow_id)
    raise RuntimeError("El switch no confirmo la instalacion")


service = MabVM(get_parent, install_flow, delete_flow, ALLOWED, QUOTA)


def save():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "children": [asdict(c) for c in service.children.values()],
        "revoking": list(service.revoking),
    }))
    temporary.replace(STATE)


if STATE.exists():
    data = json.loads(STATE.read_text())
    service.children = {
        child["mac"]: Child(**child) for child in data.get("children", [])
    }
    service.revoking = set(data.get("revoking", []))


def reconcile():
    with service.lock:
        groups = {}
        for child in service.children.values():
            groups.setdefault(child.parent_id, []).append(child)

        for parent_id, children in groups.items():
            first = children[0]
            try:
                parent = get_parent(first.dpid, first.port)
                valid = (
                    parent is not None
                    and parent.session_id == parent_id
                    and all(parent.vlan == c.vlan for c in children)
                )
            except Exception:
                valid = False

            if not valid or parent_id in service.revoking:
                try:
                    service.revoke_parent(parent_id)
                except Exception as error:
                    print("Revocacion pendiente:", str(error), flush=True)
                finally:
                    save()
                continue

            # Si una regla temporal expiró, liberar su sesión/cuota.
            for child in list(children):
                try:
                    data, _ = request(
                        "GET",
                        f"{ONOS}/onos/v1/flows/{child.dpid}/{child.flow_id}",
                        onos=True,
                    )
                    absent = not data.get("flows") or all(
                        f.get("state") == "REMOVED"
                        for f in data.get("flows", [])
                    )
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        continue
                    absent = True
                except Exception:
                    continue

                if absent:
                    service.children.pop(child.mac, None)
                    save()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/sessions":
            return self.reply(404, {"error": "NOT_FOUND"})
        with service.lock:
            self.reply(200, {
                "sessions": [asdict(c) for c in service.children.values()]
            })

    def do_POST(self):
        if self.path != "/api/mab-request":
            return self.reply(404, {"error": "NOT_FOUND"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("INVALID_BODY")
            data = json.loads(self.rfile.read(length))
            with service.lock:
                reconcile()
                child = service.admit(
                    data["mac"], data["deviceId"], str(data["port"])
                )
                save()
                self.reply(200, {"status": "FOUND", **asdict(child)})
        except Denied as error:
            self.reply(403, {"status": "DENIED", "reason": str(error)})
        except (ValueError, KeyError, TypeError):
            self.reply(422, {"error": "INVALID_REQUEST"})
        except Exception as error:
            print("Error de integracion:", str(error), flush=True)
            self.reply(503, {"error": "DEPENDENCY_UNAVAILABLE"})


def monitor():
    while True:
        try:
            reconcile()
        except Exception as error:
            print("Monitor:", str(error), flush=True)
        time.sleep(5)


threading.Thread(target=monitor, daemon=True).start()
print("R2 MAB VM escuchando en 5001", flush=True)
ThreadingHTTPServer(("0.0.0.0", 5001), Handler).serve_forever()
