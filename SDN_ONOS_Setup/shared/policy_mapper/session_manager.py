"""
session_manager.py
Gestor de sesiones activas en memoria para la arquitectura SDN Zero Trust.
Mantiene el anclaje físico (DPID, Puerto) y el ID de flujo en la TCAM de OVS.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ActiveSession:
    username: str
    mac: str
    dpid: str
    port: str
    vlan: int
    rol: str
    flow_id: Optional[str] = None


class SessionManager:

    def __init__(self):
        # Índice por MAC física del host: mac -> ActiveSession
        self._sessions_by_mac: Dict[str, ActiveSession] = {}
        # Índice por ubicación física del conmutador: "dpid:port" -> ActiveSession
        self._sessions_by_port: Dict[str, ActiveSession] = {}
        self._sessions_by_user: Dict[str, ActiveSession] = {}

    def get_by_mac(self, mac: str) -> Optional[ActiveSession]:
        return self._sessions_by_mac.get(mac.lower())

    def get_by_username(self, username: str) -> Optional[ActiveSession]:
        return self._sessions_by_user.get(username.lower())

    def get_by_port(self, dpid: str, port: str) -> Optional[ActiveSession]:
        key = f"{dpid}:{port}"
        return self._sessions_by_port.get(key)

    def register_session(self, session: ActiveSession) -> None:
        mac = session.mac.lower()
        port_key = f"{session.dpid}:{session.port}"

        self._sessions_by_mac[mac] = session
        self._sessions_by_port[port_key] = session

    def remove_session(self, mac: str) -> Optional[ActiveSession]:
        mac = mac.lower()
        session = self._sessions_by_mac.pop(mac, None)
        if session:
            port_key = f"{session.dpid}:{session.port}"
            self._sessions_by_port.pop(port_key, None)
        return session

    def is_spoofing(self, mac: str, dpid: str, port: str) -> bool:
        """Verifica si la MAC ya cuenta con una sesión en otra boca física."""
        session = self.get_by_mac(mac)
        if not session:
            return False
        return session.dpid != dpid or str(session.port) != str(port)


# Instancia única singleton para la aplicación
session_store = SessionManager()