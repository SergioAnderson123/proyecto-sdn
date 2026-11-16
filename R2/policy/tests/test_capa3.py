"""Capa 3 por subredes (capa3.py) sobre la topología de pruebas de 5 switches con lazos: entrega de punta a punta con reescritura de MAC y TTL, ARP del gateway, ARP local sin salir del
switch, rutas por prefijo sin lazos ante caídas, modo de ruta por defecto y la propiedad que justifica todo: las reglas del núcleo no crecen con el número de hosts."""
import itertools
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import caminos as c  # noqa: E402
import capa3 as l3  # noqa: E402
from benchmark_capa3 import plan_sintetico  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
import policy_tables as pt  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402

TODOS = [B, C1, C2, A1, A2]
GW = "02:00:5E:00:00:FE"
BCAST = "ff:ff:ff:ff:ff:ff"
PLAN = l3.cargar_plan({"gw_mac": GW, "subredes": [
    {"nombre": "servicios", "dispositivo": C1, "prefijo": "10.0.0.0/24", "gateway": "10.0.0.254", "puertos": [8]},
    {"nombre": "acc1", "dispositivo": A1, "prefijo": "10.0.1.0/24", "gateway": "10.0.1.254", "puertos": [1, 2]},
    {"nombre": "acc2", "dispositivo": A2, "prefijo": "10.0.2.0/24", "gateway": "10.0.2.254", "puertos": [1]},
    {"nombre": "borde", "dispositivo": B, "prefijo": "10.0.3.0/24", "gateway": "10.0.3.254", "puertos": [3]}]})
HOSTS = [{"mac": "02:00:00:00:10:01", "ip": "10.0.0.10", "device": C1, "port": 8},
         {"mac": "aa:00:00:00:01:01", "ip": "10.0.1.101", "device": A1, "port": 1},
         {"mac": "aa:00:00:00:01:02", "ip": "10.0.1.102", "device": A1, "port": 2},
         {"mac": "aa:00:00:00:02:01", "ip": "10.0.2.101", "device": A2, "port": 1},
         {"mac": "aa:00:00:00:03:01", "ip": "10.0.3.101", "device": B, "port": 3}]
NOMBRE = {(h["device"], h["port"]): h["ip"] for h in HOSTS}
G = c.grafo_desde_links(LINKS)


def red(grafo=G, hosts=HOSTS, plan=PLAN, **kw):
    flujos = l3.flujos_capa3(plan, grafo, hosts, PREF, **kw)
    arp = lambda d: pt._base(d, 0, 40003, [{"type": "ETH_TYPE", "ethType": "0x0806"}], [{"type": "TABLE", "tableId": 60}])    # noqa: E731
    ip = lambda d: pt._base(d, 0, 40001, [{"type": "ETH_TYPE", "ethType": "0x0800"}], [{"type": "TABLE", "tableId": 60}])     # noqa: E731
    por = {d: [arp(d), ip(d)] + [f for f in flujos if f["deviceId"] == d] for d in grafo}
    fisico = {}
    for d, salidas in grafo.items():
        for nb, p in salidas:
            fisico[(d, p)] = (nb, next(q for v, q in grafo[nb] if v == d))
    ubic = {(h["device"], h["port"]): h["ip"] for h in hosts}
    return {"flujos": por, "fisico": fisico, "hosts": ubic}, flujos


def tcp(origen, destino, dst_mac=GW, ttl=64):
    return {"eth_src": origen["mac"], "eth_dst": dst_mac, "eth_type": 0x0800, "vlan": None, "ip_src": origen["ip"], "ip_dst": destino["ip"],
            "ip_proto": 6, "tcp_dst": 8001, "ttl": ttl}


def desde(r, h, pkt):
    return sim.entregar(r, h["device"], h["port"], pkt)


SRV, ALU, ALU2, INV, JHON = HOSTS


# ---------- el plan ----------
def test_el_plan_se_valida_y_rechaza_incoherencias():
    base = {"gw_mac": GW, "subredes": [{"nombre": "a", "dispositivo": A1, "prefijo": "10.0.1.0/24", "gateway": "10.0.1.254", "puertos": [1]}]}
    l3.cargar_plan(base)
    malos = [
        {"gw_mac": "xx", "subredes": base["subredes"]},
        {"gw_mac": GW, "subredes": []},
        {"gw_mac": GW, "subredes": [dict(base["subredes"][0], gateway="10.0.2.1")]},                                         # gateway fuera del prefijo
        {"gw_mac": GW, "subredes": [dict(base["subredes"][0], gateway="10.0.1.0")]},                                         # dirección de red
        {"gw_mac": GW, "subredes": [dict(base["subredes"][0], prefijo="10.0.1.5/24")]},                                      # prefijo con bits de host
        {"gw_mac": GW, "subredes": base["subredes"] + [dict(base["subredes"][0], nombre="b", dispositivo=A2, prefijo="10.0.1.0/25", gateway="10.0.1.1")]},  # se solapan
        {"gw_mac": GW, "subredes": base["subredes"] + [dict(base["subredes"][0], nombre="b", prefijo="10.0.9.0/24", gateway="10.0.9.1")]},                # mismo puerto
        {"gw_mac": GW, "subredes": [dict(base["subredes"][0], puertos=[])]},
    ]
    for mal in malos:
        with pytest.raises(l3.PlanInvalido):
            l3.cargar_plan(mal)


def test_los_hosts_que_no_encajan_se_apartan_con_su_motivo():
    extra = [{"mac": "aa:00:00:00:09:09", "ip": None, "device": A1, "port": 1},
             {"mac": "aa:00:00:00:09:08", "ip": "192.168.5.5", "device": A1, "port": 1},
             {"mac": "aa:00:00:00:09:07", "ip": "10.0.2.5", "device": A1, "port": 1},                                        # IP de acc2 conectado en acc1
             {"mac": "aa:00:00:00:09:06", "ip": "10.0.1.254", "device": A1, "port": 1}]                                      # usa el gateway
    validos, rechazados = l3.hosts_validos(PLAN, HOSTS + extra)
    assert len(validos) == len(HOSTS) and [m for _, m in rechazados] == [
        "sin IP conocida", "su IP no está en ninguna subred del plan", "IP de acc2 pero conectado en acc1/1", "usa la IP del gateway"]


# ---------- propiedades de los flujos ----------
@pytest.mark.parametrize("opciones", [{}, {"nucleo": [C1, C2, B]}, {"copiar_arp": True}])
def test_ningun_flujo_inunda_ni_salta_hacia_atras_y_todo_va_en_la_tabla_60(opciones):
    _, flujos = red(**opciones)
    assert flujos and c.flujos_que_inundan(flujos) == [] and {f["tableId"] for f in flujos} == {60}
    for f in flujos:
        for i in f["treatment"]["instructions"]:
            assert i["type"] != "TABLE" and str(i.get("port", "")).upper() not in ("NORMAL", "FLOOD", "ALL")


# ---------- entrega de punta a punta ----------
@pytest.mark.parametrize("origen,destino", list(itertools.permutations([ALU, INV, JHON, SRV], 2)))
def test_un_paquete_entre_subredes_llega_una_sola_vez_con_las_mac_y_el_ttl_correctos(origen, destino):
    r, _ = red()
    res = desde(r, origen, tcp(origen, destino))
    nombre = NOMBRE[(destino["device"], destino["port"])]
    assert res["entregado"] == {nombre: 1} and not res["inunda"] and not res["cortado"] and not res["ttl_agotado"]
    final = res["paquetes"][nombre][0]
    assert final["eth_dst"].lower() == destino["mac"].lower() and final["eth_src"].upper() == GW                  # el último salto reescribe: origen = gateway, destino = el host
    ruta = c.camino(c.arbol_hacia(G, destino["device"], PREF), origen["device"], destino["device"])
    assert final["ttl"] == 64 - (len(ruta) - 1) - 1                                               # un decremento por salto de ruta y otro al entregar


def test_dentro_de_la_subred_el_trafico_se_reenvia_por_mac_en_el_mismo_switch():
    r, _ = red()
    pkt = {"eth_src": ALU["mac"], "eth_dst": ALU2["mac"], "eth_type": 0x0800, "vlan": None, "ip_src": ALU["ip"], "ip_dst": ALU2["ip"], "ip_proto": 6, "tcp_dst": 80}
    res = desde(r, ALU, pkt)
    assert res["entregado"] == {"10.0.1.102": 1} and res["llegadas"] == {A1: 1}                  # nunca salió de acc1


def test_una_ip_desconocida_de_una_subred_conocida_se_descarta_en_el_switch_del_destino_sin_inundar():
    r, _ = red()
    fantasma = {"mac": "aa:00:00:00:ff:ff", "ip": "10.0.2.99"}
    res = desde(r, ALU, tcp(ALU, fantasma))
    assert res["entregado"] == {} and not res["inunda"] and not res["cortado"] and res["llegadas"].get(A2) == 1


def test_un_paquete_a_la_mac_del_gateway_con_destino_fuera_del_plan_se_descarta():
    r, _ = red()
    res = desde(r, ALU, tcp(ALU, {"mac": "x", "ip": "172.16.0.1"}))
    assert res["entregado"] == {} and not res["inunda"]


def test_un_ttl_agotado_corta_el_paquete():
    r, _ = red()
    res = desde(r, ALU, tcp(ALU, SRV, ttl=1))
    assert res["entregado"] == {} and res["ttl_agotado"]


# ---------- ARP ----------
def arp_peticion(origen, objetivo_ip):
    return {"eth_src": origen["mac"], "eth_dst": BCAST, "eth_type": 0x0806, "vlan": None, "arp_op": 1, "arp_sha": origen["mac"],
            "arp_spa": origen["ip"], "arp_tha": "00:00:00:00:00:00", "arp_tpa": objetivo_ip}


def test_el_gateway_contesta_el_arp_por_el_puerto_de_entrada_y_sin_salir_del_switch():
    r, _ = red()
    res = desde(r, ALU, arp_peticion(ALU, "10.0.1.254"))
    assert res["entregado"] == {"10.0.1.101": 1} and res["llegadas"] == {A1: 1} and not res["inunda"]
    resp = res["paquetes"]["10.0.1.101"][0]
    assert resp["arp_op"] == 2 and resp["eth_src"].upper() == GW and resp["eth_dst"] == ALU["mac"]
    assert resp["arp_sha"].upper() == GW and resp["arp_spa"] == "10.0.1.254" and resp["arp_tha"] == ALU["mac"] and resp["arp_tpa"] == ALU["ip"]


def test_el_arp_de_un_vecino_se_inunda_solo_por_los_puertos_de_acceso_de_la_subred():
    r, _ = red()
    res = desde(r, ALU, arp_peticion(ALU, "10.0.1.102"))
    assert res["entregado"] == {"10.0.1.102": 1} and res["llegadas"] == {A1: 1}                  # llegó al vecino y no cruzó ninguna troncal


@pytest.mark.parametrize("origen", [ALU, INV, JHON, SRV])
def test_ningun_arp_cruza_una_troncal_jamas(origen):
    r, _ = red()
    for objetivo in ("10.0.0.254", "10.0.1.254", "10.0.2.77", "10.0.9.9", "10.0.0.10"):
        res = desde(r, origen, arp_peticion(origen, objetivo))
        assert set(res["llegadas"]) == {origen["device"]} and not res["cortado"], (origen["ip"], objetivo)


def test_con_copiar_arp_el_gateway_sube_una_copia_de_la_peticion_al_controlador():
    r, _ = red(copiar_arp=True)
    res = desde(r, ALU, arp_peticion(ALU, "10.0.1.254"))
    assert res["controlador"] == [A1] and res["entregado"] == {"10.0.1.101": 1}
    r2, _ = red(copiar_arp=False)
    assert desde(r2, ALU, arp_peticion(ALU, "10.0.1.254"))["controlador"] == []


# ---------- caídas: las rutas siguen siendo árboles ----------
def sin_cable(a, b):
    return [e for e in LINKS if not ({e["src"]["device"], e["dst"]["device"]} == {a, b})]


@pytest.mark.parametrize("a,b", [(C1, A1), (C1, A2), (C2, A1), (C1, C2), (C1, B), (C2, B), (C2, A2)])
def test_ante_la_caida_de_un_cable_todo_sigue_llegando_sin_lazos_ni_inundar(a, b):
    g = c.grafo_desde_links(sin_cable(a, b))
    r, _ = red(grafo=g)
    for origen, destino in itertools.permutations([ALU, INV, JHON, SRV], 2):
        res = desde(r, origen, tcp(origen, destino))
        assert res["entregado"] == {NOMBRE[(destino["device"], destino["port"])]: 1}, (a, b, origen["ip"], destino["ip"])
        assert not res["inunda"] and not res["cortado"] and not res["ttl_agotado"]


def test_ante_la_caida_de_un_switch_los_demas_siguen_comunicados():
    links = [e for e in LINKS if C2 not in (e["src"]["device"], e["dst"]["device"])]
    g = c.grafo_desde_links(links)
    g.setdefault(C2, [])
    hosts = [h for h in HOSTS if h["device"] != C2]
    r, _ = red(grafo=g, hosts=hosts)
    for origen, destino in itertools.permutations([ALU, INV, JHON, SRV], 2):
        assert desde(r, origen, tcp(origen, destino))["entregado"] == {NOMBRE[(destino["device"], destino["port"])]: 1}


# ---------- modo con ruta por defecto en los accesos ----------
def test_con_ruta_por_defecto_los_accesos_no_llevan_rutas_por_prefijo_y_todo_sigue_llegando():
    r, flujos = red(nucleo=[C1, C2, B])
    en_acceso = [f for f in flujos if f["deviceId"] in (A1, A2) and f["priority"] == l3.PRIO_RUTA]
    assert en_acceso == []                                                                        # sin una regla por subred
    for origen, destino in itertools.permutations([ALU, INV, JHON, SRV], 2):
        res = desde(r, origen, tcp(origen, destino))
        assert res["entregado"] == {NOMBRE[(destino["device"], destino["port"])]: 1} and not res["inunda"] and not res["ttl_agotado"]


def test_con_ruta_por_defecto_un_paquete_de_la_troncal_hacia_una_ip_de_otra_subred_no_rebota():
    """La regla 'hacia arriba' exige entrar por un puerto de acceso: lo que llega por una troncal sin destino local se descarta (nunca vuelve a subir)."""
    r, _ = red(nucleo=[C1, C2, B])
    pkt = dict(tcp(ALU, INV), in_port=6)
    res = sim.ejecutar(r["flujos"][A1], dict(pkt, in_port=5))                                      # entra por la troncal 5 de acc1, destino en acc2
    assert res["salidas"] == [] and not res["inunda"]


# ---------- escala: lo que justifica la capa 3 ----------
def _reglas_por_tipo(accesos, por_acceso, **kw):
    plan, grafo, hosts = plan_sintetico(accesos, por_acceso)
    flujos = l3.flujos_capa3(plan, grafo, hosts, ["core1", "core2"], **kw)
    c3 = l3.contar_por_switch(flujos)
    l2 = fp.flujos_unicast([{"mac": h["mac"], "device": h["device"], "port": h["port"]} for h in hosts], grafo, grafo.keys(), ["core1", "core2"])
    return c3, len(l2) + len(grafo), sum(c3.values())


def test_las_reglas_del_nucleo_no_dependen_del_numero_de_hosts():
    pocas, _, _ = _reglas_por_tipo(10, 5)
    muchas, _, _ = _reglas_por_tipo(10, 40)
    assert pocas["core1"] == muchas["core1"] and pocas["core2"] == muchas["core2"]                # lo del núcleo solo depende de cuántas subredes hay
    assert pocas["core1"] == 10 + 1                                                               # una por subred (ruta) + el descarte


def test_las_reglas_de_un_acceso_crecen_con_sus_hosts_locales_y_no_con_los_de_otros_accesos():
    uno, _, _ = _reglas_por_tipo(10, 5)
    otro, _, _ = _reglas_por_tipo(10, 20)
    assert otro["acc3"] - uno["acc3"] == 2 * (20 - 5)                                            # dos reglas por host local (MAC y entrega /32)


def test_la_capa_3_necesita_muchas_menos_reglas_que_la_capa_2_con_muchos_hosts():
    c3, l2, total3 = _reglas_por_tipo(20, 48)                                                     # 960 hosts en 20 accesos
    assert total3 < l2 / 2                                                                        # la capa 2 pone una regla por host en CADA switch (22 switches)


def test_con_ruta_por_defecto_el_total_crece_linealmente_con_las_subredes():
    def total(accesos):
        c3, _, t = _reglas_por_tipo(accesos, 10, nucleo=["core1", "core2"])
        return c3["acc0"], t
    a10, t10 = total(10)
    a40, t40 = total(40)
    assert a10 == a40                                                                             # un acceso no sabe cuántas subredes hay: solo su ruta por defecto


# ---------- la prueba de viabilidad contra ONOS es inerte ----------
def test_los_flujos_de_la_prueba_de_viabilidad_son_inertes():
    """Van a la tabla 61 con prioridad 1: ningún flujo de R2 ni del modo capa 3 salta a ella, así que ningún paquete puede llegar."""
    import spike_capa3 as spike
    pruebas = spike.pruebas("of:1")
    assert len(pruebas) >= 8 and all(f["tableId"] == 61 and f["priority"] == 1 for f in pruebas)
    _, flujos = red()
    for f in flujos + [x for fl in red()[0]["flujos"].values() for x in fl]:
        for i in f["treatment"]["instructions"]:
            assert not (i["type"] == "TABLE" and int(i["tableId"]) == 61)
