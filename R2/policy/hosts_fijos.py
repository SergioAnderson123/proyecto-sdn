"""hosts_fijos.py: hosts que ONOS no aprende solos (el server con sus dos tarjetas, el portal) y que el vigilante debe conocer siempre.

Archivo JSON (lista), p. ej. /etc/vigilante/hosts_fijos.json:
    [{"nombre": "server-ens4", "mac": "52:54:00:aa:bb:01", "device": "core-1", "port": 5},
     {"nombre": "server-ens5", "mac": "52:54:00:aa:bb:02", "device": "core-2", "port": 5}]
`device` puede ser el nombre (borde, core-1, core-2, acc-1, acc-2) o el deviceId `of:...`. Un archivo mal formado detiene el arranque: es
preferible no arrancar a ignorar en silencio un servidor.
"""
import json
import re

from plan_arboles import NOMBRES

_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)


def cargar(ruta, nombres=None):
    """Lee y valida el archivo. Devuelve [{mac (MAYÚSCULAS), device (of:...), port (int), nombre}]. Lanza ValueError si algo no cuadra."""
    nombres = NOMBRES if nombres is None else nombres
    por_nombre = {v: k for k, v in nombres.items()}
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    if not isinstance(datos, list):
        raise ValueError(f"{ruta}: debe ser una lista de hosts")
    salida, vistas = [], set()
    for i, h in enumerate(datos):
        try:
            mac, dev, puerto = str(h["mac"]), str(h["device"]), int(h["port"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{ruta}: el host #{i + 1} necesita mac, device y port ({e!r})")
        dev = por_nombre.get(dev, dev)
        if not _MAC.match(mac):
            raise ValueError(f"{ruta}: MAC inválida en el host #{i + 1}: {mac}")
        if not dev.startswith("of:"):
            raise ValueError(f"{ruta}: dispositivo desconocido en el host #{i + 1}: {h['device']}")
        if mac.upper() in vistas:
            raise ValueError(f"{ruta}: MAC repetida: {mac}")
        vistas.add(mac.upper())
        salida.append({"mac": mac.upper(), "device": dev, "port": puerto, "ips": [], "nombre": h.get("nombre", mac)})
    return salida
