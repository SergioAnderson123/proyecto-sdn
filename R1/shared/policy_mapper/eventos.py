"""eventos.py - bitácora en memoria de los accesos que decide el Policy Mapper (la lee el panel de accesos vía GET /events).

Guarda los últimos MAX eventos: admitido, rechazado, conflicto (MAC/usuario duplicado), logout y revocado (R5), cada uno con el
usuario, la MAC, el puerto y el motivo. En HA persiste en etcd bajo el mismo cerrojo que las sesiones; en modo local usa memoria.
"""
import ha_store
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
    if ha_store.ENABLED:
        ha_store.fence()
        client = ha_store.STORE.client
        previous, revision = client.get("events")
        client.cas("events", ((previous or []) + [ev])[-MAX:], revision, ha_store.owner())
    else:
        _eventos.append(ev)
    return ev


def listar(limite: int = 50) -> List[dict]:
    """Los más recientes primero."""
    if ha_store.ENABLED:
        events, _ = ha_store.STORE.client.get("events")
    else:
        events = list(_eventos)
    return (events or [])[::-1][:max(1, min(MAX, limite))]


def limpiar() -> None:
    if ha_store.ENABLED:
        ha_store.fence()
        client = ha_store.STORE.client
        _, revision = client.get("events")
        client.cas("events", [], revision, ha_store.owner())
    else:
        _eventos.clear()
