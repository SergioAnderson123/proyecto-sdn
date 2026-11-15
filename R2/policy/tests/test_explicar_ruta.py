"""Pruebas de explicar_ruta.py (sin red ni ONOS)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caminos as c  # noqa: E402
import explicar_ruta as er  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402

G = c.grafo_desde_links(LINKS)


def test_acc2_a_acc1_empata_entre_los_dos_cores_y_gana_el_preferido():
    d = er.derivar(G, A2, A1, PREF)
    assert d["ruta"] == [A2, C1, A1] and d["saltos"] == 2 and d["arbol_valido"]
    primero = d["pasos"][0]
    assert primero["empate"] and primero["elegido"] == C1
    assert [u for u, _ in primero["candidatos"]] == [C1, C2]            # ordenados por preferencia
    assert not d["pasos"][1]["empate"]                                    # core-1 -> acc-1 no tenía alternativa


def test_si_cae_el_cable_directo_la_ruta_da_la_vuelta_y_ya_no_hay_empate():
    sin = c.grafo_desde_links([e for e in LINKS if {e["src"]["device"], e["dst"]["device"]} != {C1, A1}])
    d = er.derivar(sin, A2, A1, PREF)
    assert d["ruta"] == [A2, C2, A1]                                      # acc-2 -> core-2 -> acc-1
    assert not d["pasos"][0]["empate"]


def test_la_distancia_baja_en_cada_salto_de_la_ruta():
    d = er.derivar(G, B, A2, PREF)
    niveles = [p["nivel"] for p in d["pasos"]]
    assert niveles == sorted(niveles, reverse=True) and niveles[-1] == 1


def test_destino_inalcanzable_devuelve_error_y_no_revienta():
    aislado = {**G, "isla": []}
    assert "error" in er.derivar(aislado, "isla", A1, PREF)


def test_el_informe_menciona_empate_y_ganador():
    d = er.derivar(G, A2, A1, PREF)
    texto = "\n".join(er.lineas(G, d, A2, A1, PREF))
    assert "EMPATE" in texto and "gana" in texto and "PASO 4" in texto
    assert "1 saltos" not in texto and "está a 1 salto." in texto         # singular correcto


class _OnosFalso:
    def __init__(self, rutas):
        self.rutas = rutas

    def get(self, ruta):
        return {"paths": [{"cost": float(len(r) - 1), "links": [{"src": {"device": a, "port": "1"}, "dst": {"device": b, "port": "1"}}
                                                                  for a, b in zip(r, r[1:])]} for r in self.rutas]}


def test_verificacion_cruzada_con_onos():
    nuestra = [A2, C1, A1]
    igual = "\n".join(er.comparar_onos(_OnosFalso([[A2, C1, A1], [A2, C2, A1]]), A2, A1, nuestra))
    assert "una de las más cortas" in igual and "<- la nuestra" in igual
    otro_desempate = "\n".join(er.comparar_onos(_OnosFalso([[A2, C2, A1]]), A2, A1, nuestra))
    assert "misma longitud" in otro_desempate
    mas_larga = "\n".join(er.comparar_onos(_OnosFalso([[A2, C1, C2, A1]]), A2, A1, nuestra))
    assert "DISTINTA LONGITUD" in mas_larga
