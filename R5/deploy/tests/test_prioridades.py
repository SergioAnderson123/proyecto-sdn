"""Invariantes del mapa de prioridades entre R1, R2 y R5. Los valores se leen del código real, no de copias.

Ejecutar desde la raíz del repositorio:  python -m pytest R5/deploy/tests -q
"""
import os
import re
import sys

import pytest
import yaml

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
sys.path.insert(0, os.path.join(RAIZ, "R2", "policy"))
import policy_tables as pt  # noqa: E402


def leer(ruta):
    with open(os.path.join(RAIZ, ruta), encoding="utf8") as f:
        return f.read()


@pytest.fixture(scope="module")
def mapa():
    return yaml.safe_load(leer("R5/deploy/prioridades.yaml"))


def por_defecto(ruta, variable):
    m = re.search(rf'getenv\("{variable}",\s*"(\d+)"\)', leer(ruta))
    assert m, f"{variable} no tiene valor por defecto en {ruta}"
    return int(m.group(1))


# ---- el código coincide con el mapa ----
def test_r5_drop_y_espejo_coinciden_con_el_mapa(mapa):
    assert por_defecto("R5/codigo/integrador.py", "P_R5") == mapa["r5"]["drop"]
    assert por_defecto("R5/lab/espejo.py", "P_ESPEJO") == mapa["r5"]["espejo"]


def test_r2_sesion_confianza_y_tablas_coinciden_con_el_mapa(mapa):
    assert pt.PRIO_SESION == mapa["r2"]["sesion"] and pt.PRIO_CONFIANZA == mapa["r2"]["confianza"]
    assert pt.PRIO_ROL == mapa["r2"]["tabla_rol"]
    roles = pt.cargar_politica()["roles"]
    assert {r: d["tabla"] for r, d in roles.items()} == mapa["r2"]["tablas"]


def test_portal_cautivo_coincide_con_el_mapa_y_cede_ante_la_sesion(mapa):
    assert pt.PRIO_PORTAL_ARP == mapa["r2"]["portal_arp"] and pt.PRIO_PORTAL_RETORNO == mapa["r2"]["portal_retorno"]
    assert pt.PRIO_PORTAL == mapa["r2"]["portal"]
    assert mapa["r2"]["portal"] < mapa["r2"]["sesion"]            # con sesión, el flujo de R2 gana y el HTTP no se desvía
    assert mapa["r2"]["portal_retorno"] > mapa["r2"]["confianza"]  # la reescritura debe ganar al NORMAL de la troncal
    assert mapa["r2"]["portal_arp"] > mapa["r2"]["sesion"] > mapa["r1"]["base"]


def test_r1_drop_temporal_y_sesion_en_el_policy_mapper_coinciden_con_el_mapa(mapa):
    prios = set(int(x) for x in re.findall(r'"priority":\s*(\d+)', leer("R1/shared/policy_mapper/policy_mapper.py")))
    assert mapa["r1"]["drop_temporal"] in prios and mapa["r2"]["sesion"] in prios


# ---- invariantes de orden ----
def test_el_espejo_de_r5_queda_sobre_la_sesion_de_r2(mapa):
    # un espejo bajo la sesión (o bajo los puertos de confianza) no vería nunca esos paquetes
    assert mapa["r5"]["espejo"] > mapa["r2"]["sesion"] > mapa["r1"]["base"]


def test_el_drop_de_r5_prevalece_sobre_sesion_y_espejo_y_cede_ante_r1(mapa):
    assert mapa["r1"]["drop_temporal"] > mapa["r5"]["drop"] > mapa["r5"]["espejo"]


def test_el_eapol_de_un_puerto_con_sesion_gana_a_la_sesion_y_a_la_lista_negra(mapa):
    # si el EAPOL quedara bajo la sesion (40001), el logoff y la reautenticacion de un host autenticado se descartarian
    assert mapa["r1"]["eapol"] > mapa["r1"]["drop_temporal"] > mapa["r2"]["sesion"] > mapa["r1"]["base"]


def test_el_policy_mapper_instala_el_eapol_a_la_prioridad_del_mapa(mapa):
    m = re.search(r"P_EAPOL_PUERTO\s*=\s*(\d+)", leer("R1/shared/policy_mapper/policy_mapper.py"))
    assert m and int(m.group(1)) == mapa["r1"]["eapol"]


def test_r5_usa_solo_su_banda(mapa):
    assert all(41000 <= p <= 44999 for p in mapa["r5"].values())


def test_las_tablas_de_rol_no_compiten_en_la_tabla_0(mapa):
    # sus reglas viven en las tablas 10/20/40; si algún día estuvieran en la tabla 0, 30000 queda bajo todo lo demás
    assert mapa["r2"]["tabla_rol"] < mapa["r1"]["base"]
    assert 0 not in mapa["r2"]["tablas"].values() and 30 not in mapa["r2"]["tablas"].values()


def test_las_prioridades_clave_son_distintas_entre_requerimientos(mapa):
    # 40001 se repite a propósito en R2 (sesión y confianza: no coinciden en el mismo switch con la misma selección)
    r1, r5 = set(mapa["r1"].values()), set(mapa["r5"].values())
    assert not (r1 & r5) and mapa["r2"]["sesion"] not in r1 | r5


# ---- DHCP y cuarentena de R2 ----
def test_dhcp_y_cuarentena_coinciden_con_el_mapa(mapa):
    assert pt.PRIO_DHCP == mapa["r2"]["dhcp"] and pt.PRIO_CUARENTENA == mapa["r2"]["cuarentena"]
    assert pt.tabla_cuarentena(pt.cargar_politica()) == mapa["r2"]["tabla_cuarentena"]


def test_el_dhcp_gana_a_la_sesion_pero_cede_ante_la_lista_negra_y_el_drop_de_r5(mapa):
    # bajo la sesion (40001) un host con sesion no podria renovar su IP: la tabla de su rol descarta el UDP 67
    assert mapa["r2"]["dhcp"] > mapa["r2"]["sesion"] > mapa["r1"]["base"]
    assert mapa["r1"]["drop_temporal"] > mapa["r5"]["drop"] > mapa["r2"]["dhcp"]     # un host bloqueado tampoco obtiene IP


def test_la_cuarentena_es_lo_ultimo_de_la_tabla_0_y_su_tabla_es_nueva(mapa):
    # solo recibe lo que ninguna otra regla de la tabla 0 quiso (incluida la trampa MAB, 20000)
    assert 0 < mapa["r2"]["cuarentena"] < 20000 < mapa["r2"]["portal"]
    usadas = set(mapa["r2"]["tablas"].values()) | {0, 30}
    assert mapa["r2"]["tabla_cuarentena"] not in usadas
