"""Cuántos flujos genera cada elemento (docs/CAPACIDAD_FLUJOS.md). Si alguna fórmula del documento deja de cumplirse tras un cambio en policy_tables.py o capa3.py, esta prueba lo dice."""
import copy
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
import benchmark_capa3 as b  # noqa: E402
import capa3 as l3  # noqa: E402
import policy_tables as pt  # noqa: E402

ACC = "of:0000e6925d87354b"


def base():
    p = pt.cargar_politica(os.path.join(RAIZ, "policy.yaml"))
    p["reenvio"], p["trusted"], p["sin_usuarios"] = "proactivo", {}, []
    return p


def politica(roles, servicios, permitidos=None):
    p = base()
    p["roles"] = {f"rol{i}": {"matriz": f"m{i}", "tabla": 10 + i if 10 + i != 30 else 31} for i in range(roles)}
    p["services"] = {f"srv{j}": {"ip": f"10.1.{j // 250}.{j % 250 + 1}", "tcp": 8000 + j} for j in range(servicios)}
    p["rules"] = [{"src": f"m{i}", "dst": f"srv{j}", "allow": True} for i in range(roles) for j in range(permitidos if permitidos is not None else servicios)]
    return p


def n_r2(p):
    return len(pt.flujos_dispositivo(p, ACC))


FIJOS = n_r2(politica(1, 0)) - 3          # lo que R2 pone en un acceso aunque no haya roles con servicios (descubrimiento, DHCP, cuarentena...)


@pytest.mark.parametrize("roles,servicios", [(1, 0), (1, 10), (4, 8), (10, 20), (30, 15), (58, 10)])
def test_las_tablas_de_rol_de_un_acceso_cuestan_roles_por_3_mas_los_servicios_permitidos(roles, servicios):
    assert n_r2(politica(roles, servicios)) == FIJOS + roles * (3 + servicios)


def test_cada_rol_lleva_un_arp_el_portal_y_un_descarte_mas_un_flujo_por_servicio_permitido():
    p = politica(1, 5)
    flujos = pt.flujos_tabla_rol(p, ACC, "rol0", p["roles"]["rol0"]["tabla"])
    assert len(flujos) == 3 + 5 and sum(1 for f in flujos if f["priority"] == 0) == 1


def test_un_servicio_denegado_sin_regla_o_sin_host_en_el_catalogo_no_cuesta_ningun_flujo():
    solo_10 = n_r2(politica(4, 20, permitidos=10))
    assert n_r2(politica(4, 10)) == solo_10                                   # los 10 servicios restantes del catálogo, sin regla de permiso: 0 flujos
    p = politica(4, 10)
    p["rules"] += [{"src": f"m{i}", "dst": "srv_denegado", "allow": False} for i in range(4)] + [{"src": f"m{i}", "dst": "internet", "allow": True} for i in range(4)]
    assert n_r2(p) == solo_10                                                 # denegar, o permitir un servicio sin host (como `internet`), tampoco añade flujos
    permitido = politica(4, 10)
    permitido["rules"] = [r for r in permitido["rules"] if not (r["src"] == "m0" and r["dst"] == "srv0")]
    assert n_r2(permitido) == solo_10 - 1                                     # quitar un permiso quita un flujo en ese rol


def test_los_cores_sin_usuarios_no_llevan_tablas_de_rol():
    p = politica(10, 20)
    p["sin_usuarios"] = [ACC]
    assert n_r2(p) < FIJOS + 10                                               # solo lo del descubrimiento: nada de roles


def test_en_capa_3_un_acceso_lleva_2_por_host_y_2_por_puerto_y_no_crece_con_el_numero_de_accesos():
    def acceso(accesos, h, p):
        plan, grafo, hs = b.plan_sintetico(accesos, h, p)
        return l3.contar_por_switch(l3.flujos_capa3(plan, grafo, hs, b.CORES, nucleo=b.CORES, arp_gateway=False))["acc0"]
    assert acceso(2, 20, 48) - acceso(2, 10, 48) == 20                        # 2 por host
    assert acceso(2, 0, 3) - acceso(2, 0, 2) == 2                             # 2 por puerto de acceso
    assert acceso(2, 48, 48) == acceso(50, 48, 48) == acceso(200, 48, 48)     # más switches de acceso no engordan a los demás accesos


def test_en_capa_3_el_nucleo_lleva_una_ruta_por_subred_y_ninguna_por_host_de_otro_switch():
    def core(accesos, h):
        plan, grafo, hs = b.plan_sintetico(accesos, h, 48)
        return l3.contar_por_switch(l3.flujos_capa3(plan, grafo, hs, b.CORES, nucleo=b.CORES, arp_gateway=False))["core1"]
    assert [core(a, 0) for a in (2, 10, 50, 100)] == [3, 11, 51, 101]         # accesos + 1
    assert core(50, 0) == core(50, 48)                                        # los hosts de los accesos no engordan al núcleo
