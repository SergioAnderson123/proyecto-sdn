"""puertos.py: nombres de interfaz de cada puerto OpenFlow, para mostrar `p4[ens7]` en vez de solo `p4`.

ONOS publica el nombre de la interfaz en `GET /onos/v1/devices/{id}/ports` (`annotations.portName`). Los números de puerto de OpenFlow NO
coinciden con los de las interfaces (p4 de sw-core-1 es ens7, no ens4): por eso conviene verlos juntos.
"""
from typing import Dict, Iterable, Optional, Tuple

Nombres = Dict[Tuple[str, int], str]


def nombres_de_puertos(onos, dispositivos: Iterable[str]) -> Nombres:
    """{(deviceId, puerto): interfaz}. Si un switch no responde, simplemente no figura (se mostrará solo el número)."""
    res: Nombres = {}
    for d in dispositivos:
        try:
            puertos = onos.get(f"/devices/{d}/ports").get("ports", [])
        except Exception:  # noqa: BLE001
            continue
        for pt in puertos:
            num = str(pt.get("port", ""))
            nombre = (pt.get("annotations") or {}).get("portName")
            if num.isdigit() and nombre:
                res[(d, int(num))] = nombre
    return res


def etiqueta(nombres: Optional[Nombres], dpid: str, puerto) -> str:
    """`p4[ens7]` si se conoce la interfaz; `p4` si no."""
    try:
        nombre = (nombres or {}).get((dpid, int(puerto)))
    except (TypeError, ValueError):
        nombre = None
    return f"p{puerto}[{nombre}]" if nombre else f"p{puerto}"


def resumen(nombres: Optional[Nombres], dpid: str) -> str:
    """`1=ens4, 2=ens5, ...` de un switch (vacío si no hay datos)."""
    pares = sorted((p, n) for (d, p), n in (nombres or {}).items() if d == dpid)
    return ", ".join(f"{p}={n}" for p, n in pares)
