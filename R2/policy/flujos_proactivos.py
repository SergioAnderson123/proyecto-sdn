"""flujos_proactivos.py

R2: flujos de reenvío UNICAST por destino (eth_dst -> output:<puerto>), sin NORMAL, FLOOD ni ALL. Solo genera la lista; no habla con ONOS
ni instala nada. El diseño y las reglas de seguridad están en docs/DISENO_FLUJOS_PROACTIVOS.md.

Van en su propia tabla (TABLA_REENVIO): las tablas de rol, en vez de `OUTPUT NORMAL`, harán `goto_table` a esta (integración posterior).
Lo que no tiene regla (broadcast, multicast, destinos desconocidos) cae en un descarte explícito: nunca se inunda.
"""
from typing import Dict, Iterable, List

import caminos
from policy_tables import _base

TABLA_REENVIO = 60
PRIO_UNICAST = 100
PRIO_DESCARTE = 1


def flujos_unicast(hosts: Iterable[dict], grafo: caminos.Grafo, dispositivos: Iterable[str], preferidos: Iterable[str] = ()) -> List[dict]:
    """Un flujo por (switch, host): eth_dst=MAC -> output por el árbol hacia el switch del host (o por su puerto de acceso).

    `hosts`: [{"mac": "AA:..", "device": "of:...", "port": 1}]. Un host cuyo switch no está en la topología se omite. Un switch sin camino
    al switch del host no recibe flujo para él (descarta: nunca inunda). El resultado es determinista (mismos datos, mismos flujos)."""
    dispositivos = sorted(dispositivos)
    preferidos = list(preferidos)
    flujos: List[dict] = []
    arboles: Dict[str, caminos.Arbol] = {}
    for h in sorted(hosts, key=lambda x: (x["mac"].lower(), x["device"])):
        destino = h["device"]
        if destino not in grafo:
            continue
        arbol = arboles.setdefault(destino, caminos.arbol_hacia(grafo, destino, preferidos))
        criterio = [{"type": "ETH_DST", "mac": h["mac"].upper()}]
        for d in dispositivos:
            if d == destino:
                puerto = int(h["port"])
            elif d in arbol:
                puerto = arbol[d][1]
            else:
                continue
            flujos.append(_base(d, TABLA_REENVIO, PRIO_UNICAST, criterio, [{"type": "OUTPUT", "port": str(puerto)}]))
    return flujos


def flujos_descarte(dispositivos: Iterable[str]) -> List[dict]:
    """Descarte explícito de todo lo que no tenga regla unicast en la tabla de reenvío (sin instrucciones = DROP)."""
    return [_base(d, TABLA_REENVIO, PRIO_DESCARTE, [], []) for d in sorted(dispositivos)]
