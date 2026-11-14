"""caminos.py

R2: caminos SIN LAZOS entre switches para el reenvío proactivo (sin NORMAL ni FLOOD).

Con lazos de capa 2 y sin STP, cualquier acción que inunde (NORMAL, FLOOD, ALL) puede multiplicar un broadcast hasta saturar la red. El
reenvío proactivo evita el problema de raíz: cada switch recibe flujos unicast con UN puerto de salida por destino, y esos puertos salen de
un ÁRBOL hacia el destino (un árbol no tiene ciclos). Aunque la topología física tenga lazos, el reenvío no.

Todo son funciones puras sobre el JSON de `GET /onos/v1/links`, para poder probarlas sin red ni ONOS.

    grafo  = grafo_desde_links(links)                       dispositivo -> [(vecino, puerto_local)]
    arbol  = arbol_hacia(grafo, destino, preferidos)        dispositivo -> (siguiente_dispositivo, puerto_local)   (el destino no figura)
    ruta   = camino(arbol, origen, destino)                 [origen, ..., destino]
"""
from collections import deque
from typing import Dict, Iterable, List, Optional, Tuple

Grafo = Dict[str, List[Tuple[str, int]]]
Arbol = Dict[str, Tuple[str, int]]

# Acciones de salida que inundan: con un lazo, un broadcast que entre por una regla así puede multiplicarse sin fin.
ACCIONES_QUE_INUNDAN = ("NORMAL", "FLOOD", "ALL")


def grafo_desde_links(links: Iterable[dict]) -> Grafo:
    """{dispositivo: [(vecino, puerto_local), ...]} a partir de los enlaces de ONOS (`src` y `dst` con `device` y `port`).

    ONOS lista cada enlace en los dos sentidos; solo se usa el sentido src -> dst de cada entrada, así que un enlace caído en un sentido
    no se usa. Con enlaces paralelos entre los mismos dos switches se conserva el de menor puerto (resultado estable)."""
    mejor: Dict[Tuple[str, str], int] = {}
    for e in links:
        origen, destino = e["src"]["device"], e["dst"]["device"]
        if origen == destino:
            continue
        puerto = int(e["src"]["port"])
        clave = (origen, destino)
        if clave not in mejor or puerto < mejor[clave]:
            mejor[clave] = puerto
    grafo: Grafo = {}
    for (origen, destino), puerto in sorted(mejor.items()):
        grafo.setdefault(origen, []).append((destino, puerto))
        grafo.setdefault(destino, [])
    return grafo


def arbol_hacia(grafo: Grafo, destino: str, preferidos: Iterable[str] = ()) -> Arbol:
    """Árbol de caminos más cortos HACIA `destino`: para cada dispositivo, el siguiente salto y el puerto local por el que sale.

    Búsqueda en anchura desde el destino por los enlaces inversos: cada dispositivo se fija una sola vez, así que el resultado es un árbol
    (sin ciclos) por construcción. Ante dos caminos igual de cortos gana el que pasa por el dispositivo más preferido (`preferidos`, por
    ejemplo [sw-core-1, sw-core-2]: el tráfico va por core-1 mientras pueda) y, después, el de nombre menor. Los dispositivos sin camino
    al destino no figuran en el resultado."""
    rango = {d: i for i, d in enumerate(preferidos)}
    por_nombre = lambda d: (rango.get(d, len(rango)), d)  # noqa: E731
    entrantes: Dict[str, List[Tuple[str, int]]] = {}      # u -> [(v, puerto de v hacia u)]
    for v, salidas in grafo.items():
        for u, puerto in salidas:
            entrantes.setdefault(u, []).append((v, puerto))
    arbol: Arbol = {}
    visto = {destino}
    cola = deque([destino])
    while cola:
        u = cola.popleft()
        for v, puerto in sorted(entrantes.get(u, []), key=lambda e: (por_nombre(e[0]), e[1])):
            if v in visto:
                continue
            visto.add(v)
            arbol[v] = (u, puerto)
            cola.append(v)
    return arbol


def camino(arbol: Arbol, origen: str, destino: str) -> Optional[List[str]]:
    """[origen, ..., destino] siguiendo el árbol, o None si no hay camino. Nunca se queda en un ciclo."""
    ruta, actual = [origen], origen
    while actual != destino:
        if actual not in arbol or len(ruta) > len(arbol) + 1:
            return None
        actual = arbol[actual][0]
        ruta.append(actual)
    return ruta


def es_arbol(arbol: Arbol, destino: str) -> bool:
    """Invariante de seguridad: desde cualquier dispositivo del árbol se llega al destino sin repetir ninguno (sin lazos)."""
    for origen in arbol:
        ruta = camino(arbol, origen, destino)
        if ruta is None or len(set(ruta)) != len(ruta):
            return False
    return True


def flujos_que_inundan(flujos: Iterable[dict]) -> List[dict]:
    """Los flujos cuya salida es NORMAL, FLOOD o ALL. En el modo proactivo la lista debe quedar SIEMPRE vacía."""
    malos = []
    for f in flujos:
        for i in f.get("treatment", {}).get("instructions", []):
            if i.get("type") == "OUTPUT" and str(i.get("port", "")).upper() in ACCIONES_QUE_INUNDAN:
                malos.append(f)
                break
    return malos
