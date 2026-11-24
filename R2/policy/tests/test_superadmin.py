"""Rol `superadmin` en R2 (docs/DISENO_EXCEPCIONES_POR_SESION.md 4.7, requisito P11): tabla 45, panel solo para él y tubería completa en el simulador."""
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import policy_tables as pt  # noqa: E402
from test_capa3_con_politica_r2 import IPS, desde, paquete, red  # noqa: E402
from test_fase_e import A1, MAC, politica  # noqa: E402

PUERTO_PANEL = 8480


def repo():
    return pt.cargar_politica(os.path.join(os.path.dirname(AQUI), "policy.yaml"))


# ---------- la política ----------
def test_el_superadmin_tiene_su_propia_tabla_y_su_propia_matriz():
    p = repo()
    info = p["roles"]["superadmin"]
    assert info == {"matriz": "superadmin", "tabla": 45}
    tablas = [int(d["tabla"]) for d in p["roles"].values()]
    assert len(set(tablas)) == len(tablas)                                    # ninguna tabla repetida
    assert 45 < pt.TABLA_REENVIO                                             # un goto solo va hacia delante: tiene que ser menor que la 60
    assert 45 not in (30, pt.TABLA_CUARENTENA)                                # la 30 es de IoT/MAB y la 99 de cuarentena
    assert info["matriz"] not in {d["matriz"] for r, d in p["roles"].items() if r != "superadmin"}   # no se comparte la matriz del administrador


def test_solo_el_superadmin_llega_al_panel():
    p = repo()
    assert p["services"]["admin_panel"]["tcp"] == PUERTO_PANEL
    for rol in p["roles"]:
        esperado = "PERMIT" if rol == "superadmin" else "DENY"
        assert pt.evaluar_acceso(p, rol, "admin_panel")["decision"] == esperado, rol


def test_el_superadmin_conserva_todo_lo_del_administrador():
    p = repo()
    admin = {s for s, ok in pt.servicios_permitidos(p, "administrator").items() if ok and s != "admin_panel"}
    super_ = {s for s, ok in pt.servicios_permitidos(p, "superadmin").items() if ok}
    assert admin and admin <= super_ and super_ - admin == {"admin_panel"}


def test_el_administrador_de_soporte_no_hereda_el_panel():
    assert not pt.servicios_permitidos(repo(), "administrator").get("admin_panel")


def test_la_tabla_45_tiene_el_panel_y_la_40_no():
    p = politica()
    def puertos(rol, tabla):
        return {f["selector"]["criteria"][4]["tcpPort"] for f in pt.flujos_tabla_rol(p, A1, rol, tabla)
                if len(f["selector"]["criteria"]) == 5 and f["priority"] == pt.PRIO_ROL}
    assert PUERTO_PANEL in puertos("superadmin", 45) and PUERTO_PANEL not in puertos("soporte", 40)
    assert all(f["tableId"] == 45 and f["selector"]["criteria"][0] == {"type": "VLAN_VID", "vlanId": 45}
               for f in pt.flujos_tabla_rol(p, A1, "superadmin", 45) if f["selector"]["criteria"])
    assert pt.flujos_tabla_rol(p, A1, "superadmin", 45)[-1]["priority"] == 0           # y termina en DROP, como las demás tablas de rol


def test_el_switch_de_acceso_instala_la_tabla_45_con_los_demas_roles():
    tablas = {f["tableId"] for f in pt.flujos_dispositivo(politica(), A1)}
    assert {10, 20, 40, 45, 50, 99} <= tablas


# ---------- tubería completa: tabla 0 -> tabla del rol -> tabla 60 (capa 3) ----------
def llega(r, puerto):
    res = desde(r, "alumno", paquete(MAC["alumno"], IPS["alumno"], IPS["servidor"], puerto))
    assert not res["inunda"] and not res["cortado"]
    return res["entregado"] == {"srv1": 1}


@pytest.mark.parametrize("vlan, rol", [(10, "docente"), (20, "alumno"), (40, "soporte"), (50, "invitado")])
def test_ningun_otro_rol_llega_al_panel(vlan, rol):
    assert not llega(red(vlan), PUERTO_PANEL), rol


def test_el_superadmin_llega_al_panel_y_a_lo_del_administrador():
    r = red(45)
    assert llega(r, PUERTO_PANEL) and llega(r, 8105) and llega(r, 8102) and llega(r, 8001)


def test_el_superadmin_no_abre_puertos_que_nadie_tiene():
    r = red(45)
    assert not llega(r, 22) and not llega(r, 8100) and not llega(r, 8481)


def test_el_administrador_sigue_llegando_a_lo_suyo_sin_el_panel():
    r = red(40)
    assert llega(r, 8105) and llega(r, 8102) and not llega(r, PUERTO_PANEL)
