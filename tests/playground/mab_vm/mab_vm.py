"""R2: admision MAB de VM. La autenticacion del anfitrion pertenece a R1."""
from dataclasses import dataclass
from threading import RLock
import re


@dataclass(frozen=True)
class Parent:
    mac: str
    dpid: str
    port: str
    vlan: int
    role: str
    session_id: str
    authorized: bool


@dataclass
class Child:
    mac: str
    parent_id: str
    dpid: str
    port: str
    vlan: int
    flow_id: str
    state: str = "ACTIVE"


class Denied(Exception):
    pass


class MabVM:
    def __init__(self, get_parent, install_flow, delete_flow,
                 allowed_ports, quota=2):
        if quota < 0:
            raise ValueError("La cuota no puede ser negativa")
        # Adaptadores externos: R1 verifica al padre; ONOS aplica los flujos.
        self.get_parent = get_parent
        self.install_flow = install_flow
        self.delete_flow = delete_flow
        self.allowed_ports = set(allowed_ports)
        self.quota = quota
        self.children = {}
        self.revoking = set()
        self.lock = RLock()

    @staticmethod
    def normalize(mac):
        mac = mac.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac):
            raise Denied("INVALID_MAC")
        if mac == "00:00:00:00:00:00" or int(mac[:2], 16) & 1:
            raise Denied("INVALID_MAC")
        return mac

    def admit(self, mac, dpid, port):
        mac = self.normalize(mac)
        port = str(port)

        with self.lock:
            if (dpid, port) not in self.allowed_ports:
                raise Denied("PORT_NOT_ALLOWED")

            # Debe consultar una fuente confiable, no datos del solicitante.
            parent = self.get_parent(dpid, port)
            if not parent or not parent.authorized:
                raise Denied("NO_AUTHENTICATED_PARENT")
            if (parent.dpid, str(parent.port)) != (dpid, port):
                raise Denied("PARENT_LOCATION_MISMATCH")
            if parent.role.lower() not in ("docente", "alumno"):
                raise Denied("PARENT_ROLE_NOT_ALLOWED")
            if not parent.session_id or parent.session_id in self.revoking:
                raise Denied("PARENT_NOT_ACTIVE")
            if not 1 <= parent.vlan <= 4094:
                raise Denied("INVALID_PARENT_VLAN")
            if mac == self.normalize(parent.mac):
                raise Denied("MAC_IS_PARENT")

            existing = self.children.get(mac)
            if existing:
                expected = (parent.session_id, dpid, port, parent.vlan)
                actual = (existing.parent_id, existing.dpid,
                          existing.port, existing.vlan)
                if actual != expected or existing.state != "ACTIVE":
                    raise Denied("SESSION_CONFLICT")
                return existing

            used = sum(c.parent_id == parent.session_id
                       for c in self.children.values())
            if used >= self.quota:
                raise Denied("VM_QUOTA_EXCEEDED")

            # El adaptador debe confirmar la instalacion y devolver su ID.
            flow_id = self.install_flow(dpid, port, mac, parent.vlan)
            if not flow_id:
                raise Denied("FLOW_NOT_CONFIRMED")

            child = Child(mac, parent.session_id, dpid, port,
                          parent.vlan, str(flow_id))
            self.children[mac] = child
            return child

    def revoke_parent(self, session_id):
        """R1 llama esto al cerrar la sesion. Se puede reintentar si falla."""
        with self.lock:
            self.revoking.add(session_id)
            children = [c for c in self.children.values()
                        if c.parent_id == session_id]
            for child in children:
                child.state = "REVOKING"
            for child in children:
                if not self.delete_flow(child.dpid, child.flow_id):
                    raise Denied("FLOW_DELETE_NOT_CONFIRMED")
                del self.children[child.mac]
            # El ID cerrado permanece bloqueado; otra sesion usa otro ID.
            return len(children)
