"""Los planes de direccionamiento (docs/PLAN_DIRECCIONAMIENTO.md): el del slice de VNRT sobre la topología real y la plantilla de pabellones."""
import ipaddress
import itertools
import json
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(AQUI)))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(os.path.dirname(AQUI), "planes"))
import caminos as c  # noqa: E402
import capa3 as l3  # noqa: E402
import generar_plan_pabellones as gp  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
from plan_arboles import NOMBRES, PREFERIDOS  # noqa: E402
from test_capa3 import arp_peticion, desde, red, tcp  # noqa: E402
from test_generar_politica_local import LINKS as LINKS_REALES  # noqa: E402

PLAN_A = json.load(open(os.path.join(os.path.dirname(AQUI), "planes", "vnrt_topologia14.json"), encoding="utf-8"))
OCUPADAS = [ipaddress.ip_network("192.168.201.0/24"), ipaddress.ip_network("10.50.0.0/24")]       # gestión y red interna de Docker
POR = {v: k for k, v in NOMBRES.items()}
G = c.grafo_desde_links(LINKS_REALES)
SRV = {"mac": "fa:16:3e:92:d3:e6", "ip": "10.0.0.10", "device": POR["core-1"], "port": 5}
H1 = {"mac": "fa:16:3e:b6:96:08", "ip": "10.0.1.201", "device": POR["acc-1"], "port": 1}
H2 = {"mac": "fa:16:3e:71:08:66", "ip": "10.0.2.202", "device": POR["acc-2"], "port": 1}
JHON = {"mac": "fa:16:3e:00:00:03", "ip": "203.0.113.103", "device": POR["borde"], "port": 1}
HOSTS = [SRV, H1, H2, JHON]
NOMBRE = {(h["device"], h["port"]): h["ip"] for h in HOSTS}


def test_el_plan_del_slice_carga_con_los_nombres_reales_y_no_pisa_gestion_ni_docker():
    plan = l3.cargar_plan(PLAN_A, nombres=NOMBRES)
    assert [s.nombre for s in plan.subredes] == ["servicios", "acc-1", "acc-2", "externa"]
    for s in plan.subredes:
        assert not any(s.prefijo.overlaps(o) for o in OCUPADAS), s
    assert plan.subredes[0].dispositivo == POR["core-1"] and plan.subredes[3].dispositivo == POR["borde"]


def test_el_campus_agregado_es_un_16_que_contiene_servicios_y_accesos_pero_no_la_externa():
    plan = l3.cargar_plan(PLAN_A, nombres=NOMBRES)
    home = ipaddress.ip_network("10.0.0.0/16")
    dentro = {s.nombre: s.prefijo.subnet_of(home) for s in plan.subredes}
    assert dentro == {"servicios": True, "acc-1": True, "acc-2": True, "externa": False}


def test_el_server_y_el_portal_conservan_su_ip_y_los_hosts_de_prueba_su_sufijo():
    plan = l3.cargar_plan(PLAN_A, nombres=NOMBRES)
    assert plan.subred_de("10.0.0.10").nombre == "servicios"
    assert plan.subred_de("10.0.1.201").nombre == "acc-1" and plan.subred_de("10.0.2.202").nombre == "acc-2"
    validos, rechazados = l3.hosts_validos(plan, HOSTS)
    assert len(validos) == 4 and rechazados == []


@pytest.mark.parametrize("opciones", [{}, {"nucleo": [POR["core-1"], POR["core-2"], POR["borde"]]}])
def test_en_la_topologia_real_todos_los_pares_se_comunican_sin_inundar(opciones):
    plan = l3.cargar_plan(PLAN_A, nombres=NOMBRES)
    r, flujos = red(grafo=G, hosts=HOSTS, plan=plan, pref=PREFERIDOS, **opciones)
    assert c.flujos_que_inundan(flujos) == []
    for origen, destino in itertools.permutations(HOSTS, 2):
        res = desde(r, origen, tcp(origen, destino))
        assert res["entregado"] == {NOMBRE[(destino["device"], destino["port"])]: 1}, (origen["ip"], destino["ip"])
        assert not res["inunda"] and not res["cortado"] and not res["ttl_agotado"]


def test_en_la_topologia_real_el_arp_del_gateway_se_contesta_en_el_acceso_y_ningun_arp_sale_del_switch():
    plan = l3.cargar_plan(PLAN_A, nombres=NOMBRES)
    r, _ = red(grafo=G, hosts=HOSTS, plan=plan, pref=PREFERIDOS)
    for h, gw in ((H1, "10.0.1.254"), (H2, "10.0.2.254"), (SRV, "10.0.0.254"), (JHON, "203.0.113.254")):
        res = desde(r, h, arp_peticion(h, gw))
        assert res["entregado"] == {h["ip"]: 1} and set(res["llegadas"]) == {h["device"]}
        assert res["paquetes"][h["ip"]][0]["arp_spa"] == gw
    for h in HOSTS:                                                                                   # y tampoco el ARP de cualquier otra dirección
        assert set(desde(r, h, arp_peticion(h, "10.0.9.9"))["llegadas"]) == {h["device"]}


# ---------- la plantilla de pabellones ----------
def test_la_plantilla_de_pabellones_carga_sin_solapes_y_cada_pabellon_agrega_a_su_16():
    filas = gp.construir()
    plan = l3.cargar_plan(gp.a_json(filas))
    assert len(plan.subredes) == 33 and len([f for f in filas if f["pabellon"] != "—"]) == 31
    for pab, d in gp.PABELLONES.items():
        bloque = ipaddress.ip_network(f"10.{d['id']}.0.0/16")
        propias = [s for s, f in zip(plan.subredes, filas) if f["pabellon"] == pab]
        assert len(propias) == sum(d["switches"]) and all(s.prefijo.subnet_of(bloque) for s in propias)
    for s in plan.subredes:
        assert not any(s.prefijo.overlaps(o) for o in OCUPADAS)


def test_un_pabellon_con_mas_de_254_switches_de_acceso_se_rechaza():
    with pytest.raises(ValueError):
        gp.construir({"X": {"id": 90, "switches": [3] * 100}}, [])


def test_la_zona_generada_del_documento_esta_al_dia():
    doc = open(os.path.join(RAIZ, "docs", "PLAN_DIRECCIONAMIENTO.md"), encoding="utf-8").read()
    zona = doc.split(gp.INICIO, 1)[1].split(gp.FIN, 1)[0].strip()
    assert zona == gp.tablas(gp.construir()).strip(), "regenera con: python3 R2/policy/planes/generar_plan_pabellones.py --doc docs/PLAN_DIRECCIONAMIENTO.md"


def test_los_pisos_dados_por_el_equipo_estan_en_la_plantilla():
    """V 3 pisos (más switches en el 3.º), CIA 4, A 8 (pocos equipos), O 5."""
    pisos = {pab: len(d["switches"]) for pab, d in gp.PABELLONES.items()}
    assert pisos == {"V": 3, "CIA": 4, "A": 8, "O": 5}
    assert gp.PABELLONES["V"]["switches"][2] > max(gp.PABELLONES["V"]["switches"][:2])          # el 3.º de V tiene más switches
    assert max(gp.PABELLONES["A"]["switches"]) == 1                                              # A tiene pocos equipos
