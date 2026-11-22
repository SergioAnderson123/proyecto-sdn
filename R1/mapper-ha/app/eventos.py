"""eventos.py - bitácora en memoria de los accesos que decide el Policy Mapper (la lee el panel de accesos vía GET /events).

Guarda los últimos MAX eventos: admitido, rechazado, conflicto (MAC/usuario duplicado), logout y revocado (R5), cada uno con el
usuario, la MAC, el puerto y el motivo. No persiste: se reinicia con el mapper, igual que el gestor de sesiones.
"""
import time
from collections import deque
from typing import List, Optional

MAX = 200
TIPOS = ("admitido", "rechazado", "conflicto", "logout", "revocado")
_eventos: deque = deque(maxlen=MAX)


def registrar(tipo: str, usuario: str = "", mac: str = "", dpid: str = "", port: str = "", detalle: str = "",
              rol: str = "", vlan: Optional[int] = None) -> dict:
    assert tipo in TIPOS, tipo
    ev = {"t": time.time(), "tipo": tipo, "usuario": usuario, "mac": (mac or "").lower(), "dpid": dpid,
          "port": str(port) if port != "" else "", "detalle": detalle, "rol": rol, "vlan": vlan}
    _eventos.append(ev)
    return ev


def listar(limite: int = 50) -> List[dict]:
    """Los más recientes primero."""
    return list(_eventos)[::-1][:max(1, limite)]


def limpiar() -> None:
    _eventos.clear()
