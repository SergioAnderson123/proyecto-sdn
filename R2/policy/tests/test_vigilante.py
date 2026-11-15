"""Pruebas del vigilante y del cliente de varios nodos de ONOS, con un ONOS simulado y un reloj falso (sin red)."""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flujos_proactivos as fp  # noqa: E402
import onos_multi as om  # noqa: E402
import recalculo as rc  # noqa: E402
import vigilante as vg  # noqa: E402
from test_caminos import LINKS, PREF, A1, C1, C2  # noqa: E402
from test_flujos_proactivos import ACCESO, HOSTS  # noqa: E402
from test_recalculo import DISP, TODOS, _como_onos, sin_cable  # noqa: E402


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def pasar(self, s):
        self.t += s


class OnosFalso:
    """Un ONOS con estado: topología, hosts y flujos de la tabla 60; acepta POST y DELETE como el real."""
    def __init__(self):
        self.dispositivos = [dict(d) for d in DISP]
        self.enlaces = list(LINKS)
        self.hosts = [{"mac": h["mac"], "locations": [{"elementId": h["device"], "port": str(h["port"])}], "ipAddresses": []} for h in HOSTS]
        self.flujos, self.sig, self.borrados, self.instalados, self.falla = {}, 1, 0, 0, False
        g = rc.grafo_vivo(LINKS, DISP)
        for f in rc.flujos_deseados(g, HOSTS, ACCESO, PREF):
            self._poner(f)

    def _poner(self, f):
        self.flujos.setdefault(f["deviceId"], []).append(_como_onos(f, str(self.sig)))
        self.sig += 1

    def get(self, ruta):
        if self.falla:
            raise om.ONOSNoDisponible("simulado")
        if ruta == "/devices":
            return {"devices": self.dispositivos}
        if ruta == "/links":
            return {"links": self.enlaces}
        if ruta == "/hosts":
            return {"hosts": self.hosts}
        raise AssertionError(ruta)

    def flujos_de_app(self, dpid, tabla=None):
        if self.falla:
            raise om.ONOSNoDisponible("simulado")
        return [f for f in self.flujos.get(dpid, []) if tabla is None or f["tableId"] == tabla]

    def borrar(self, dpid, flow_id):
        self.flujos[dpid] = [f for f in self.flujos.get(dpid, []) if f["id"] != flow_id]
        self.borrados += 1

    def instalar(self, flujos):
        for f in flujos:
            self._poner(f)
            self.instalados += 1

    def estado(self):
        return {rc.clave(f) for fl in self.flujos.values() for f in fl}


def nuevo(aplicar=True, **kw):
    reloj, onos, logs = Reloj(), OnosFalso(), []
    v = vg.Vigilante(onos, PREF, ACCESO, aplicar=aplicar, estable_s=15, min_intervalo_s=60, log=logs.append, reloj=reloj, monitoreo={}, **kw)
    return v, onos, reloj, logs


def cae_el_cable_core1_acc1(onos):
    onos.enlaces = sin_cable(C1, A1)


# ---------- comportamiento del vigilante ----------
def test_sin_cambios_no_toca_nada():
    v, onos, reloj, _ = nuevo()
    assert v.ciclo() == "sin cambios" and onos.borrados == 0 and onos.instalados == 0


def test_cae_un_cable_espera_a_que_se_estabilice_y_luego_aplica_borrando_antes_de_montar():
    v, onos, reloj, logs = nuevo()
    cae_el_cable_core1_acc1(onos)
    assert v.ciclo() == "esperando" and onos.borrados == 0                      # lo detecta pero no actúa aún
    reloj.pasar(5)
    assert v.ciclo() == "esperando" and onos.borrados == 0
    reloj.pasar(11)                                                              # ya van 16 s estable
    assert v.ciclo() == "aplicado" and onos.borrados > 0 and onos.instalados > 0
    g2 = rc.grafo_vivo(onos.enlaces, DISP)
    assert onos.estado() == {rc.clave(f) for f in rc.flujos_deseados(g2, HOSTS, ACCESO, PREF)}      # quedó exactamente lo deseado
    reloj.pasar(5)
    assert v.ciclo() == "sin cambios"                                            # y ya no hay nada más que hacer
    assert any("CAMBIO DETECTADO" in x for x in logs) and any("APLICADO" in x for x in logs)


def test_sin_aplicar_solo_avisa():
    v, onos, reloj, logs = nuevo(aplicar=False)
    antes = onos.estado()
    cae_el_cable_core1_acc1(onos)
    v.ciclo()
    reloj.pasar(20)
    assert v.ciclo() == "simulación" and onos.estado() == antes and any("SIMULACIÓN" in x for x in logs)


def test_un_parpadeo_breve_no_provoca_cambios():
    v, onos, reloj, _ = nuevo()
    antes = onos.estado()
    cae_el_cable_core1_acc1(onos)
    assert v.ciclo() == "esperando"
    reloj.pasar(5)
    onos.enlaces = list(LINKS)                                                   # el cable vuelve antes de estabilizarse
    assert v.ciclo() == "sin cambios" and onos.estado() == antes and onos.borrados == 0


def test_respeta_el_intervalo_minimo_entre_dos_cambios():
    v, onos, reloj, _ = nuevo()
    cae_el_cable_core1_acc1(onos)
    v.ciclo()
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"
    onos.enlaces = list(LINKS)                                                   # vuelve el cable
    assert v.ciclo() == "esperando"
    reloj.pasar(16)
    assert v.ciclo() == "intervalo"                                              # han pasado 32 s < 60 s del primer cambio
    reloj.pasar(40)
    assert v.ciclo() == "intervalo"                                              # 72 s del primer cambio… aún no: el reloj cuenta desde que se aplicó (t=16)
    reloj.pasar(10)
    assert v.ciclo() == "aplicado"


def test_el_interruptor_pausa_todo(tmp_path):
    archivo = tmp_path / "desactivar"
    v, onos, reloj, _ = nuevo(interruptor=str(archivo))
    cae_el_cable_core1_acc1(onos)
    archivo.write_text("")
    assert v.ciclo() == "pausado"
    reloj.pasar(60)
    assert v.ciclo() == "pausado" and onos.borrados == 0
    archivo.unlink()
    assert v.ciclo() == "esperando"


def test_si_onos_no_responde_o_viene_vacio_no_se_hace_nada():
    v, onos, reloj, _ = nuevo()
    antes = onos.estado()
    onos.falla = True
    assert v.ciclo() == "sin ONOS"
    onos.falla = False
    onos.enlaces = []                                                            # ONOS reiniciando: switches sin enlaces
    assert v.ciclo() == "sospechoso" and onos.estado() == antes and onos.borrados == 0


def test_si_la_red_se_parte_no_recalcula():
    v, onos, reloj, _ = nuevo()
    antes = onos.estado()
    onos.enlaces = [e for e in LINKS if A1 not in (e["src"]["device"], e["dst"]["device"])]      # acc-1 vivo pero sin cables
    assert v.ciclo() == "partida"
    reloj.pasar(60)
    assert v.ciclo() == "partida" and onos.estado() == antes


def test_cae_un_switch_y_la_raiz_pasa_a_core2():
    v, onos, reloj, _ = nuevo()
    onos.dispositivos = [dict(d, available=False) if d["id"] == C1 else d for d in onos.dispositivos]
    v.ciclo()
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"
    quedan = {f["deviceId"] for fl in onos.flujos.values() for f in fl}
    assert quedan  # los demás switches siguen con flujos
    g2 = rc.grafo_vivo(onos.enlaces, onos.dispositivos)
    assert C1 not in g2 and rc.raiz_viva(g2, PREF) == C2


def test_un_cambio_demasiado_grande_no_se_aplica():
    v, onos, reloj, logs = nuevo(max_cambios=3)
    cae_el_cable_core1_acc1(onos)
    assert v.ciclo() == "demasiados cambios" and onos.borrados == 0
    assert any("No se aplica" in x for x in logs)


def test_un_host_que_onos_deja_de_listar_no_se_olvida():
    v, onos, reloj, _ = nuevo()
    assert v.ciclo() == "sin cambios"
    onos.hosts = []                                                              # ONOS no lista hosts un momento
    assert v.ciclo() == "sin cambios" and len(v.hosts) == len(HOSTS)


def test_los_puertos_de_un_cable_caido_nunca_pasan_a_ser_de_acceso():
    """Si el archivo de acceso incluyera por error un puerto de un cable, tras verlo en /links se trata siempre como troncal."""
    mal = {**ACCESO, C1: [1, 8, 5]}                                              # 5 de core-1 es el cable a acc-1
    reloj, onos, logs = Reloj(), OnosFalso(), []
    v = vg.Vigilante(onos, PREF, mal, aplicar=True, log=logs.append, reloj=reloj, monitoreo={})
    v.ciclo()
    assert (C1, 5) in v.troncales
    cae_el_cable_core1_acc1(onos)
    v.ciclo()
    reloj.pasar(16)
    v.ciclo()
    salidas = {i["port"] for f in onos.flujos[C1] if f["priority"] == fp.PRIO_DIFUSION for i in f["treatment"]["instructions"]}
    assert "5" not in salidas


# ---------- cliente de varios nodos ----------
class NodoFalso:
    def __init__(self, resultado=None, error=None):
        self.resultado, self.error, self.llamadas = resultado, error, 0

    def get(self, ruta):
        self.llamadas += 1
        if self.error:
            raise self.error
        return self.resultado


def test_cluster_prueba_primero_el_1_y_pasa_al_siguiente_si_falla_y_se_queda_con_el_que_responde():
    reloj = Reloj()
    n1 = NodoFalso(error=urllib.error.URLError("rechazado"))
    n2, n3 = NodoFalso({"ok": 2}), NodoFalso({"ok": 3})
    cl = om.OnosCluster([], "x", nodos=[n1, n2, n3], reloj=reloj)
    assert cl.get("/x") == {"ok": 2} and cl.actual == 1 and n1.llamadas == 1 and n3.llamadas == 0
    assert cl.get("/x") == {"ok": 2} and n1.llamadas == 1                        # se queda con el que responde (no vuelve a probar el 1 todavía)
    reloj.pasar(61)
    n1.error, n1.resultado = None, {"ok": 1}
    assert cl.get("/x") == {"ok": 1} and cl.actual == 0                          # pasado un minuto, vuelve al preferido
    assert cl.cambios_de_nodo == [(0, 1), (1, 0)]


def test_cluster_un_404_no_cambia_de_nodo_y_un_500_si():
    err404 = urllib.error.HTTPError("u", 404, "no", {}, None)
    cl = om.OnosCluster([], "x", nodos=[NodoFalso(error=err404), NodoFalso({"ok": 2})])
    try:
        cl.get("/x")
    except urllib.error.HTTPError as e:
        assert e.code == 404 and cl.actual == 0
    else:
        raise AssertionError("un 404 es una respuesta real")
    err500 = urllib.error.HTTPError("u", 500, "mal", {}, None)
    cl2 = om.OnosCluster([], "x", nodos=[NodoFalso(error=err500), NodoFalso({"ok": 2})])
    assert cl2.get("/x") == {"ok": 2} and cl2.actual == 1


def test_cluster_sin_ningun_nodo_lanza_onos_no_disponible():
    cl = om.OnosCluster([], "x", nodos=[NodoFalso(error=OSError("a")), NodoFalso(error=TimeoutError("b"))])
    try:
        cl.get("/x")
    except om.ONOSNoDisponible as e:
        assert "nodo 0" in str(e) and "nodo 1" in str(e)
    else:
        raise AssertionError("debía lanzar ONOSNoDisponible")
