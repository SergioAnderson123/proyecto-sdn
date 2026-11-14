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
