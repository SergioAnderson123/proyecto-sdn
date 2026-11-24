"""Ruta por defecto hacia el exterior (capa3.Salida), con los datos REALES de la slice: los 14 enlaces de ONOS (`links.json`), el plan de subredes (con su `salida`) y los hosts fijos.

Caso real (23-nov): las respuestas del server hacia 203.0.113.0/24 llegaban al borde y se descartaban, porque el vigilante solo sabía entregar a hosts dados de alta uno a uno y el atacante (externo,
sin DHCP) no figuraba. Arreglo: no se da de alta a ningún externo; el borde lleva UNA ruta por defecto hacia el siguiente salto (la MAC del equipo del otro lado del puerto 1). Una IP externa nueva
no necesita ninguna regla."""
import copy
import json
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
sys.path.insert(0, AQUI)
import caminos as c  # noqa: E402
import capa3 as l3  # noqa: E402
import hosts_fijos  # noqa: E402
import plan_arboles as pa  # noqa: E402
import recalculo as rc  # noqa: E402
import test_capa3 as t  # noqa: E402

POR = {v: k for k, v in pa.NOMBRES.items()}
BORDE, CORE1, CORE2 = POR["borde"], POR["core-1"], POR["core-2"]
NUCLEO = [CORE1, CORE2, BORDE]                      # el --nucleo del despliegue (playbook 68)
GW = "02:00:5E:00:00:FE"
SIGUIENTE_SALTO = "FA:16:3E:EB:EA:4C"               # la VM del atacante, del otro lado del puerto 1 del borde
EXTERNAS = ["203.0.113.103", "203.0.113.77", "198.51.100.7", "8.8.8.8", "1.1.1.1"]
PRIVADAS_SIN_RUTA = ["10.0.0.77", "10.0.9.9", "10.200.0.1", "172.16.5.5", "192.168.1.1"]


def _json_plan():
    return json.load(open(os.path.join(RAIZ, "planes", "vnrt_topologia14.json"), encoding="utf-8"))


def _datos(quitar_salida=False):
    links = json.load(open(os.path.join(RAIZ, "links.json"), encoding="utf-8"))["links"]
    grafo = c.grafo_desde_links(links)
    datos = _json_plan()
    if quitar_salida:
        datos.pop("salida")
    plan = l3.cargar_plan(datos, nombres=pa.NOMBRES)
    fijos = hosts_fijos.cargar(os.path.join(RAIZ, "planes", "hosts_fijos_capa3.json"))
    hosts = [{"mac": h["mac"], "ip": h["ips"][0], "device": h["device"], "port": h["port"]} for h in fijos if h["ips"]]
    return grafo, plan, hosts


def _red(quitar_salida=False, **kw):
    grafo, plan, hosts = _datos(quitar_salida)
    r, flujos = t.red(grafo=grafo, hosts=hosts, plan=plan, pref=pa.PREFERIDOS, **kw)
    r["hosts"][(BORDE, 1)] = "otro-lado"            # el simulador etiqueta lo que sale por el puerto 1 del borde; el vigilante NO lo conoce
    return r, flujos, {h["ip"]: h for h in hosts}


def _externo(ip):
    return {"mac": "fa:16:3e:eb:ea:4c", "ip": ip, "device": BORDE, "port": 1}


# ---------- el plan ----------
def test_el_plan_real_trae_la_salida_por_el_puerto_1_del_borde_con_la_mac_de_la_vm_atacante():
    _, plan, _ = _datos()
    assert plan.salida.dispositivo == BORDE and plan.salida.puerto == 1 and plan.salida.mac == SIGUIENTE_SALTO
    assert [str(p) for p in plan.salida.bloquear] == list(l3.BLOQUEO_PRIVADO)


def test_el_atacante_no_se_da_de_alta_como_host_en_ninguna_parte():
    _, flujos, hosts = _red()
    assert "203.0.113.103" not in hosts
    assert not [f for f in flujos if "203.0.113.103" in json.dumps(f) or "203.0.113.77" in json.dumps(f)]      # el prefijo y el ARP del gateway sí existen; ninguna IP concreta
    assert not [f for f in flujos if f["priority"] == l3.PRIO_ENTREGA and "203.0.113." in json.dumps(f)]    # ni una entrega /32 al exterior


def test_un_plan_sin_salida_sigue_igual_y_no_genera_reglas_de_defecto_ni_de_bloqueo():
    _, flujos, _ = _red(quitar_salida=True)
    assert not [f for f in flujos if f["priority"] in (l3.PRIO_DEFECTO, l3.PRIO_BLOQUEO)]


@pytest.mark.parametrize("cambio", [
    {"mac": "xx"}, {"mac": None}, {"puerto": 0}, {"puerto": "uno"}, {"bloquear": ["10.0.0.5/8"]}, {"bloquear": ["fe80::/10"]}, {"bloquear": ["no-es-un-prefijo"]},
])
def test_una_salida_incoherente_se_rechaza_sin_generar_nada(cambio):
    datos = _json_plan()
    datos["salida"] = dict(datos["salida"], **cambio)
    with pytest.raises(l3.PlanInvalido):
        l3.cargar_plan(datos, nombres=pa.NOMBRES)


@pytest.mark.parametrize("falta", ["dispositivo", "puerto", "mac"])
def test_a_la_salida_le_puede_faltar_un_campo_y_se_avisa(falta):
    datos = _json_plan()
    del datos["salida"][falta]
    with pytest.raises(l3.PlanInvalido):
        l3.cargar_plan(datos, nombres=pa.NOMBRES)


# ---------- lo que se genera ----------
def test_el_borde_saca_por_el_puerto_1_hacia_el_siguiente_salto_con_las_mac_y_el_ttl_reescritos():
    _, flujos, _ = _red()
    (defecto,) = [f for f in flujos if f["deviceId"] == BORDE and f["priority"] == l3.PRIO_DEFECTO]
    crit = {x["type"]: x for x in defecto["selector"]["criteria"]}
    assert set(crit) == {"ETH_TYPE", "ETH_DST"} and crit["ETH_DST"]["mac"] == GW                   # solo IPv4 mandado a la MAC del gateway; sin IPV4_DST: es "todo lo demás"
    assert defecto["tableId"] == 60
    acc = defecto["treatment"]["instructions"]
    assert {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": SIGUIENTE_SALTO} in acc
    assert {"type": "L2MODIFICATION", "subtype": "ETH_SRC", "mac": GW} in acc
    assert {"type": "L3MODIFICATION", "subtype": "DEC_TTL"} in acc and acc[-1] == {"type": "OUTPUT", "port": "1"}


def test_cada_switch_del_nucleo_lleva_una_ruta_por_defecto_hacia_el_borde_y_el_bloqueo_de_privadas():
    _, flujos, _ = _red(nucleo=NUCLEO)
    for d in NUCLEO:
        defecto = [f for f in flujos if f["deviceId"] == d and f["priority"] == l3.PRIO_DEFECTO]
        bloqueo = [f for f in flujos if f["deviceId"] == d and f["priority"] == l3.PRIO_BLOQUEO]
        assert len(defecto) == 1 and len(bloqueo) == len(l3.BLOQUEO_PRIVADO), d
        assert all(f["treatment"]["instructions"] == [] for f in bloqueo)                          # descartar
    assert not [f for f in flujos if f["deviceId"] not in NUCLEO and f["priority"] in (l3.PRIO_DEFECTO, l3.PRIO_BLOQUEO)]


def test_las_reglas_de_salida_no_crecen_con_las_ips_externas_ni_con_los_hosts():
    _, base, _ = _red(nucleo=NUCLEO)
    grafo, plan, hosts = _datos()
    mas = hosts + [{"mac": f"aa:00:00:00:00:{i:02x}", "ip": f"10.0.0.{20 + i}", "device": CORE1, "port": 5} for i in range(1, 9)]
    _, con_mas = t.red(grafo=grafo, hosts=mas, plan=plan, pref=pa.PREFERIDOS, nucleo=NUCLEO)
    n = lambda fl: len([f for f in fl if f["priority"] in (l3.PRIO_DEFECTO, l3.PRIO_BLOQUEO)])      # noqa: E731
    assert n(base) == n(con_mas) == len(NUCLEO) * (1 + len(l3.BLOQUEO_PRIVADO))                    # por switch del núcleo: una de defecto (el borde, la de salida) y los bloqueos


@pytest.mark.parametrize("opciones", [{}, {"nucleo": NUCLEO}])
def test_nada_inunda_todo_va_a_la_tabla_60_y_no_hay_saltos_de_tabla(opciones):
    _, flujos, _ = _red(**opciones)
    assert c.flujos_que_inundan(flujos) == [] and {f["tableId"] for f in flujos} == {60}
    for f in flujos:
        for i in f["treatment"]["instructions"]:
            assert i["type"] != "TABLE" and str(i.get("port", "")).upper() not in ("NORMAL", "FLOOD", "ALL")


# ---------- de punta a punta con el simulador ----------
@pytest.mark.parametrize("opciones", [{}, {"nucleo": NUCLEO}])
@pytest.mark.parametrize("ip", EXTERNAS)
def test_el_server_llega_a_cualquier_ip_externa_por_el_siguiente_salto_sin_que_nadie_la_conozca(ip, opciones):
    r, _, hosts = _red(**opciones)
    srv = hosts["10.0.0.10"]
    res = t.desde(r, srv, t.tcp(srv, {"mac": "x", "ip": ip}, dst_mac=GW))
    assert res["entregado"] == {"otro-lado": 1}, res
    assert not res["inunda"] and not res["cortado"] and not res["ttl_agotado"]
    final = res["paquetes"]["otro-lado"][0]
    assert final["eth_dst"].upper() == SIGUIENTE_SALTO and final["eth_src"].upper() == GW and final["ip_dst"] == ip and final["ttl"] < 64


def test_la_respuesta_al_escaneo_del_atacante_ya_no_se_descarta_en_el_borde_que_era_el_fallo_original():
    r, _, hosts = _red(nucleo=NUCLEO)
    srv = hosts["10.0.0.10"]
    res = t.desde(r, srv, t.tcp(srv, _externo("203.0.113.103"), dst_mac=GW))
    assert res["entregado"] == {"otro-lado": 1}
    r2, _, hosts2 = _red(quitar_salida=True, nucleo=NUCLEO)
    assert t.desde(r2, srv, t.tcp(srv, _externo("203.0.113.103"), dst_mac=GW))["entregado"] == {}  # sin la ruta por defecto, el mismo paquete muere


@pytest.mark.parametrize("opciones", [{}, {"nucleo": NUCLEO}])
def test_el_atacante_sigue_llegando_al_server(opciones):
    r, _, hosts = _red(**opciones)
    srv = hosts["10.0.0.10"]
    ata = {"mac": "fa:16:3e:eb:ea:4c", "ip": "203.0.113.103", "device": BORDE, "port": 1}
    res = t.desde(r, ata, t.tcp(ata, srv, dst_mac=GW))
    assert res["entregado"] == {"10.0.0.10": 1} and not res["inunda"] and not res["cortado"]


@pytest.mark.parametrize("opciones", [{}, {"nucleo": NUCLEO}])
@pytest.mark.parametrize("ip", PRIVADAS_SIN_RUTA)
def test_una_ip_privada_sin_ruta_se_descarta_y_nunca_sale_al_exterior(ip, opciones):
    r, _, hosts = _red(**opciones)
    srv = hosts["10.0.0.10"]
    res = t.desde(r, srv, t.tcp(srv, {"mac": "x", "ip": ip}, dst_mac=GW))
    assert res["entregado"] == {} and not res["inunda"] and not res["cortado"], res


def test_el_bloqueo_tambien_vale_si_el_paquete_llega_al_borde_desde_el_exterior():
    r, _, _ = _red(nucleo=NUCLEO)
    ata = {"mac": "fa:16:3e:eb:ea:4c", "ip": "203.0.113.103", "device": BORDE, "port": 1}
    for ip in PRIVADAS_SIN_RUTA:
        res = t.desde(r, ata, t.tcp(ata, {"mac": "x", "ip": ip}, dst_mac=GW))
        assert res["entregado"] == {}, ip


def test_se_puede_cambiar_la_lista_de_prefijos_que_no_salen():
    datos = _json_plan()
    datos["salida"]["bloquear"] = ["10.0.0.0/8"]
    grafo, _, hosts = _datos()
    plan = l3.cargar_plan(datos, nombres=pa.NOMBRES)
    r, _ = t.red(grafo=grafo, hosts=hosts, plan=plan, pref=pa.PREFERIDOS)
    r["hosts"][(BORDE, 1)] = "otro-lado"
    srv = {h["ip"]: h for h in hosts}["10.0.0.10"]
    assert t.desde(r, srv, t.tcp(srv, {"mac": "x", "ip": "192.168.1.1"}, dst_mac=GW))["entregado"] == {"otro-lado": 1}   # ya no es privada para este plan
    assert t.desde(r, srv, t.tcp(srv, {"mac": "x", "ip": "10.0.9.9"}, dst_mac=GW))["entregado"] == {}


# ---------- coherencia con el vigilante ----------
def test_el_switch_de_salida_tiene_que_ser_del_nucleo():
    grafo, plan, hosts = _datos()
    with pytest.raises(l3.PlanInvalido):
        l3.flujos_capa3(plan, grafo, hosts, pa.PREFERIDOS, nucleo=[CORE1, CORE2])


def test_el_vigilante_conserva_la_salida_si_el_borde_esta_vivo_y_la_quita_si_no():
    grafo, plan, hosts = _datos()
    assert rc.plan_vivo(plan, grafo).salida is plan.salida
    sin_borde = {d: [(nb, p) for nb, p in v if nb != BORDE] for d, v in grafo.items() if d != BORDE}
    vivo = rc.plan_vivo(plan, sin_borde)
    assert vivo is not None and vivo.salida is None
    deseados = rc.flujos_deseados_capa3(sin_borde, hosts, plan, pa.PREFERIDOS, NUCLEO)                 # un borde caído no impide calcular lo demás
    assert deseados and not [f for f in deseados if f["priority"] in (l3.PRIO_DEFECTO, l3.PRIO_BLOQUEO)]


def test_la_salida_no_cambia_el_resto_de_los_flujos():
    _, con, _ = _red(nucleo=NUCLEO)
    _, sin, _ = _red(quitar_salida=True, nucleo=NUCLEO)
    clave = lambda f: json.dumps(f, sort_keys=True)                                                     # noqa: E731
    solo_salida = [f for f in con if f["priority"] in (l3.PRIO_DEFECTO, l3.PRIO_BLOQUEO)]
    assert sorted(map(clave, [f for f in con if f not in solo_salida])) == sorted(map(clave, sin))
    assert copy.deepcopy(con) is not con
