"""Pruebas de las comprobaciones de seguridad de instalar_proactivo.py (sin red ni ONOS)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caminos as c  # noqa: E402
import instalar_proactivo as ip  # noqa: E402
from test_caminos import LINKS, A1, A2, C1, C2, B  # noqa: E402
from test_flujos_proactivos import ACCESO, HOSTS  # noqa: E402
import plan_arboles as pa  # noqa: E402


def _con_nombres(f):
    """El script usa las constantes reales de PREFERIDOS (dpids de ONOS); en la prueba los sustituimos por los nombres de test."""
    anterior = pa.PREFERIDOS[:], ip.PREFERIDOS[:]
    ip.PREFERIDOS[:] = [C1, C2]
    pa.PREFERIDOS[:] = [C1, C2]
    try:
        return f()
    finally:
        ip.PREFERIDOS[:], pa.PREFERIDOS[:] = anterior[1], anterior[0]


def _plan(fase, links=LINKS, esperados=14, dpid=A1):
    return _con_nombres(lambda: ip.construir_plan(links, HOSTS, ACCESO, dpid, fase, esperados))


def test_fase_b_solo_trae_flujos_del_switch_pedido_en_la_tabla_60_y_sin_inundar():
    plan = _plan("B")
    assert plan and {f["deviceId"] for f in plan} == {A1} and {f["tableId"] for f in plan} == {60}
    assert c.flujos_que_inundan(plan) == []


def test_fase_c_es_una_sola_regla_de_arp_hacia_la_tabla_60_con_prioridad_del_mapa():
    plan = _plan("C")
    assert len(plan) == 1
    f = plan[0]
    assert f["tableId"] == 0 and f["priority"] == 40003 and f["deviceId"] == A1
    assert f["treatment"]["instructions"] == [{"type": "TABLE", "tableId": 60}]
    assert f["selector"]["criteria"] == [{"type": "ETH_TYPE", "ethType": "0x0806"}]


def test_si_faltan_enlaces_el_script_se_detiene():
    sin = [e for e in LINKS if {e["src"]["device"], e["dst"]["device"]} != {C1, A1}]
    try:
        _plan("B", links=sin)
    except ip.Detener as e:
        assert "enlaces" in str(e)
    else:
        raise AssertionError("debía detenerse con enlaces caídos")


def test_un_switch_que_no_esta_en_la_topologia_detiene():
    try:
        _plan("B", dpid="of:desconocido")
    except ip.Detener:
        return
    raise AssertionError("debía detenerse")


def test_resolver_dispositivo_acepta_nombre_o_deviceid():
    assert ip.resolver_dispositivo("acc-1") == "of:0000e6925d87354b"
    assert ip.resolver_dispositivo("of:0000766a0c84c741") == "of:0000766a0c84c741"


def test_el_arp_de_la_tabla_0_no_choca_con_el_mapa_de_prioridades():
    import yaml
    raiz = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
    with open(os.path.join(raiz, "R5", "deploy", "prioridades.yaml"), encoding="utf8") as f:
        mapa = yaml.safe_load(f)["r2"]
    assert mapa["reenvio_arp"] == ip.PRIO_ARP_TABLA0
    usadas = [v for k, v in mapa.items() if isinstance(v, int) and k not in ("tabla_rol", "tabla_cuarentena", "cuarentena")]
    assert usadas.count(ip.PRIO_ARP_TABLA0) == 1             # nadie más usa 40003


class _OnosFalso:
    """Responde /devices/<id>/ports como la API de ONOS."""
    def __init__(self, puertos):
        self.puertos = puertos

    def get(self, ruta):
        dev = ruta.split("/")[2]
        return {"ports": [{"element": dev, "port": p, "isEnabled": ok} for p, ok in self.puertos[dev]]}


def test_puertos_de_acceso_por_rest_excluye_troncales_local_y_deshabilitados():
    puertos = {B: [("1", True), ("2", True), ("3", True), ("LOCAL", True)],
               C1: [("5", True), ("6", True), ("7", True), ("9", True), ("1", True), ("8", False)],
               C2: [("5", True), ("6", True), ("7", True), ("9", True)], A1: [("1", True), ("5", True), ("6", True)],
               A2: [("1", True), ("5", True), ("6", True)]}
    acceso = ip.puertos_acceso_rest(_OnosFalso(puertos), LINKS)
    assert acceso[A1] == [1] and acceso[A2] == [1]            # solo el puerto del host: los otros dos son troncales
    assert acceso[B] == [3]                                    # LOCAL fuera, 1 y 2 son troncales
    assert acceso[C1] == [1]                                   # 8 deshabilitado fuera
    assert C2 not in acceso                                    # sin puertos de acceso


def test_get_y_delete_no_envian_content_type_pero_post_si():
    o = ip.Onos("http://x:8181", "onos:rocks")
    assert "Content-Type" not in o.cabeceras(False) and o.cabeceras(False)["Accept"] == "application/json"
    assert o.cabeceras(True)["Content-Type"] == "application/json"


def test_los_puertos_de_monitoreo_se_excluyen_de_la_difusion():
    acceso = {C1: [1, 8], A1: [1]}
    limpio = ip.sin_monitoreo(acceso, {C1: [1]})
    assert limpio == {C1: [8], A1: [1]}
    assert ip.sin_monitoreo({C1: [1]}, {C1: [1]}) == {}                    # sin puertos de acceso no queda ni la clave
    plan = _con_nombres(lambda: ip.construir_plan(LINKS, HOSTS, acceso, C1, "B", 14, {C1: [1]}))
    salidas = {i["port"] for f in plan for i in f["treatment"]["instructions"]}
    entradas = {cr["port"] for f in plan for cr in f["selector"]["criteria"] if cr["type"] == "IN_PORT"}
    assert "1" not in salidas and 1 not in entradas and "8" in salidas      # el 8 (datos) sí; el 1 (sensor) no


def test_los_sensores_reales_del_dibujo_estan_excluidos_por_defecto():
    assert ip.MONITOREO["of:00007e3401ecd842"] == [1] and ip.MONITOREO["of:000076f83813d34e"] == [1]     # core-1 y core-2: ens4 -> sensor
    assert ip.MONITOREO["of:000052ca28945241"] == [4, 5]                                                 # borde: ens8 y ens9 -> sensores
    real = ip.sin_monitoreo({"of:00007e3401ecd842": [1, 5], "of:000052ca28945241": [1, 4, 5]})
    assert real == {"of:00007e3401ecd842": [5], "of:000052ca28945241": [1]}                              # quedan el server y host-jhon


class _OnosConFlujos:
    def __init__(self, flujos):
        self.flujos, self.borrados = flujos, []

    def get(self, ruta):
        return {"flows": self.flujos}

    def _pedir(self, metodo, ruta, cuerpo=None):
        self.borrados.append((metodo, ruta))


def test_vaciar_tabla_borra_solo_los_flujos_de_este_appid_en_esa_tabla():
    f = [{"id": "11", "appId": "r2.proactivo", "tableId": 60}, {"id": "12", "appId": "r2.proactivo", "tableId": 0},
         {"id": "13", "appId": "org.onosproject.core", "tableId": 60}, {"id": "14", "appId": "r2.proactivo", "tableId": 60}]
    o = _OnosConFlujos(f)
    # Onos.vaciar_tabla usa self.flujos_de_app y self._pedir: se enlazan al simulador
    o.flujos_de_app = lambda dpid, tabla=None: ip.Onos.flujos_de_app(o, dpid, tabla)
    n = ip.Onos.vaciar_tabla(o, "of:x", 60)
    assert n == 2 and o.borrados == [("DELETE", "/flows/of:x/11"), ("DELETE", "/flows/of:x/14")]
