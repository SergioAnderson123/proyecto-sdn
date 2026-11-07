"""Pruebas de policy_tables.py (sin red ni ONOS). Ejecutar desde R2/policy:  python -m pytest tests -q"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy_tables as pt  # noqa: E402

S1, S2 = "of:0000000000000001", "of:0000000000000002"


@pytest.fixture(scope="module")
def pol():
    return pt.cargar_politica()


def destinos(flujos):
    """{(ip, puerto)} de los flujos que permiten un servicio (sin contar el portal, que todos los roles alcanzan)."""
    res = set()
    for f in flujos:
        c = {x["type"]: x for x in f["selector"]["criteria"]}
        if "IPV4_DST" in c and c["IPV4_DST"]["ip"] != "10.0.0.60/32":
            res.add((c["IPV4_DST"]["ip"].split("/")[0], c["TCP_DST"]["tcpPort"]))
    return res


def test_docente_alcanza_sus_servicios_y_no_los_denegados(pol):
    d = destinos(pt.flujos_tabla_rol(pol, S1, "docente", 10))
    assert ("10.0.0.50", 8101) in d          # grade_system: permitido al profesor
    assert ("10.0.0.40", 8001) in d          # virtual_library
    assert ("10.0.0.50", 8102) not in d      # student_records: denegado al profesor
    assert ("10.0.0.50", 8105) not in d      # network_management: solo administrador


def test_alumno_solo_alcanza_servicios_academicos_abiertos(pol):
    d = destinos(pt.flujos_tabla_rol(pol, S1, "alumno", 20))
    assert d == {("10.0.0.40", 8001), ("10.0.0.40", 8002), ("10.0.0.40", 8003)}
    assert ("10.0.0.50", 8101) not in d      # grade_system: denegado al alumno


def test_cada_tabla_tiene_arp_y_drop_por_defecto(pol):
    f = pt.flujos_tabla_rol(pol, S1, "alumno", 20)
    assert all(x["tableId"] == 20 and x["deviceId"] == S1 for x in f)
    assert any(c["ethType"] == "0x0806" for x in f for c in x["selector"]["criteria"] if c["type"] == "ETH_TYPE")
    drop = [x for x in f if x["priority"] == 0]
    assert len(drop) == 1 and drop[0]["treatment"]["instructions"] == []


def test_los_permitidos_quitan_la_vlan_y_salen_por_normal(pol):
    for x in pt.flujos_tabla_rol(pol, S1, "docente", 10):
        if x["priority"] == pt.PRIO_ROL:
            assert x["treatment"]["instructions"] == [
                {"type": "L2MODIFICATION", "subtype": "VLAN_POP"}, {"type": "OUTPUT", "port": "NORMAL"}]


def test_servicio_sin_catalogo_no_genera_flujo(pol):
    # internet esta permitido al docente en la matriz pero no tiene host en el catalogo
    assert "internet" not in pol["services"]
    n = len(pt.flujos_tabla_rol(pol, S1, "docente", 10))
    permitidos = [s for s, ok in pt.servicios_permitidos(pol, "professor").items() if ok and s in pol["services"]]
    assert n == len(permitidos) + 3          # + ARP + portal + DROP


def test_switch_de_confianza_total_no_lleva_tablas_de_rol(pol):
    f = pt.flujos_dispositivo(pol, S2)
    assert len(f) == 1 and f[0]["tableId"] == 0 and f[0]["selector"]["criteria"] == []
    assert f[0]["priority"] == pt.PRIO_CONFIANZA


def test_switch_de_acceso_lleva_puerto_de_confianza_y_las_tablas_de_rol(pol):
    f = pt.flujos_dispositivo(pol, S1)
    confianza = pt.flujos_confianza(pol, S1)
    assert len(confianza) == 1 and confianza[0]["selector"]["criteria"] == [{"type": "IN_PORT", "port": 4}]
    assert {x["tableId"] for x in f} == {0, 10, 20, 40, 50}


def test_switch_desconocido_no_recibe_nada(pol):
    assert pt.flujos_confianza(pol, "of:00000000000000ff") == []
    assert pt.servicios_permitidos(pol, "rol-inexistente") == {}


def test_la_prioridad_de_rol_no_pisa_la_banda_de_r1_r5():
    assert pt.PRIO_ROL < 40000 and pt.PRIO_CONFIANZA == 40001


def test_los_flujos_con_pop_vlan_exigen_el_vlan_vid_de_su_rol(pol):
    # sin VLAN_VID, OVS responde MATCH_INCONSISTENT al pop_vlan (visto en el laboratorio)
    for rol, tabla in (("docente", 10), ("alumno", 20), ("soporte", 40), ("invitado", 50)):
        for x in pt.flujos_tabla_rol(pol, S1, rol, tabla):
            if x["priority"] == pt.PRIO_ROL:
                assert {"type": "VLAN_VID", "vlanId": tabla} in x["selector"]["criteria"]

def test_administrador_alcanza_todos_los_servicios_del_catalogo(pol):
    d = destinos(pt.flujos_tabla_rol(pol, S1, "soporte", 40))
    assert d == {(s["ip"], int(s["tcp"])) for s in pol["services"].values()}
    assert len(pt.flujos_tabla_rol(pol, S1, "soporte", 40)) == len(pol["services"]) + 3   # + ARP + portal + DROP


def test_solo_los_flujos_de_sesion_se_consideran_huerfanos():
    sesion = {"priority": 40001, "tableId": 0, "selector": {"criteria": [{"type": "IN_PORT", "port": 1}, {"type": "ETH_SRC", "mac": "00:00:00:00:00:11"}]}}
    confianza = {"priority": 40001, "tableId": 0, "selector": {"criteria": [{"type": "IN_PORT", "port": 4}]}}
    rol = {"priority": 30000, "tableId": 10, "selector": {"criteria": [{"type": "ETH_SRC", "mac": "00:00:00:00:00:11"}]}}
    assert pt.es_flujo_de_sesion(sesion)
    assert not pt.es_flujo_de_sesion(confianza) and not pt.es_flujo_de_sesion(rol)


# ---------- solo se reenvía lo que falta (evita PENDING_ADD y el reinicio de contadores) ----------
def _flujos_onos(pol, dpid, estado="ADDED", quitar=0):
    f = [{"deviceId": dpid, "state": estado} for _ in pt.flujos_dispositivo(pol, dpid)]
    return f[quitar:]


def test_no_reenvia_si_ya_estan_todos_los_flujos(pol):
    onos = _flujos_onos(pol, S1) + _flujos_onos(pol, S2)
    assert pt.dispositivos_incompletos(pol, [S1, S2], onos) == []


def test_reenvia_solo_al_switch_al_que_le_faltan(pol):
    onos = _flujos_onos(pol, S1, quitar=1) + _flujos_onos(pol, S2)
    assert pt.dispositivos_incompletos(pol, [S1, S2], onos) == [S1]


def test_los_flujos_pendientes_no_cuentan_como_instalados(pol):
    assert pt.dispositivos_incompletos(pol, [S1], _flujos_onos(pol, S1, estado="PENDING_ADD")) == [S1]


def test_un_switch_nuevo_sin_flujos_se_detecta(pol):
    assert pt.dispositivos_incompletos(pol, [S1, S2], []) == [S1, S2]

# ---------- portal cautivo ----------
def test_el_invitado_solo_alcanza_el_portal_universitario(pol):
    d = destinos(pt.flujos_tabla_rol(pol, S1, "invitado", 50))
    assert d == {("10.0.0.40", 8003)}


def test_el_portal_solo_se_instala_en_los_switches_de_acceso(pol):
    assert pt.flujos_portal(pol, S1) and pt.flujos_portal(pol, S2) == []


def test_un_usuario_sin_sesion_puede_resolver_y_llegar_al_portal(pol):
    f = pt.flujos_portal(pol, S1)
    arp = [x for x in f if x["priority"] == pt.PRIO_PORTAL_ARP and not any(c["type"] == "IP_PROTO" for c in x["selector"]["criteria"])]
    assert len(arp) == 1 and arp[0]["selector"]["criteria"] == [{"type": "ETH_TYPE", "ethType": "0x0806"}]
    assert {"type": "OUTPUT", "port": "CONTROLLER"} in arp[0]["treatment"]["instructions"]
    tcp = {c["tcpPort"] for x in f if x["priority"] == pt.PRIO_PORTAL for c in x["selector"]["criteria"]
           if c["type"] == "TCP_DST" and {"type": "IPV4_DST", "ip": "10.0.0.60/32"} in x["selector"]["criteria"]}
    assert tcp == {8080, 8083}


def test_el_desvio_reescribe_hacia_el_portal(pol):
    desvio = [x for x in pt.flujos_portal(pol, S1)
              if {"type": "IPV4_DST", "ip": "10.0.0.40/32"} in x["selector"]["criteria"] and x["priority"] == pt.PRIO_PORTAL][0]
    assert {"type": "TCP_DST", "tcpPort": 8003} in desvio["selector"]["criteria"]
    ins = desvio["treatment"]["instructions"]
    assert {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": "02:00:00:00:00:60"} in ins
    assert {"type": "L3MODIFICATION", "subtype": "IPV4_DST", "ip": "10.0.0.60"} in ins
    assert {"type": "L4MODIFICATION", "subtype": "TCP_DST", "tcpPort": 8083} in ins and ins[-1] == {"type": "OUTPUT", "port": "NORMAL"}


def test_la_respuesta_se_reescribe_de_vuelta_por_la_troncal_y_gana_a_la_confianza(pol):
    retorno = [x for x in pt.flujos_portal(pol, S1) if x["priority"] == pt.PRIO_PORTAL_RETORNO and x["tableId"] == 0
               and any(c["type"] == "IP_PROTO" for c in x["selector"]["criteria"])]
    assert len(retorno) == 1 and {"type": "IN_PORT", "port": 4} in retorno[0]["selector"]["criteria"]
    ins = retorno[0]["treatment"]["instructions"]
    assert {"type": "L3MODIFICATION", "subtype": "IPV4_SRC", "ip": "10.0.0.40"} in ins
    assert {"type": "L4MODIFICATION", "subtype": "TCP_SRC", "tcpPort": 8003} in ins
    assert pt.PRIO_PORTAL_RETORNO > pt.PRIO_CONFIANZA


def test_el_desvio_queda_bajo_la_sesion_de_r2(pol):
    assert pt.PRIO_PORTAL < pt.PRIO_SESION          # con sesión, nunca se desvía


def test_con_sesion_todos_los_roles_alcanzan_el_portal_para_cerrar_sesion(pol):
    for rol, tabla in (("docente", 10), ("alumno", 20), ("soporte", 40), ("invitado", 50)):
        cs = [{x["type"]: x for x in f["selector"]["criteria"]} for f in pt.flujos_tabla_rol(pol, S1, rol, tabla)]
        assert any(c.get("IPV4_DST", {}).get("ip") == "10.0.0.60/32" and c["TCP_DST"]["tcpPort"] == 8080 for c in cs if "TCP_DST" in c)


def test_duracion_de_la_sesion_del_portal_por_rol(pol):
    assert pt.duracion_sesion_portal(pol, "invitado") == 1800
    assert pt.duracion_sesion_portal(pol, "docente") == 28800          # cae en `default`
    assert pt.duracion_sesion_portal({}, "invitado") == 28800          # sin configuración


def test_solo_vencen_las_sesiones_con_plazo_cumplido():
    from session_manager import ActiveSession, SessionManager
    m = SessionManager()
    m.register_session(ActiveSession("a", "00:00:00:00:00:01", "d", "1", 50, "Invitado", expira=100.0, metodo="Portal"))
    m.register_session(ActiveSession("b", "00:00:00:00:00:02", "d", "2", 20, "Alumno"))                 # 802.1X: no vence
    m.register_session(ActiveSession("c", "00:00:00:00:00:03", "d", "3", 50, "Invitado", expira=900.0, metodo="Portal"))
    assert [s.username for s in m.vencidas(500.0)] == ["a"]
