"""flujos_proactivos.py

R2: flujos de reenvío UNICAST por destino (eth_dst -> output:<puerto>), sin NORMAL, FLOOD ni ALL. Solo genera la lista; no habla con ONOS
ni instala nada. El diseño y las reglas de seguridad están en docs/DISENO_FLUJOS_PROACTIVOS.md.

Van en su propia tabla (TABLA_REENVIO): las tablas de rol, en vez de `OUTPUT NORMAL`, harán `goto_table` a esta (integración posterior).
Lo que no tiene regla (broadcast, multicast, destinos desconocidos) cae en un descarte explícito: nunca se inunda.
"""
from typing import Dict, Iterable, List

import caminos

TABLA_REENVIO = 60


def _base(dpid: str, tabla: int, prioridad: int, criterios: list, instrucciones: list) -> dict:
    """Mismo formato que policy_tables._base (copiado para no depender de httpx/yaml en el borde)."""
    return {"priority": prioridad, "timeout": 0, "isPermanent": True, "deviceId": dpid, "tableId": tabla,
            "treatment": {"instructions": instrucciones}, "selector": {"criteria": criterios}}

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


PRIO_DIFUSION = 200
MAC_BROADCAST = "FF:FF:FF:FF:FF:FF"


def puertos_del_arbol(grafo: caminos.Grafo, raiz: str, preferidos: Iterable[str] = ()) -> Dict[str, set]:
    """Puertos troncales que forman el ÁRBOL de difusión con raíz `raiz`: dispositivo -> {puertos} (en los dos extremos de cada enlace del árbol).

    Los enlaces que no están en el árbol (los que cerrarían un lazo) no aparecen: por ellos nunca se difunde ni se acepta difusión."""
    arbol = caminos.arbol_hacia(grafo, raiz, preferidos)
    puertos: Dict[str, set] = {d: set() for d in grafo}
    for d, (sig, puerto) in arbol.items():
        puertos[d].add(puerto)
        for vecino, p in grafo.get(sig, []):
            if vecino == d:
                puertos[sig].add(p)
    return puertos


def flujos_difusion(grafo: caminos.Grafo, raiz: str, puertos_acceso: Dict[str, Iterable[int]], preferidos: Iterable[str] = ()) -> List[dict]:
    """ARP en broadcast SIN inundar: por cada switch y puerto de entrada válido (del árbol o de acceso), un flujo explícito que lo saca por
    el resto de puertos del árbol y de acceso, nunca por donde entró. Un broadcast que entre por un puerto troncal fuera del árbol no
    coincide con ningún flujo y se descarta (el descarte de la tabla)."""
    arbol = puertos_del_arbol(grafo, raiz, preferidos)
    flujos: List[dict] = []
    for d in sorted(arbol):
        troncales = {p for _, p in grafo.get(d, [])}                        # un puerto con enlace NUNCA es de acceso, aunque se pase como tal
        acceso = {int(p) for p in puertos_acceso.get(d, ())} - troncales
        todos = set(arbol[d]) | acceso
        for entrada in sorted(todos):
            salidas = [{"type": "OUTPUT", "port": str(p)} for p in sorted(todos - {entrada})]
            flujos.append(_base(d, TABLA_REENVIO, PRIO_DIFUSION,
                                [{"type": "IN_PORT", "port": entrada}, {"type": "ETH_TYPE", "ethType": "0x0806"},
                                 {"type": "ETH_DST", "mac": MAC_BROADCAST}], salidas))
    return flujos
