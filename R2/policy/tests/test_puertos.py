"""Pruebas de puertos.py y de su uso en explicar_ruta.py (sin red)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caminos as c  # noqa: E402
import explicar_ruta as er  # noqa: E402
import puertos as pr  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, C1, C2  # noqa: E402


class _Onos:
    def __init__(self, datos, falla=()):
        self.datos, self.falla = datos, falla

    def get(self, ruta):
        dev = ruta.split("/")[2]
        if dev in self.falla:
            raise OSError("sin respuesta")
        return {"ports": [{"port": p, "annotations": ({"portName": n} if n else {})} for p, n in self.datos[dev]]}


def test_nombres_de_puertos_lee_el_portname_de_onos_y_ignora_local_y_sin_nombre():
    onos = _Onos({C1: [("1", "ens4"), ("4", "ens7"), ("LOCAL", "br0"), ("9", None)]})
    assert pr.nombres_de_puertos(onos, [C1]) == {(C1, 1): "ens4", (C1, 4): "ens7"}


def test_un_switch_que_no_responde_no_rompe_el_resto():
    onos = _Onos({C1: [("4", "ens7")], C2: [("4", "ens7")]}, falla=(C1,))
    assert pr.nombres_de_puertos(onos, [C1, C2]) == {(C2, 4): "ens7"}


def test_etiqueta_y_resumen():
    n = {(C1, 4): "ens7", (C1, 1): "ens4"}
    assert pr.etiqueta(n, C1, 4) == "p4[ens7]" and pr.etiqueta(n, C1, 6) == "p6" and pr.etiqueta(None, C1, 4) == "p4"
    assert pr.etiqueta(n, C1, "?") == "p?"                                  # puerto desconocido: no revienta
    assert pr.resumen(n, C1) == "1=ens4, 4=ens7" and pr.resumen(n, C2) == ""


def test_el_informe_muestra_la_interfaz_junto_al_puerto_si_se_conoce():
    G = c.grafo_desde_links(LINKS)
    d = er.derivar(G, A2, A1, PREF)
    nombres = {(A2, 6): "ens6", (A2, 5): "ens5", (C1, 5): "ens5"}
    texto = "\n".join(er.lineas(G, d, A2, A1, PREF, nombres=nombres))
    assert "p6[ens6]" in texto                                              # la salida elegida de acc-2 hacia core-1
    sin = "\n".join(er.lineas(G, d, A2, A1, PREF))
    assert "[ens" not in sin                                                # sin datos de ONOS, solo el número
