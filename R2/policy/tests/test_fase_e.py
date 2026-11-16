"""Fase E: el tráfico IP de R2 se entrega por la tabla 60 (sin NORMAL). Pruebas con el simulador de la tubería sobre la topología de 5 switches con lazos."""
import copy
import json
import os
import sys

import pytest
import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import caminos as c  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
import policy_tables as pt  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402

TODOS = [B, C1, C2, A1, A2]
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(AQUI)))
MAC = {"alumno": "aa:00:00:00:00:01", "invitado": "aa:00:00:00:00:02", "jhon": "aa:00:00:00:00:03",
       "srv1": "02:00:00:00:10:01", "srv2": "02:00:00:00:10:02"}
IP = {"alumno": "10.0.0.101", "invitado": "10.0.0.102", "jhon": "10.0.0.103", "servidor": "10.0.0.10"}
UBICACION = {"alumno": (A1, 1), "invitado": (A2, 1), "jhon": (B, 3), "srv1": (C1, 8), "srv2": (C2, 8)}
ACCESO = {A1: [1], A2: [1], B: [3], C1: [8], C2: [8]}
G = c.grafo_desde_links(LINKS)
BCAST = "ff:ff:ff:ff:ff:ff"


def troncales(d):
    return sorted({p for _, p in G[d]})


def politica(reenvio="proactivo"):
    """La política del repositorio adaptada a la topología de 14 VM: una sola IP de servicios, confianza por puerto, cores sin usuarios, jhon externo."""
    p = copy.deepcopy(pt.cargar_politica(os.path.join(os.path.dirname(AQUI), "policy.yaml")))
    p["reenvio"] = reenvio
    p["services"] = {k: {"ip": IP["servidor"], "tcp": v["tcp"]} for k, v in p["services"].items()}
    p["portal"] = dict(p["portal"], ip=IP["servidor"], mac=MAC["srv1"], puerto=8443, switches=[A1, A2, B], desvios=[])
    p["trusted"] = {A1: troncales(A1), A2: troncales(A2), B: troncales(B), C1: troncales(C1) + [8], C2: troncales(C2) + [8]}
    p["sin_usuarios"] = [C1, C2]
    p["puertos_externos"] = {B: [3]}
    return p


def onos_propios(d):
    """Lo que ONOS instala por su cuenta en la tabla 0 (prioridad 40000)."""
    return [pt._base(d, 0, 40000, [{"type": "ETH_TYPE", "ethType": et}], [{"type": "OUTPUT", "port": "CONTROLLER"}]) for et in ("0x88cc", "0x8942", "0x888e")] + [
        pt._base(d, 0, 40000, [{"type": "ETH_TYPE", "ethType": "0x0806"}], [{"type": "OUTPUT", "port": "CONTROLLER"}]),
        pt._base(d, 0, 40000, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "IP_PROTO", "protocol": 17}, {"type": "UDP_SRC", "udpPort": 68},
                               {"type": "UDP_DST", "udpPort": 67}], [{"type": "OUTPUT", "port": "CONTROLLER"}])]


def sesion(d, puerto, mac, vlan):
    """El flujo de sesión del Policy Mapper (inyectar_flujo_tabla_0)."""
    return pt._base(d, 0, 40001, [{"type": "IN_PORT", "port": puerto}, {"type": "ETH_SRC", "mac": mac}], [
        {"type": "L2MODIFICATION", "subtype": "VLAN_PUSH", "ethernetType": "0x8100"},
        {"type": "L2MODIFICATION", "subtype": "VLAN_ID", "vlanId": vlan}, {"type": "TABLE", "tableId": vlan}])


def red(pol, sesiones=()):
    hosts = [{"mac": MAC[n], "device": d, "port": p} for n, (d, p) in UBICACION.items()]
    t60 = fp.flujos_unicast(hosts, G, TODOS, PREF) + fp.flujos_descarte(TODOS) + fp.flujos_difusion(G, C1, ACCESO, PREF)
    arp = lambda d: pt._base(d, 0, 40003, [{"type": "ETH_TYPE", "ethType": "0x0806"}], [{"type": "TABLE", "tableId": 60}])  # noqa: E731
    flujos = {d: pt.flujos_dispositivo(pol, d) + onos_propios(d) + [arp(d)] + [f for f in t60 if f["deviceId"] == d] for d in TODOS}
    for d, puerto, mac, vlan in sesiones:
        flujos[d].append(sesion(d, puerto, mac, vlan))
    fisico = {}
    for d, salidas in G.items():
        for nb, p in salidas:
            fisico[(d, p)] = (nb, next(q for v, q in G[nb] if v == d))
    return {"flujos": flujos, "fisico": fisico, "hosts": {u: n for n, u in UBICACION.items()}}


def tcp(origen, dst_mac, dport, destino="servidor"):
    return {"eth_src": MAC[origen], "eth_dst": dst_mac, "eth_type": 0x0800, "vlan": None, "ip_src": IP[origen], "ip_dst": IP[destino],
            "ip_proto": 6, "tcp_dst": dport}


def desde(r, origen, pkt):
    d, p = UBICACION[origen]
    return sim.entregar(r, d, p, pkt)


@pytest.fixture(scope="module")
def pol():
    return politica()


@pytest.fixture(scope="module")
def r_alumno(pol):
    return red(pol, [(A1, 1, MAC["alumno"], 20)])


# ---------- invariantes ----------
def test_el_modo_proactivo_no_genera_ninguna_inundacion(pol):
    r = red(pol, [(A1, 1, MAC["alumno"], 20)])
    todos = [f for fl in r["flujos"].values() for f in fl]
    assert todos and c.flujos_que_inundan(todos) == [] and "NORMAL" not in json.dumps(todos)


def test_la_politica_no_cambia_solo_la_entrega(pol):
    """Mismas tablas de rol y cuarentena, mismas coincidencias y prioridades: solo cambia la última acción (NORMAL -> tabla 60)."""
    normal = politica("normal")
    for d in (A1, B):
        a = [f for f in pt.flujos_dispositivo(normal, d) if f["tableId"] != 0]
        b = [f for f in pt.flujos_dispositivo(pol, d) if f["tableId"] != 0]
        clave = lambda f: (f["tableId"], f["priority"], json.dumps(f["selector"], sort_keys=True))  # noqa: E731
        assert sorted(map(clave, a)) == sorted(map(clave, b)) and a
        asignadas = [f for f in a if f["treatment"]["instructions"]]
        assert all(any(i["port"] == "NORMAL" for i in f["treatment"]["instructions"] if i["type"] == "OUTPUT") for f in asignadas)
        permitidas = [f for f in b if f["treatment"]["instructions"]]
        assert all(i["type"] != "OUTPUT" or i["port"] != "NORMAL" for f in permitidas for i in f["treatment"]["instructions"])


def test_con_la_bandera_apagada_nada_cambia():
    normal = politica("normal")
    assert pt.reenvio_proactivo(normal) is False and pt.reenvio_proactivo(pt.cargar_politica()) is False
    assert pt.flujos_descubrimiento(normal, A1) == []
    assert any(i.get("port") == "NORMAL" for f in pt.flujos_dispositivo(normal, A1) for i in f["treatment"]["instructions"])
    assert pt.flujos_dhcp_externo(pt.cargar_politica(), B) == []                 # sin `puertos_externos` no hay nada


def test_las_prioridades_nuevas_coinciden_con_el_mapa():
    with open(os.path.join(RAIZ, "R5", "deploy", "prioridades.yaml"), encoding="utf8") as f:
        m = yaml.safe_load(f)
    assert pt.PRIO_DESCUBRIMIENTO == m["r2"]["descubrimiento"] and pt.PRIO_DHCP_EXTERNO == m["r2"]["dhcp_externo"]
    r2 = m["r2"]
    assert r2["dhcp"] < r2["dhcp_externo"] < m["r5"]["espejo"] < m["r5"]["drop"] < m["r1"]["drop_temporal"]
    assert r2["reenvio_arp"] < r2["descubrimiento"] < r2["dhcp"]                 # LLDP/BDDP sobre confianza, portal y ARP; bajo el DHCP
    assert r2["descubrimiento"] > max(r2["confianza"], r2["portal_arp"], r2["sesion"])


def test_los_cores_no_llevan_tablas_de_rol_ni_dhcp_ni_cuarentena(pol):
    f = pt.flujos_dispositivo(pol, C1)
    assert f and all(x["tableId"] == 0 for x in f) and not any(x["priority"] in (40500, 1000) for x in f)
    assert any(x["tableId"] == 20 for x in pt.flujos_dispositivo(pol, A1))      # los accesos sí


# ---------- confianza solo IPv4 y descubrimiento de enlaces ----------
def test_las_reglas_de_confianza_solo_casan_ipv4(pol):
    for d in TODOS:
        flujos = pt.flujos_confianza(pol, d)
        assert flujos
        for f in flujos:
            assert {"type": "ETH_TYPE", "ethType": "0x0800"} in f["selector"]["criteria"]
            assert f["treatment"]["instructions"] == [{"type": "TABLE", "tableId": 60}]


def test_con_ipv4_y_sin_ella_en_la_confianza_el_lldp_llega_o_se_pierde():
    """Demuestra el riesgo: una confianza `in_port -> tabla` sin tipo de trama (40001) gana a la regla de ONOS (40000) y se traga el LLDP."""
    lldp = {"eth_src": "02:00:00:00:00:99", "eth_dst": "01:80:c2:00:00:0e", "eth_type": 0x88CC, "in_port": 5, "vlan": None}
    mala = pt._base(A1, 0, 40001, [{"type": "IN_PORT", "port": 5}], [{"type": "TABLE", "tableId": 60}])
    buena = pt._base(A1, 0, 40001, [{"type": "IN_PORT", "port": 5}, {"type": "ETH_TYPE", "ethType": "0x0800"}], [{"type": "TABLE", "tableId": 60}])
    assert sim.ejecutar(onos_propios(A1) + [mala], lldp)["controlador"] is False        # sin la condición, ONOS deja de ver el cable
    assert sim.ejecutar(onos_propios(A1) + [buena], lldp)["controlador"] is True        # con IPv4 solo, llega por la regla de ONOS


def test_lldp_y_bddp_llegan_al_controlador_por_cada_puerto_troncal_de_cada_switch(pol):
    r = red(pol, [(A1, 1, MAC["alumno"], 20)])
    for d in TODOS:
        for p in troncales(d):
            for et in (0x88CC, 0x8942):
                res = sim.ejecutar(r["flujos"][d], {"eth_src": "02:00:00:00:00:99", "eth_dst": BCAST, "eth_type": et, "in_port": p, "vlan": None})
                assert res["controlador"] and not res["salidas"], (d, p, hex(et))


def test_el_refuerzo_de_lldp_funciona_aunque_alguien_amplie_la_confianza(pol):
    """Si una regla de confianza más ancha (40001) se colara, el refuerzo propio (40100) sigue subiendo el LLDP."""
    flujos = pt.flujos_descubrimiento(pol, A1) + onos_propios(A1)
    ancha = pt._base(A1, 0, 40001, [{"type": "IN_PORT", "port": 5}], [{"type": "TABLE", "tableId": 60}])
    lldp = {"eth_src": "02:00:00:00:00:99", "eth_dst": "01:80:c2:00:00:0e", "eth_type": 0x88CC, "in_port": 5, "vlan": None}
    assert sim.ejecutar(flujos + [ancha], lldp)["controlador"] is True
    assert {f["priority"] for f in pt.flujos_descubrimiento(pol, A1)} == {40100}


# ---------- de la sesión al servicio ----------
@pytest.mark.parametrize("srv", ["srv1", "srv2"])
def test_un_alumno_con_sesion_alcanza_un_servicio_permitido_por_cualquier_tarjeta_del_server(r_alumno, srv):
    res = desde(r_alumno, "alumno", tcp("alumno", MAC[srv], 8001))                   # virtual_library: permitido al alumno
    assert res["entregado"] == {srv: 1} and not res["controlador"] and not res["inunda"] and not res["cortado"]


def test_un_alumno_no_alcanza_el_sistema_de_notas(r_alumno):
    assert desde(r_alumno, "alumno", tcp("alumno", MAC["srv1"], 8101))["entregado"] == {}


@pytest.mark.parametrize("puerto", [22, 21, 23, 25, 8100, 80])
def test_ssh_ftp_telnet_smtp_y_otros_puertos_se_descartan(r_alumno, puerto):
    assert desde(r_alumno, "alumno", tcp("alumno", MAC["srv1"], puerto))["entregado"] == {}


def test_icmp_y_udp_se_descartan_aunque_vayan_a_un_servicio_permitido(r_alumno):
    icmp = dict(tcp("alumno", MAC["srv1"], 0), ip_proto=1)
    udp = dict(tcp("alumno", MAC["srv1"], 0), ip_proto=17, udp_dst=8001)
    assert desde(r_alumno, "alumno", icmp)["entregado"] == {} and desde(r_alumno, "alumno", udp)["entregado"] == {}


def test_otra_ip_distinta_de_la_del_server_se_descarta(r_alumno):
    otro = dict(tcp("alumno", MAC["srv1"], 8001), ip_dst="10.0.0.99")
    assert desde(r_alumno, "alumno", otro)["entregado"] == {}


def test_un_docente_si_alcanza_el_sistema_de_notas():
    r = red(politica(), [(A1, 1, MAC["alumno"], 10)])
    assert desde(r, "alumno", tcp("alumno", MAC["srv1"], 8101))["entregado"] == {"srv1": 1}


def test_la_respuesta_del_servidor_llega_al_alumno_sin_pasar_por_su_politica(r_alumno):
    resp = {"eth_src": MAC["srv1"], "eth_dst": MAC["alumno"], "eth_type": 0x0800, "vlan": None, "ip_src": IP["servidor"], "ip_dst": IP["alumno"],
            "ip_proto": 6, "tcp_src": 8101, "tcp_dst": 40000}                      # ni siquiera un puerto que el alumno pueda usar: la respuesta no se filtra
    res = desde(r_alumno, "srv1", resp)
    assert res["entregado"] == {"alumno": 1} and not res["inunda"]


def test_la_respuesta_llega_aunque_salga_por_la_otra_tarjeta_del_server(r_alumno):
    resp = {"eth_src": MAC["srv2"], "eth_dst": MAC["alumno"], "eth_type": 0x0800, "vlan": None, "ip_src": IP["servidor"], "ip_dst": IP["alumno"],
            "ip_proto": 6, "tcp_src": 8001, "tcp_dst": 40000}
    assert desde(r_alumno, "srv2", resp)["entregado"] == {"alumno": 1}


# ---------- invitados, cuarentena y externos ----------
def test_un_invitado_sin_sesion_solo_llega_al_portal(pol):
    r = red(pol)
    assert desde(r, "invitado", tcp("invitado", MAC["srv1"], 8443))["entregado"] == {"srv1": 1}      # el portal
    assert desde(r, "invitado", tcp("invitado", MAC["srv1"], 8001))["entregado"] == {}                # un servicio: no
    assert desde(r, "invitado", tcp("invitado", MAC["srv1"], 22))["entregado"] == {}


def test_el_dhcp_de_un_invitado_va_al_controlador(pol):
    dhcp = {"eth_src": MAC["invitado"], "eth_dst": BCAST, "eth_type": 0x0800, "vlan": None, "ip_src": "0.0.0.0", "ip_dst": "255.255.255.255",
            "ip_proto": 17, "udp_src": 68, "udp_dst": 67}
    res = desde(red(pol), "invitado", dhcp)
    assert res["controlador"] == [A2] and res["entregado"] == {}


def test_el_dhcp_del_equipo_externo_se_descarta_y_no_llega_ni_al_controlador(pol):
    dhcp = {"eth_src": MAC["jhon"], "eth_dst": BCAST, "eth_type": 0x0800, "vlan": None, "ip_src": "0.0.0.0", "ip_dst": "255.255.255.255",
            "ip_proto": 17, "udp_src": 68, "udp_dst": 67}
    res = desde(red(pol), "jhon", dhcp)
    assert res["controlador"] == [] and res["entregado"] == {}                       # gana a la regla de R2 (40500) y a la de ONOS (40000)
    sin = desde(red(politica_sin_externos()), "jhon", dhcp)
    assert sin["controlador"] == [B]                                                 # sin `puertos_externos` sí llegaría al controlador


def politica_sin_externos():
    p = politica()
    p["puertos_externos"] = {}
    return p


def test_el_externo_en_cuarentena_solo_llega_al_portal(pol):
    r = red(pol)
    assert desde(r, "jhon", tcp("jhon", MAC["srv1"], 8443))["entregado"] == {"srv1": 1}
    assert desde(r, "jhon", tcp("jhon", MAC["srv1"], 8001))["entregado"] == {}
    assert desde(r, "jhon", tcp("jhon", MAC["srv1"], 22))["entregado"] == {}


# ---------- ARP ----------
@pytest.mark.parametrize("origen", ["alumno", "invitado", "jhon", "srv1", "srv2"])
def test_el_arp_en_difusion_llega_una_vez_a_cada_host_y_nunca_se_duplica(pol, origen):
    arp = {"eth_src": MAC[origen], "eth_dst": BCAST, "eth_type": 0x0806, "vlan": None}
    res = desde(red(pol, [(A1, 1, MAC["alumno"], 20)]), origen, arp)
    esperado = {n: 1 for n in UBICACION if n != origen}
    assert res["entregado"] == esperado and not res["cortado"] and not res["inunda"]
    assert all(v == 1 for v in res["llegadas"].values())
