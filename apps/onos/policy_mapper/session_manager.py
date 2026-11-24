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
    metodo: str = "802.1X"       # "802.1X" (RADIUS) o "Portal" (usuario y contraseña)
    expira: Optional[float] = None   # instante (time.time()) en que vence; None = no vence
    base: Optional["ActiveSession"] = None   # ascenso por el portal: la sesion 802.1X del equipo que esta reemplaza (se restaura al cerrar o vencer)


class SessionManager:

    def __init__(self):
        # Índice por MAC física del host: mac -> ActiveSession
        self._sessions_by_mac: Dict[str, ActiveSession] = {}
        # Índice por ubicación física del conmutador: "dpid:port" -> ActiveSession
        self._sessions_by_port: Dict[str, ActiveSession] = {}
        self._sessions_by_user: Dict[str, ActiveSession] = {}

    def vencidas(self, ahora: float) -> list:
        """Sesiones cuyo plazo ya pasó."""
        return [s for s in list(self._sessions_by_mac.values()) if s.expira is not None and s.expira <= ahora]

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
        self._sessions_by_user[session.username.lower()] = session

    def remove_session(self, mac: str) -> Optional[ActiveSession]:
        mac = mac.lower()
        session = self._sessions_by_mac.pop(mac, None)
        if session:
            port_key = f"{session.dpid}:{session.port}"
            self._sessions_by_port.pop(port_key, None)
            # solo si sigue siendo la misma sesión (una más nueva del mismo usuario ya pudo haber pisado el índice)
            if self._sessions_by_user.get(session.username.lower()) is session:
                self._sessions_by_user.pop(session.username.lower(), None)
        return session

    def is_spoofing(self, mac: str, dpid: str, port: str) -> bool:
        """Verifica si la MAC ya cuenta con una sesión en otra boca física."""
        session = self.get_by_mac(mac)
        if not session:
            return False
        return session.dpid != dpid or str(session.port) != str(port)


# Instancia única singleton para la aplicación
session_store = SessionManager()