"""onos_multi.py: cliente REST de ONOS con varios nodos. Usa el primero de la lista (node-ctrl-1, el maestro de los switches) y, si no responde,
pasa al siguiente. Se queda con el que respondió (no salta de un nodo a otro sin motivo) y, cada cierto tiempo, vuelve a probar el preferido.

Una respuesta 4xx (por ejemplo 404 de un flujo que ya no existe) es una respuesta de verdad y NO provoca el cambio de nodo; sí lo provocan los
5xx, los tiempos agotados y las conexiones rechazadas. Si ningún nodo responde se lanza ONOSNoDisponible: quien llama NO debe interpretarlo como
un cambio de topología.
"""
import time
import urllib.error

from instalar_proactivo import Onos, APP_ID  # noqa: F401


class ONOSNoDisponible(Exception):
    """Ningún nodo de ONOS respondió."""


class OnosCluster:
    def __init__(self, urls, usuario, timeout=4, reintento_preferido_s=60, reloj=time.monotonic, nodos=None):
        self.nodos = nodos if nodos is not None else [Onos(u, usuario, timeout) for u in urls]
        self.actual = 0
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

    def _con_failover(self, f):
        errores = []
        for idx in self._orden():
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
            if idx != self.actual:
                self.cambios_de_nodo.append((self.actual, idx))
                self.actual = idx
            return r
        raise ONOSNoDisponible("; ".join(errores) or "sin nodos configurados")

    def get(self, ruta):
        return self._con_failover(lambda n: n.get(ruta))

    def instalar(self, flujos):
        return self._con_failover(lambda n: n.instalar(flujos))

    def borrar(self, dpid, flow_id):
        return self._con_failover(lambda n: n.borrar(dpid, flow_id))

    def flujos_de_app(self, dpid, tabla=None):
        return self._con_failover(lambda n: n.flujos_de_app(dpid, tabla))
