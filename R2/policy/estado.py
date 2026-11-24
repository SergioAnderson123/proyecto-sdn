"""estado.py: lo que el vigilante cuenta de sí mismo para el panel (GET /estado), en funciones PURAS (sin ONOS ni red) más un historial corto de eventos.

Qué muestra:
  - los switches con su nivel hasta la raíz y su padre en el árbol hacia ella;
  - cada cable que alguna vez se vio, con su estado: «arbol» (algún árbol de destino lo usa), «reserva» (vivo pero sin uso: es la redundancia que tomaría el relevo) o «caido»;
  - los switches aislados si la red está partida;
  - un historial de eventos: cable o switch caído o restaurado, cambio detectado (con los flujos por switch), aplicado, alta rápida, red partida y recuperada.

NO incluye hosts, MAC ni IP de nadie: solo la topología de los switches (el servicio lo publica en la red de gestión, igual que /salud).
En capa 3 no hay un árbol de difusión: cada subred tiene el suyo (árbol de caminos más cortos hacia el switch de esa subred, con preferencia por core-1). Un cable está «en uso» si cualquiera de esos árboles
(más el de la raíz y el de la salida al exterior) pasa por él."""
import threading
from collections import deque
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

import caminos

MAX_HISTORIAL = 200


def _par(a: str, b: str) -> FrozenSet[str]:
    return frozenset((a, b))


def pares_de_enlaces(grafo: caminos.Grafo) -> Dict[FrozenSet[str], Dict[str, int]]:
    """Cada cable del grafo VIVO como par no dirigido: {frozenset({a, b}): {a: puerto_de_a, b: puerto_de_b}}."""
    pares: Dict[FrozenSet[str], Dict[str, int]] = {}
    for d, vecinos in grafo.items():
        for v, p in vecinos:
            pares.setdefault(_par(d, v), {})[d] = p
    return pares


def recordar_enlaces(conocidos: Dict[FrozenSet[str], Dict[str, int]], grafo: caminos.Grafo) -> None:
    """Añade a la memoria los cables vivos (con sus puertos). Nunca borra: un cable caído sigue «conocido» para poder mostrarlo como caído."""
    for par, puertos in pares_de_enlaces(grafo).items():
        conocidos[par] = dict(puertos)


def destinos_de_arbol(plan, raiz: Optional[str]) -> List[str]:
    """Switches hacia los que hay un árbol: el de cada subred del plan, el de la salida al exterior y la raíz."""
    ds = [raiz] if raiz else []
    if plan is not None:
        ds += [s.dispositivo for s in plan.subredes]
        if getattr(plan, "salida", None) is not None:
            ds.append(plan.salida.dispositivo)
    vistos, out = set(), []
    for d in ds:
        if d not in vistos:
            vistos.add(d)
            out.append(d)
    return out


def enlaces_en_uso(grafo: caminos.Grafo, destinos: Iterable[str], preferidos: Iterable[str]) -> set:
    """Pares de switches por los que pasa algún árbol hacia alguno de los `destinos`."""
    uso = set()
    for destino in destinos:
        if destino not in grafo:
            continue
        for origen, (siguiente, _puerto) in caminos.arbol_hacia(grafo, destino, preferidos).items():
            uso.add(_par(origen, siguiente))
    return uso


def construir(grafo: caminos.Grafo, conocidos: Dict[FrozenSet[str], Dict[str, int]], dispositivos: Iterable[dict], raiz: Optional[str], destinos: Iterable[str],
              preferidos: Iterable[str], nombre: Callable[[str], str] = lambda d: d) -> dict:
    """La parte de topología del /estado."""
    preferidos = list(preferidos)
    niveles = caminos.niveles_hacia(grafo, raiz) if raiz in grafo else {}
    arbol = caminos.arbol_hacia(grafo, raiz, preferidos) if raiz in grafo else {}
    ids = sorted({d["id"] for d in dispositivos} | set(grafo))
    disponibles = {d["id"] for d in dispositivos if d.get("available", True)}
    switches = []
    for i in ids:
        padre = arbol.get(i)
        switches.append({"id": i, "nombre": nombre(i), "disponible": i in disponibles, "nivel": niveles.get(i), "es_raiz": i == raiz,
                         "padre": {"id": padre[0], "nombre": nombre(padre[0]), "puerto": padre[1]} if padre else None})
    vivos = pares_de_enlaces(grafo)
    uso = enlaces_en_uso(grafo, destinos, preferidos)
    enlaces = []
    for par in sorted(set(conocidos) | set(vivos), key=lambda p: sorted(nombre(x) for x in p)):
        a, b = sorted(par, key=nombre)
        puertos = conocidos.get(par) or vivos.get(par, {})
        vivo = par in vivos
        enlaces.append({"a": a, "b": b, "a_nombre": nombre(a), "b_nombre": nombre(b), "a_puerto": puertos.get(a), "b_puerto": puertos.get(b),
                        "estado": ("arbol" if par in uso else "reserva") if vivo else "caido"})
    aislados = sorted(i for i in grafo if raiz is not None and i not in niveles) if raiz is not None else sorted(grafo)
    return {"raiz": {"id": raiz, "nombre": nombre(raiz)} if raiz else None, "switches": switches, "enlaces": enlaces,
            "aislados": [{"id": i, "nombre": nombre(i)} for i in aislados], "conexa": not aislados and bool(grafo)}


def eventos_de_topologia(antes: Optional[Tuple[set, dict]], ahora: Tuple[set, dict], nombre: Callable[[str], str] = lambda d: d) -> List[dict]:
    """Qué cambió entre dos fotos de la red viva. Cada foto es (switches_vivos, {par: {switch: puerto}}). Sin foto anterior (primer ciclo) no hay eventos."""
    if antes is None:
        return []
    sw0, en0 = antes
    sw1, en1 = ahora
    out = []
    for s in sorted(sw0 - sw1, key=nombre):
        out.append({"tipo": "switch_caido", "detalle": f"El switch {nombre(s)} dejó de estar disponible"})
    for s in sorted(sw1 - sw0, key=nombre):
        out.append({"tipo": "switch_restaurado", "detalle": f"El switch {nombre(s)} volvió a estar disponible"})

    def texto(par, puertos):
        a, b = sorted(par, key=nombre)
        return f"{nombre(a)}/{puertos.get(a)} ↔ {nombre(b)}/{puertos.get(b)}"
    for par in sorted(set(en0) - set(en1), key=lambda p: sorted(nombre(x) for x in p)):
        if par <= sw1:                                                      # si el cable cayó porque cayó un switch, ya lo dice el evento del switch
            out.append({"tipo": "enlace_caido", "detalle": f"Cable caído: {texto(par, en0[par])}"})
    for par in sorted(set(en1) - set(en0), key=lambda p: sorted(nombre(x) for x in p)):
        if par <= sw0:
            out.append({"tipo": "enlace_restaurado", "detalle": f"Cable restaurado: {texto(par, en1[par])}"})
    return out


class Historial:
    """Eventos del vigilante, los más recientes al final; tope fijo. Seguro entre hilos (el ciclo escribe, el servidor HTTP lee)."""

    def __init__(self, maximo: int = MAX_HISTORIAL):
        self._eventos: deque = deque(maxlen=maximo)
        self._cerrojo = threading.Lock()

    def agregar(self, t: float, tipo: str, detalle: str, cambios: Optional[dict] = None) -> dict:
        ev = {"t": round(float(t), 3), "tipo": tipo, "detalle": detalle}
        if cambios:
            ev["cambios"] = cambios
        with self._cerrojo:
            self._eventos.append(ev)
        return ev

    def recientes(self, limite: int = 50) -> List[dict]:
        """Los más nuevos primero."""
        with self._cerrojo:
            return list(self._eventos)[::-1][:max(1, int(limite))]


def cambios_por_switch(dif: Dict[str, List[dict]], nombre: Callable[[str], str] = lambda d: d) -> Dict[str, Dict[str, int]]:
    """Cuántos flujos se quitan y se añaden en cada switch: {"borde": {"quitar": 0, "agregar": 4}, ...}."""
    por: Dict[str, Dict[str, int]] = {}
    for f in dif.get("borrar", []):
        por.setdefault(nombre(f["deviceId"]), {"quitar": 0, "agregar": 0})["quitar"] += 1
    for f in dif.get("agregar", []):
        por.setdefault(nombre(f["deviceId"]), {"quitar": 0, "agregar": 0})["agregar"] += 1
    return dict(sorted(por.items()))
