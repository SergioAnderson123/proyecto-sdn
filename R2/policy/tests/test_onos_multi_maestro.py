"""Cliente de varios nodos de ONOS: los flujos de un switch se piden al nodo MAESTRO de ese switch (lectura local), con memoria, sin cambiar el nodo en uso y con el failover de siempre si
el maestro no se sabe o no responde. Caso real (23-nov): el maestro de todos los switches pasó a ctrl-2 y las lecturas por ctrl-1 tardaban 15 s en fallar."""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import onos_multi as om  # noqa: E402

DEV1, DEV2 = "of:0000e6925d87354b", "of:0000766a0c84c741"


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Nodo:
    """Un nodo de ONOS falso: sabe quién manda en cada switch y apunta qué le piden."""
    def __init__(self, ip, maestros=None, error=None):
        self.url = f"http://{ip}:8181/onos/v1"
        self.ip, self.maestros, self.error = ip, maestros or {}, error
        self.leyo, self.borro, self.preguntas = [], [], 0

    def get(self, ruta):
        if self.error:
            raise self.error
        if ruta.startswith("/mastership/"):
            self.preguntas += 1
            dpid = ruta.split("/")[2]
            if dpid not in self.maestros:
                raise urllib.error.HTTPError("u", 404, "no", {}, None)
            return {"nodeId": self.maestros[dpid]}
        return {"ok": self.ip}

    def flujos_de_app(self, dpid, tabla=None):
        if self.error:
            raise self.error
        self.leyo.append(dpid)
        return [{"id": "1", "de": self.ip}]

    def borrar(self, dpid, flow_id):
        if self.error:
            raise self.error
        self.borro.append((dpid, flow_id))
        return {}


def cluster(maestros, **kw):
    n1, n2, n3 = Nodo("192.168.201.1", maestros), Nodo("192.168.201.2", maestros), Nodo("192.168.201.3", maestros)
    reloj = Reloj()
    return om.OnosCluster([], "x", nodos=[n1, n2, n3], reloj=reloj, **kw), (n1, n2, n3), reloj


def test_los_flujos_de_un_switch_se_leen_en_su_nodo_maestro_y_no_en_el_primero():
    cl, (n1, n2, n3), _ = cluster({DEV1: "192.168.201.2"})
    assert cl.flujos_de_app(DEV1) == [{"id": "1", "de": "192.168.201.2"}]
    assert n2.leyo == [DEV1] and n1.leyo == [] and n3.leyo == []
    assert cl.actual == 0 and cl.cambios_de_nodo == []                           # no cambia el nodo en uso: otras lecturas siguen por el de siempre


def test_cada_switch_va_a_su_maestro():
    cl, (n1, n2, n3), _ = cluster({DEV1: "192.168.201.2", DEV2: "192.168.201.3"})
    cl.flujos_de_app(DEV1)
    cl.flujos_de_app(DEV2)
    assert n2.leyo == [DEV1] and n3.leyo == [DEV2] and n1.leyo == []


def test_el_maestro_se_recuerda_y_se_vuelve_a_preguntar_pasado_el_tiempo():
    cl, (n1, n2, n3), reloj = cluster({DEV1: "192.168.201.2"}, ttl_maestro_s=30)
    cl.flujos_de_app(DEV1)
    cl.flujos_de_app(DEV1)
    assert n1.preguntas == 1                                                     # una sola pregunta en 2 lecturas
    reloj.t += 31
    cl.flujos_de_app(DEV1)
    assert n1.preguntas == 2


def test_si_el_mando_cambia_se_nota_al_caducar_la_memoria():
    cl, (n1, n2, n3), reloj = cluster({DEV1: "192.168.201.2"}, ttl_maestro_s=30)
    cl.flujos_de_app(DEV1)
    for n in (n1, n2, n3):
        n.maestros = {DEV1: "192.168.201.3"}
    reloj.t += 31
    cl.flujos_de_app(DEV1)
    assert n2.leyo == [DEV1] and n3.leyo == [DEV1]


def test_si_no_se_sabe_quien_manda_se_usa_el_orden_de_siempre():
    cl, (n1, n2, n3), _ = cluster({})                                            # /mastership da 404
    assert cl.flujos_de_app(DEV1) == [{"id": "1", "de": "192.168.201.1"}]
    assert n1.leyo == [DEV1] and n2.leyo == [] and n3.leyo == []


def test_si_el_maestro_dice_una_ip_que_no_es_de_ningun_nodo_configurado_tambien():
    cl, (n1, n2, n3), _ = cluster({DEV1: "10.9.9.9"})
    assert cl.flujos_de_app(DEV1)[0]["de"] == "192.168.201.1"


def test_si_el_maestro_no_responde_se_pasa_a_otro_nodo_sin_cambiar_el_nodo_en_uso():
    cl, (n1, n2, n3), _ = cluster({DEV1: "192.168.201.2"})
    n2.error = urllib.error.URLError("tiempo agotado")
    assert cl.flujos_de_app(DEV1)[0]["de"] == "192.168.201.1"
    assert n1.leyo == [DEV1] and cl.actual == 0 and cl.cambios_de_nodo == []


def test_borrar_un_flujo_tambien_va_al_maestro():
    cl, (n1, n2, n3), _ = cluster({DEV1: "192.168.201.3"})
    cl.borrar(DEV1, "77")
    assert n3.borro == [(DEV1, "77")] and n1.borro == [] and n2.borro == []


def test_un_404_del_nodo_al_leer_no_se_trata_como_caida():
    cl, (n1, n2, n3), _ = cluster({DEV1: "192.168.201.2"})
    n2.flujos_de_app = lambda dpid, tabla=None: (_ for _ in ()).throw(urllib.error.HTTPError("u", 404, "no", {}, None))
    try:
        cl.flujos_de_app(DEV1)
    except urllib.error.HTTPError as e:
        assert e.code == 404
    else:
        raise AssertionError("un 404 es una respuesta real")


def test_nodos_sin_url_se_comportan_como_siempre():
    class Simple:
        def __init__(self, n):
            self.n = n

        def get(self, ruta):
            return {"nodo": self.n}

        def flujos_de_app(self, dpid, tabla=None):
            return [self.n]

    cl = om.OnosCluster([], "x", nodos=[Simple(1), Simple(2)])
    assert cl.flujos_de_app(DEV1) == [1] and cl.actual == 0


def test_las_demas_lecturas_siguen_el_orden_y_el_failover_de_siempre():
    cl, (n1, n2, n3), _ = cluster({DEV1: "192.168.201.2"})
    n1.error = urllib.error.URLError("caido")
    assert cl.get("/hosts") == {"ok": "192.168.201.2"} and cl.actual == 1
