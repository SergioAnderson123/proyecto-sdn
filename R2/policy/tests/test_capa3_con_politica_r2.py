"""La capa 3 EN LA TABLA 60 detras de las tablas de rol de R2: la autorizacion se mantiene. Tuberia completa (tabla 0 de R2 + sesion + tabla de rol + tabla 60 de capa 3 del vigilante)
sobre la topologia de pruebas de 5 switches. Es lo que pasara en la conmutacion real (docs/PLAN_CONMUTACION_CAPA3.md)."""
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import capa3 as l3  # noqa: E402
import policy_tables as pt  # noqa: E402
import recalculo as rc  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
from test_caminos import PREF, A1, A2, B, C1  # noqa: E402
from test_fase_e import G, IP, MAC, TODOS, UBICACION, onos_propios, politica, sesion  # noqa: E402

GW = "02:00:5E:00:00:FE"
PLAN = l3.cargar_plan({"gw_mac": GW, "subredes": [
    {"nombre": "servicios", "dispositivo": C1, "prefijo": "10.0.0.0/24", "gateway": "10.0.0.254", "puertos": [8]},
    {"nombre": "acc1", "dispositivo": A1, "prefijo": "10.0.1.0/24", "gateway": "10.0.1.254", "puertos": [1]},
    {"nombre": "acc2", "dispositivo": A2, "prefijo": "10.0.2.0/24", "gateway": "10.0.2.254", "puertos": [1]},
    {"nombre": "borde", "dispositivo": B, "prefijo": "10.0.3.0/24", "gateway": "10.0.3.254", "puertos": [3]}]})
IPS = {"servidor": "10.0.0.10", "alumno": "10.0.1.101", "invitado": "10.0.2.102"}


def red(vlan_alumno=None):
    pol = politica()
    hosts = [{"mac": MAC["srv1"], "device": C1, "port": 8, "ip": IPS["servidor"]},
             {"mac": MAC["alumno"], "device": A1, "port": 1, "ip": IPS["alumno"]},
             {"mac": MAC["invitado"], "device": A2, "port": 1, "ip": IPS["invitado"]}]
    l3_flujos = rc.flujos_deseados_capa3(G, hosts, PLAN, PREF)
    arp = lambda d: pt._base(d, 0, 40003, [{"type": "ETH_TYPE", "ethType": "0x0806"}], [{"type": "TABLE", "tableId": 60}])  # noqa: E731
    flujos = {d: pt.flujos_dispositivo(pol, d) + onos_propios(d) + [arp(d)] + [f for f in l3_flujos if f["deviceId"] == d] for d in TODOS}
    if vlan_alumno:
        flujos[A1].append(sesion(A1, 1, MAC["alumno"], vlan_alumno))
    fisico = {}
    for d, salidas in G.items():
        for nb, p in salidas:
            fisico[(d, p)] = (nb, next(q for v, q in G[nb] if v == d))
    return {"flujos": flujos, "fisico": fisico, "hosts": {u: n for n, u in UBICACION.items() if n in ("alumno", "invitado", "srv1")}}


def paquete(origen_mac, ip_src, ip_dst, dport=8101, proto=6, ttl=64):
    return {"eth_src": origen_mac, "eth_dst": GW, "eth_type": 0x0800, "vlan": None, "ip_src": ip_src, "ip_dst": ip_dst, "ip_proto": proto, "tcp_dst": dport, "ttl": ttl}


def desde(r, nombre, pkt):
    d, p = UBICACION[nombre]
    return sim.entregar(r, d, p, pkt)


def test_un_docente_con_sesion_llega_al_sistema_de_notas_por_el_gateway_con_mac_y_ttl_correctos():
    res = desde(red(10), "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"]))
    assert res["entregado"] == {"srv1": 1} and not res["inunda"] and not res["cortado"] and not res["controlador"]
    llegado = res["paquetes"]["srv1"][0]
    assert llegado["eth_dst"].lower() == MAC["srv1"] and llegado["eth_src"].upper() == GW and llegado["ttl"] < 64


def test_un_alumno_con_sesion_sigue_sin_alcanzar_el_sistema_de_notas_pero_si_la_biblioteca():
    r = red(20)
    assert desde(r, "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"], 8101))["entregado"] == {}
    assert desde(r, "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"], 8001))["entregado"] == {"srv1": 1}


@pytest.mark.parametrize("puerto", [22, 80, 8100])
def test_los_puertos_que_el_rol_no_permite_se_descartan_aunque_vayan_por_el_gateway(puerto):
    assert desde(red(10), "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"], puerto))["entregado"] == {}


def test_el_icmp_se_descarta_aunque_haya_sesion():
    assert desde(red(10), "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"], 0, proto=1))["entregado"] == {}


def test_un_host_sin_sesion_no_puede_usar_el_gateway_cuarentena():
    r = red()
    assert desde(r, "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"]))["entregado"] == {}
    assert desde(r, "invitado", paquete(MAC["invitado"], IPS["invitado"], IPS["alumno"], 8001))["entregado"] == {}


def test_la_respuesta_del_servidor_vuelve_al_docente_por_la_confianza_sin_pasar_por_su_politica():
    resp = {"eth_src": MAC["srv1"], "eth_dst": GW, "eth_type": 0x0800, "vlan": None, "ip_src": IPS["servidor"], "ip_dst": IPS["alumno"],
            "ip_proto": 6, "tcp_src": 8101, "tcp_dst": 40000, "ttl": 64}
    res = desde(red(10), "srv1", resp)
    assert res["entregado"] == {"alumno": 1} and not res["inunda"]


def test_una_ip_fuera_del_plan_se_descarta():
    assert desde(red(10), "alumno", paquete(MAC["alumno"], IPS["alumno"], "10.0.9.9"))["entregado"] == {}


def test_el_arp_de_un_host_sin_sesion_sigue_llegando_solo_al_controlador_o_a_la_tabla_60_y_no_inunda():
    arp = {"eth_src": MAC["invitado"], "eth_dst": "ff:ff:ff:ff:ff:ff", "eth_type": 0x0806, "vlan": None, "arp_op": 1, "arp_tpa": "10.0.2.77", "ttl": 64}
    res = desde(red(), "invitado", arp)
    assert not res["inunda"] and not res["cortado"]
