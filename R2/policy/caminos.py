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
        if e.get("state", "ACTIVE") != "ACTIVE":         # un enlace que ONOS no da por activo no se usa
            continue
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


def niveles_hacia(grafo: Grafo, destino: str) -> Dict[str, int]:
    """Saltos que separan a cada dispositivo del destino (búsqueda en anchura por los enlaces inversos). Los que no llegan no figuran."""
    entrantes: Dict[str, List[str]] = {}
    for v, salidas in grafo.items():
        for u, _ in salidas:
            entrantes.setdefault(u, []).append(v)
    dist = {destino: 0}
    cola = deque([destino])
    while cola:
        u = cola.popleft()
        for v in sorted(entrantes.get(u, [])):
            if v not in dist:
                dist[v] = dist[u] + 1
                cola.append(v)
    return dist


def _clave(preferidos: Iterable[str]):
    rango = {d: i for i, d in enumerate(preferidos)}
    return lambda e: (rango.get(e[0], len(rango)), e[0], e[1])      # e = (vecino, puerto)


def arbol_hacia(grafo: Grafo, destino: str, preferidos: Iterable[str] = ()) -> Arbol:
    """Árbol de caminos más cortos HACIA `destino`: para cada dispositivo, el siguiente salto y el puerto local por el que sale.

    Regla: cada dispositivo elige, entre sus vecinos que están UN SALTO MÁS CERCA del destino, el más preferido (`preferidos`, por ejemplo
    [sw-core-1, sw-core-2]: el tráfico va por core-1 mientras pueda) y, a igualdad, el de nombre menor. Como la distancia al destino baja en
    cada salto, es imposible volver a un dispositivo ya visitado: el resultado es un árbol (sin ciclos) por construcción. Los dispositivos
    sin camino al destino no figuran en el resultado."""
    dist = niveles_hacia(grafo, destino)
    clave = _clave(preferidos)
    arbol: Arbol = {}
    for v, salidas in grafo.items():
        if v == destino or v not in dist:
            continue
        candidatos = [(u, p) for u, p in salidas if dist.get(u) == dist[v] - 1]
        u, p = min(candidatos, key=clave)
        arbol[v] = (u, p)
    return arbol


def decisiones(grafo: Grafo, destino: str, preferidos: Iterable[str] = ()) -> Dict[str, dict]:
    """Por qué cada dispositivo sale por donde sale: su distancia, los candidatos (vecinos un salto más cerca, ya ordenados por preferencia),
    el elegido y si hubo empate. Es la misma regla que `arbol_hacia`, expuesta para poder explicarla y demostrarla."""
    dist = niveles_hacia(grafo, destino)
    arbol = arbol_hacia(grafo, destino, preferidos)
    clave = _clave(preferidos)
    res = {}
    for v, (elegido, puerto) in arbol.items():
        cands = sorted([(u, p) for u, p in grafo[v] if dist.get(u) == dist[v] - 1], key=clave)
        res[v] = {"nivel": dist[v], "candidatos": cands, "elegido": elegido, "puerto": puerto, "empate": len(cands) > 1}
    return res


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
