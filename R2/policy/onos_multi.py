"""onos_multi.py: cliente REST de ONOS con varios nodos. Usa el primero de la lista (node-ctrl-1) y, si no responde,
pasa al siguiente. Se queda con el que respondió (no salta de un nodo a otro sin motivo) y, cada cierto tiempo, vuelve a probar el preferido.

Los flujos de UN switch (leerlos y borrar uno) se piden al nodo que es su MAESTRO (GET /mastership/{switch}/master, con memoria de `ttl_maestro_s`): el maestro los lee en local, y
cualquier otro nodo se los pide por mensajes entre nodos de ONOS. El 23-nov, con el maestro de todos los switches en ctrl-2 y las lecturas por ctrl-1, esos mensajes se atascaron
(15 s para fallar, y la petición seguía viva en el servidor aunque el cliente ya se hubiera rendido a los 4 s). Si el maestro no se sabe o no responde, se sigue como siempre.

Vista de la topologia: /devices se lee del nodo en uso, pero si ese nodo no ve NINGUN switch disponible y otro nodo si, se usa el otro y se queda como nodo en uso (/links y /hosts
se leen entonces de el). El 23-nov, tras reiniciar dos ONOS, las replicas de ctrl-1 y ctrl-3 se quedaron con todos los switches `available=false` durante mas de 15 minutos mientras
el maestro (ctrl-2) los veia bien; el vigilante se fiaba del primero que respondia, concluia «red partida: todos» y dejo de recalcular. Si NINGUN nodo ve switches (red caida de verdad)
se devuelve la respuesta original, sin cambios.

Una respuesta 4xx (por ejemplo 404 de un flujo que ya no existe) es una respuesta de verdad y NO provoca el cambio de nodo; sí lo provocan los
5xx, los tiempos agotados y las conexiones rechazadas. Si ningún nodo responde se lanza ONOSNoDisponible: quien llama NO debe interpretarlo como
un cambio de topología.
"""
import time
import urllib.error
from urllib.parse import urlparse

from instalar_proactivo import Onos, APP_ID  # noqa: F401


class ONOSNoDisponible(Exception):
    """Ningún nodo de ONOS respondió."""


def _sin_vivos(respuesta):
    """La respuesta de /devices no trae ningun switch disponible (o no trae ninguno). Una respuesta de otra forma no se juzga."""
    try:
        dispositivos = respuesta["devices"]
    except (KeyError, TypeError):
        return False
    return not any(d.get("available", True) for d in dispositivos)


class OnosCluster:
    def __init__(self, urls, usuario, timeout=4, reintento_preferido_s=60, reloj=time.monotonic, nodos=None, ttl_maestro_s=30, log=None):
        self.nodos = nodos if nodos is not None else [Onos(u, usuario, timeout) for u in urls]
        self.actual = 0
        self.log = log                     # funcion(texto) para avisar de una vista mala; None = solo se guarda en `avisos`
        self.avisos = []
        self._vista_mala = set()           # nodos cuya ultima lectura de /devices no traia switches disponibles
        self.ttl_maestro_s = ttl_maestro_s
        self._maestros = {}                # switch -> (indice del nodo maestro o None, cuando se miró)
        self.reintento_preferido_s = reintento_preferido_s
        self.reloj = reloj
        self._ultimo_sondeo = reloj()
        self.cambios_de_nodo = []          # (nodo anterior, nodo nuevo) para registrar y para las pruebas

    def _orden(self):
        n = len(self.nodos)
        ahora = self.reloj()
        if self.actual != 0 and ahora - self._ultimo_sondeo >= self.reintento_preferido_s:
            self._ultimo_sondeo = ahora
            return [0] + [i for i in range(n) if i != 0]                 # sondear de nuevo al preferido
        return [(self.actual + i) % n for i in range(n)]

    def _indice_de(self, nodo_id):
        """Índice del nodo cuya URL tiene ese host (el `nodeId` de ONOS es su IP), o None."""
        if not nodo_id:
            return None
        for i, n in enumerate(self.nodos):
            if urlparse(getattr(n, "url", "") or "").hostname == nodo_id:
                return i
        return None

    def _maestro(self, dpid):
        """Índice del nodo maestro de ese switch, o None si no se sabe (entonces se usa el orden de siempre). Se recuerda `ttl_maestro_s` para no preguntarlo en cada lectura."""
        ahora = self.reloj()
        guardado = self._maestros.get(dpid)
        if guardado is not None and ahora - guardado[1] < self.ttl_maestro_s:
            return guardado[0]
        try:
            idx = self._indice_de(self._con_failover(lambda n: n.get(f"/mastership/{dpid}/master")).get("nodeId"))
        except (ONOSNoDisponible, urllib.error.HTTPError, AttributeError, ValueError, TypeError):
            idx = None
        self._maestros[dpid] = (idx, ahora)
        return idx

    def _con_failover(self, f, preferido=None):
        """Prueba los nodos en orden; con `preferido` (índice) ese va primero y, pase lo que pase, NO cambia el nodo en uso (`actual`): es una petición para un switch concreto."""
        errores = []
        orden = self._orden()
        if preferido is not None:
            orden = [preferido] + [i for i in orden if i != preferido]
        for idx in orden:
            try:
                r = f(self.nodos[idx])
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    raise                                                # respuesta real del servidor: no es una caída
                errores.append(f"nodo {idx}: HTTP {e.code}")
                continue
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                errores.append(f"nodo {idx}: {e}")
                continue
            if preferido is None and idx != self.actual:
                self.cambios_de_nodo.append((self.actual, idx))
                self.actual = idx
            return r
        raise ONOSNoDisponible("; ".join(errores) or "sin nodos configurados")

    def _nombre(self, idx):
        return urlparse(getattr(self.nodos[idx], "url", "") or "").hostname or f"nodo {idx}"

    def _avisar(self, texto):
        self.avisos.append(texto)
        if self.log:
            self.log(texto)

    def _devices_con_vista_viva(self, ruta, respuesta):
        usado = self.actual                                          # el nodo que acaba de responder (el failover ya lo fijo)
        if not _sin_vivos(respuesta):
            if usado in self._vista_mala:
                self._vista_mala.discard(usado)
                self._avisar(f"AVISO: el nodo {self._nombre(usado)} vuelve a ver los switches disponibles.")
            return respuesta
        if usado not in self._vista_mala:
            self._vista_mala.add(usado)
            self._avisar(f"AVISO: el nodo {self._nombre(usado)} no ve ningun switch disponible; se lee la topologia de otro nodo que si los vea.")
        for idx in range(len(self.nodos)):
            if idx == usado:
                continue
            try:
                otra = self.nodos[idx].get(ruta)
            except (urllib.error.URLError, OSError, TimeoutError):      # HTTPError es subclase de URLError: se salta ese nodo
                continue
            if _sin_vivos(otra):
                self._vista_mala.add(idx)
                continue
            self._vista_mala.discard(idx)
            self.cambios_de_nodo.append((self.actual, idx))
            self.actual = idx
            return otra
        return respuesta                                             # ningun nodo ve switches: red caida de verdad, o ONOS arrancando

    def get(self, ruta):
        respuesta = self._con_failover(lambda n: n.get(ruta))
        if len(self.nodos) > 1 and ruta.split("?")[0].rstrip("/") == "/devices":
            return self._devices_con_vista_viva(ruta, respuesta)
        return respuesta

    def instalar(self, flujos):
        return self._con_failover(lambda n: n.instalar(flujos))

    def borrar(self, dpid, flow_id):
        return self._con_failover(lambda n: n.borrar(dpid, flow_id), preferido=self._maestro(dpid))

    def flujos_de_app(self, dpid, tabla=None):
        return self._con_failover(lambda n: n.flujos_de_app(dpid, tabla), preferido=self._maestro(dpid))
