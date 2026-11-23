"""La topologia se lee de un nodo que VE los switches: si el nodo en uso trae todos los switches `available=false` y otro los ve, se usa el otro. Caso real (23-nov): tras reiniciar dos ONOS,
ctrl-1 y ctrl-3 veian 0 switches disponibles durante mas de 15 minutos y el maestro (ctrl-2) los veia bien; el vigilante concluyo «red partida: todos» y dejo de recalcular."""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import onos_multi as om  # noqa: E402
import test_vigilante as tv  # noqa: E402


def vista(disponibles):
    return {"devices": [{"id": f"of:{i}", "available": bool(disponibles)} for i in range(5)]}


class Nodo:
    def __init__(self, ip, devices=None, error=None):
        self.url = f"http://{ip}:8181/onos/v1"
        self.ip, self.devices, self.error, self.pidio = ip, devices, error, []

    def get(self, ruta):
        self.pidio.append(ruta)
        if self.error:
            raise self.error
        if ruta.split("?")[0] == "/devices":
            return self.devices
        return {"links": [], "ruta": ruta, "de": self.ip}


def cluster(d1, d2, d3, **kw):
    ns = [Nodo("192.168.201.1", d1), Nodo("192.168.201.2", d2), Nodo("192.168.201.3", d3)]
    logs = []
    return om.OnosCluster([], "x", nodos=ns, reloj=tv.Reloj(), log=logs.append, **kw), ns, logs


def test_si_el_primer_nodo_ve_todo_se_queda_con_el_y_no_pregunta_a_los_demas():
    cl, (n1, n2, n3), logs = cluster(vista(True), vista(True), vista(True))
    assert cl.get("/devices") == vista(True)
    assert n2.pidio == [] and n3.pidio == [] and cl.actual == 0 and logs == []


def test_si_el_nodo_en_uso_no_ve_ningun_switch_se_usa_otro_que_si_y_se_queda_como_nodo_en_uso():
    cl, (n1, n2, n3), logs = cluster(vista(False), vista(True), vista(False))
    assert cl.get("/devices") == vista(True)
    assert cl.actual == 1 and cl.cambios_de_nodo[-1] == (0, 1)
    assert len(logs) == 1 and "192.168.201.1" in logs[0]                     # se avisa del nodo con la vista mala
    assert cl.get("/links")["de"] == "192.168.201.2"                         # y /links sale del mismo nodo que /devices


def test_si_es_el_nodo_del_medio_el_que_ve_bien_tambien_se_encuentra():
    cl, _, _ = cluster(vista(False), vista(False), vista(True))
    assert cl.get("/devices") == vista(True) and cl.actual == 2


def test_si_ningun_nodo_ve_switches_se_devuelve_la_respuesta_original_y_no_se_cambia_de_nodo():
    cl, (n1, n2, n3), logs = cluster(vista(False), vista(False), vista(False))
    assert cl.get("/devices") == vista(False)
    assert cl.actual == 0 and cl.cambios_de_nodo == []
    assert len(n2.pidio) == 1 and len(n3.pidio) == 1                         # se preguntó a los otros dos una vez


def test_un_nodo_que_no_responde_al_buscar_otra_vista_se_salta():
    cl, ns, _ = cluster(vista(False), None, vista(True))
    ns[1].error = urllib.error.URLError("caido")
    assert cl.get("/devices") == vista(True) and cl.actual == 2


def test_un_http_5xx_al_buscar_otra_vista_tambien_se_salta():
    cl, ns, _ = cluster(vista(False), None, vista(True))
    ns[1].error = urllib.error.HTTPError("u", 500, "x", {}, None)
    assert cl.get("/devices") == vista(True) and cl.actual == 2


def test_una_lista_de_switches_vacia_cuenta_como_sin_vista():
    cl, _, _ = cluster({"devices": []}, vista(True), vista(True))
    assert cl.get("/devices") == vista(True) and cl.actual == 1


def test_un_solo_switch_disponible_basta_para_fiarse_del_nodo():
    parcial = {"devices": [{"id": "of:1", "available": False}, {"id": "of:2", "available": True}]}
    cl, (n1, n2, n3), _ = cluster(parcial, vista(True), vista(True))
    assert cl.get("/devices") == parcial and n2.pidio == []                  # un switch caido de verdad no es una vista mala


def test_el_aviso_sale_una_sola_vez_aunque_se_repita_el_ciclo_y_avisa_cuando_el_nodo_se_recupera():
    cl, (n1, n2, n3), logs = cluster(vista(False), vista(True), vista(False))
    for _ in range(5):
        cl.get("/devices")
    assert len(logs) == 1
    cl.reloj.pasar(61)                                                       # el preferido se sondea de nuevo y sigue mal: sin otro aviso
    cl.get("/devices")
    assert len(logs) == 1 and cl.actual == 1
    n1.devices = vista(True)
    cl.reloj.pasar(61)
    assert cl.get("/devices") == vista(True) and cl.actual == 0
    assert len(logs) == 2 and "vuelve a ver" in logs[1]


def test_otras_lecturas_no_se_cambian_por_esto():
    cl, (n1, n2, n3), _ = cluster(vista(False), vista(True), vista(True))
    assert cl.get("/links")["de"] == "192.168.201.1" and n2.pidio == []     # /links y /hosts siguen el orden de siempre


def test_con_un_solo_nodo_no_hay_donde_buscar():
    solo = om.OnosCluster([], "x", nodos=[Nodo("192.168.201.1", vista(False))], reloj=tv.Reloj())
    assert solo.get("/devices") == vista(False)


def test_los_dispositivos_con_ruta_con_consulta_tambien_se_juzgan():
    cl, _, _ = cluster(vista(False), vista(True), vista(True))
    assert cl.get("/devices?x=1") == vista(True) and cl.actual == 1


# ---------- el vigilante, de punta a punta ----------
class NodoFalso(tv.OnosFalso):
    """El ONOS falso del vigilante como nodo de un cluster: no sabe quien manda (404), como un nodo al que no se le puede preguntar."""
    def get(self, ruta):
        if ruta.startswith("/mastership/"):
            raise urllib.error.HTTPError("u", 404, "no", {}, None)
        return super().get(ruta)


def test_el_vigilante_no_dice_red_partida_si_un_nodo_ve_los_switches_caidos_y_otro_no():
    reloj, logs = tv.Reloj(), []
    real = NodoFalso()
    malo = NodoFalso()
    malo.dispositivos = [dict(d, available=False) for d in malo.dispositivos]
    for n in (real, malo):
        n.url = "http://192.168.201.%d:8181/onos/v1" % (2 if n is real else 1)
    cl = om.OnosCluster([], "x", nodos=[malo, real], reloj=reloj, log=logs.append)
    v = tv.vg.Vigilante(cl, tv.PREF, tv.ACCESO, aplicar=True, estable_s=15, min_intervalo_s=60, log=logs.append, reloj=reloj, monitoreo={})
    assert v.ciclo() == "sin cambios"
    assert any("no ve ningun switch disponible" in x for x in logs) and not any("partida" in x for x in logs)


def test_sin_el_arreglo_el_mismo_caso_dejaba_al_vigilante_en_partida():
    """Documenta el fallo original: con un solo nodo malo (sin otro donde mirar), el vigilante sigue frenandose por seguridad."""
    reloj, logs = tv.Reloj(), []
    malo = NodoFalso()
    malo.dispositivos = [dict(d, available=False) for d in malo.dispositivos]
    malo.url = "http://192.168.201.1:8181/onos/v1"
    cl = om.OnosCluster([], "x", nodos=[malo], reloj=reloj)
    v = tv.vg.Vigilante(cl, tv.PREF, tv.ACCESO, aplicar=True, estable_s=15, min_intervalo_s=60, log=logs.append, reloj=reloj, monitoreo={})
    assert v.ciclo() == "partida"
