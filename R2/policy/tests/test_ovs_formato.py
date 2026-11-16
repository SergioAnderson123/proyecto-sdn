"""Formateador de flujos para ovs-ofctl (ovs_formato.py): líneas exactas de los flujos que usa la prueba de viabilidad de la capa 3."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capa3 as l3  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
import ovs_formato as ovs  # noqa: E402

COOKIE = 0x99C3000000000001
GW = "02:00:5E:99:99:FE"


def f(tabla, prio, criterios, instrucciones):
    return fp._base("of:1", tabla, prio, criterios, instrucciones)


def test_el_arp_del_gateway_se_traduce_al_idioma_de_respuesta_de_open_vswitch():
    plan = l3.cargar_plan({"gw_mac": GW, "subredes": [{"nombre": "a", "dispositivo": "d", "prefijo": "10.99.1.0/24", "gateway": "10.99.1.254", "puertos": [1]}]})
    flujo = next(x for x in l3.flujos_capa3(plan, {"d": []}, [], tabla=62) if x["priority"] == l3.PRIO_ARP_GATEWAY)
    assert ovs.a_ovs(flujo, COOKIE) == (
        "cookie=0x99c3000000000001,table=62,priority=300,in_port=1,arp,arp_op=1,arp_tpa=10.99.1.254/32 actions="
        "move:NXM_OF_ETH_SRC[]->NXM_OF_ETH_DST[],mod_dl_src:02:00:5e:99:99:fe,load:0x2->NXM_OF_ARP_OP[],move:NXM_NX_ARP_SHA[]->NXM_NX_ARP_THA[],"
        "move:NXM_OF_ARP_SPA[]->NXM_OF_ARP_TPA[],load:0x02005e9999fe->NXM_NX_ARP_SHA[],load:0x0a6301fe->NXM_OF_ARP_SPA[],in_port")


def test_la_ruta_la_entrega_y_la_entrada_a_la_tabla_de_prueba():
    ruta = f(62, 150, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": GW}, {"type": "IPV4_DST", "ip": "10.99.2.0/24"}],
             [{"type": "L3MODIFICATION", "subtype": "DEC_TTL"}, {"type": "OUTPUT", "port": "3"}])
    assert ovs.a_ovs(ruta, COOKIE) == "cookie=0x99c3000000000001,table=62,priority=150,ip,dl_dst=02:00:5e:99:99:fe,nw_dst=10.99.2.0/24 actions=dec_ttl,output:3"
    entrega = f(62, 160, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": GW}, {"type": "IPV4_DST", "ip": "10.99.1.101/32"}], [
        {"type": "L2MODIFICATION", "subtype": "ETH_SRC", "mac": GW}, {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": "FA:16:3E:B6:96:08"},
        {"type": "L3MODIFICATION", "subtype": "DEC_TTL"}, {"type": "OUTPUT", "port": "1"}])
    assert ovs.a_ovs(entrega, COOKIE).endswith("actions=mod_dl_src:02:00:5e:99:99:fe,mod_dl_dst:fa:16:3e:b6:96:08,dec_ttl,output:1")
    entrada = f(0, 40060, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": GW}], [{"type": "TABLE", "tableId": 62}])
    assert ovs.a_ovs(entrada, COOKIE) == "cookie=0x99c3000000000001,table=0,priority=40060,ip,dl_dst=02:00:5e:99:99:fe actions=goto_table:62"


def test_sin_instrucciones_es_un_descarte_y_la_copia_al_controlador_se_traduce():
    assert ovs.a_ovs(f(62, 1, [], []), COOKIE) == "cookie=0x99c3000000000001,table=62,priority=1 actions=drop"
    assert ovs.a_ovs(f(62, 5, [{"type": "ETH_TYPE", "ethType": "0x88b5"}], [{"type": "OUTPUT", "port": "CONTROLLER"}]), COOKIE).endswith("dl_type=0x88b5 actions=controller")


@pytest.mark.parametrize("flujo", [
    f(0, 1, [{"type": "TCP_DST", "tcpPort": 80}], []),
    f(0, 1, [], [{"type": "OUTPUT", "port": "NORMAL"}]),
    f(0, 1, [], [{"type": "EXTENSION", "extensionType": "NICIRA_RESUBMIT"}]),
    f(0, 1, [], [{"type": "L3MODIFICATION", "subtype": "TTL_IN"}]),
])
def test_lo_que_no_se_conoce_falla_en_vez_de_instalar_algo_distinto(flujo):
    with pytest.raises(ValueError):
        ovs.a_ovs(flujo, COOKIE)
