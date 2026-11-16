"""recalculo.py: recalcular el reenvío proactivo cuando cae un enlace o un switch (funciones puras, sin red).

Idea: en vez de reaccionar a eventos, se COMPARA el estado deseado con el instalado. Con la topología que ONOS ve ahora se calculan los flujos
que debería haber en la tabla 60; la diferencia con los que hay es lo que hay que cambiar. Así sirve igual para un cable caído, un switch
caído, un host nuevo o un flujo borrado a mano.

Cambio sin lazos ("romper antes de montar"): primero se BORRAN los flujos que sobran o cambian y después se AÑADEN los nuevos.
  - Mientras se borra, cada switch solo tiene flujos del árbol viejo o ninguno: el reenvío está dentro del árbol viejo, sin ciclos.
  - Mientras se añade, los switches sin cambios y los ya actualizados reenvían solo por aristas del árbol nuevo: sin ciclos.
  (Un switch que no cambia tiene los mismos puertos en el árbol viejo y en el nuevo, así que nunca introduce una arista ajena a ambos.)
"""
import json
from typing import Dict, Iterable, List, Optional, Tuple

import caminos as c
import flujos_proactivos as fp

PRIO_DIFUSION = fp.PRIO_DIFUSION


def grafo_vivo(links: Iterable[dict], dispositivos: Iterable[dict]) -> c.Grafo:
    """Grafo con solo lo que está vivo: switches disponibles y cables ACTIVE en los DOS sentidos entre switches disponibles."""
    disponibles = {d["id"] for d in dispositivos if d.get("available", True)}
    utiles = [e for e in links if e.get("state", "ACTIVE") == "ACTIVE"
              and e["src"]["device"] in disponibles and e["dst"]["device"] in disponibles]
    pares = {(e["src"]["device"], e["dst"]["device"]) for e in utiles}
    simetricos = [e for e in utiles if (e["dst"]["device"], e["src"]["device"]) in pares]       # un cable que va solo en un sentido no sirve
    g = c.grafo_desde_links(simetricos)
    for d in disponibles:
        g.setdefault(d, [])
    return g


def raiz_viva(grafo: c.Grafo, preferidos: Iterable[str]) -> Optional[str]:
    """La primera raíz preferida que siga viva (core-1; si cae, core-2); si no hay ninguna, el switch de nombre menor."""
    for p in preferidos:
        if p in grafo:
            return p
    return sorted(grafo)[0] if grafo else None


def esta_conexa(grafo: c.Grafo, raiz: Optional[str]) -> bool:
    """¿Todos los switches vivos llegan a la raíz? Si no, la red está partida y no se recalcula nada."""
    return bool(grafo) and raiz is not None and set(c.niveles_hacia(grafo, raiz)) == set(grafo)


def flujos_deseados(grafo: c.Grafo, hosts: Iterable[dict], acceso: Dict[str, Iterable[int]], preferidos: Iterable[str],
                    raiz: Optional[str] = None, copiar_arp: bool = False) -> List[dict]:
    """Todos los flujos de la tabla 60 que debería haber ahora (unicast por host, difusión por árbol y descarte), en todos los switches vivos."""
    preferidos = list(preferidos)
    raiz = raiz or raiz_viva(grafo, preferidos)
    troncales = {(d, p) for d, v in grafo.items() for _, p in v}
    validos = [h for h in hosts if h["device"] in grafo and (h["device"], h["port"]) not in troncales]
    return (fp.flujos_unicast(validos, grafo, grafo.keys(), preferidos) + fp.flujos_descarte(grafo.keys())
            + fp.flujos_difusion(grafo, raiz, acceso, preferidos, copiar_arp))


# ---- comparar lo deseado con lo instalado ----
def _valor_criterio(cr: dict) -> str:
    t = cr["type"]
    if t == "IN_PORT":
        return str(cr["port"])
    if t == "ETH_DST":
        return cr["mac"].upper()
    if t == "ETH_TYPE":
        return str(int(str(cr["ethType"]), 16))                # ONOS devuelve "0x806"; nosotros "0x0806"
    return json.dumps(cr, sort_keys=True)


def clave(f: dict) -> Tuple:
    """Identidad normalizada de un flujo (sirve igual para los nuestros y para los que devuelve GET /flows de ONOS)."""
    criterios = tuple(sorted((cr["type"], _valor_criterio(cr)) for cr in f["selector"]["criteria"]))
    salidas, otras = [], []
    for i in f["treatment"].get("instructions", []):
        if i["type"] == "NOACTION":                            # ONOS puede representar el descarte con NOACTION en vez de una lista vacía
            continue
        if i["type"] == "OUTPUT":
            salidas.append(str(i["port"]))
        else:
            otras.append(json.dumps(i, sort_keys=True))
    return (f["deviceId"], int(f["tableId"]), int(f["priority"]), criterios, tuple(sorted(salidas)), tuple(sorted(otras)))


def diferencia(deseados: Iterable[dict], instalados: Iterable[dict]) -> Dict[str, List[dict]]:
    """{'borrar': [flujos instalados que sobran o cambian], 'agregar': [flujos deseados que faltan]}. Los que coinciden no se tocan."""
    deseados, instalados = list(deseados), list(instalados)
    kd = {clave(f) for f in deseados}
    ki = {clave(f) for f in instalados}
    return {"borrar": [f for f in instalados if clave(f) not in kd], "agregar": [f for f in deseados if clave(f) not in ki]}


def altas_puras(dif: Dict[str, List[dict]], instalados: Iterable[dict]) -> bool:
    """¿El cambio son SOLO flujos unicast de MAC nuevas (ninguna regla instalada para esa MAC y nada que borrar)?

    Si es así se puede aplicar de inmediato: mientras una MAC no tiene reglas, su tráfico cae en el descarte, así que cualquier estado a
    medias solo descarta y no puede formar un lazo; y como ninguna regla existente cambia, no se mezclan árboles viejos y nuevos."""
    if dif["borrar"] or not dif["agregar"]:
        return False

    def mac_unicast(f):
        crit = f["selector"]["criteria"]
        if int(f["tableId"]) != fp.TABLA_REENVIO or int(f["priority"]) != fp.PRIO_UNICAST or len(crit) != 1 or crit[0]["type"] != "ETH_DST":
            return None
        return crit[0]["mac"].upper()

    conocidas = {m for m in (mac_unicast(f) for f in instalados) if m}
    nuevas = [mac_unicast(f) for f in dif["agregar"]]
    return all(m is not None and m not in conocidas for m in nuevas)


def resumen(dif: Dict[str, List[dict]], nombre=lambda d: d) -> str:
    """Una línea legible: qué switches cambian y cuánto."""
    por: Dict[str, List[int]] = {}
    for f in dif["borrar"]:
        por.setdefault(f["deviceId"], [0, 0])[0] += 1
    for f in dif["agregar"]:
        por.setdefault(f["deviceId"], [0, 0])[1] += 1
    return "; ".join(f"{nombre(d)}: -{b} +{a}" for d, (b, a) in sorted(por.items(), key=lambda x: nombre(x[0]))) or "sin cambios"


def pasos_de_cambio(dif: Dict[str, List[dict]]) -> List[Tuple[str, List[dict]]]:
    """Orden seguro: primero todo lo que se borra, después todo lo que se añade (ver la explicación del módulo)."""
    pasos = []
    if dif["borrar"]:
        pasos.append(("borrar", dif["borrar"]))
    if dif["agregar"]:
        pasos.append(("agregar", dif["agregar"]))
    return pasos


def describir(f: dict, nombre=lambda d: d) -> str:
    """Una línea legible de un flujo (para decir EXACTAMENTE cuál difiere): `acc-1 t60 p200 [ETH_TYPE=2054, IN_PORT=1] -> salidas [2]`."""
    dev, tabla, prio, criterios, salidas, otras = clave(f)
    crit = ", ".join(f"{t}={v}" for t, v in criterios) or "cualquiera"
    extra = f" otras {list(otras)}" if otras else ""
    return f"{nombre(dev)} t{tabla} p{prio} [{crit}] -> salidas {list(salidas) or 'ninguna (descarte)'}{extra}"
