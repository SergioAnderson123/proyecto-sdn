"""Lab 7 (TEL354): pruebas unitarias comunes de R2 (restricción de acceso a recursos privilegiados) sobre R2/policy/policy_tables.py.

Numeración X.Y.Z del informe: módulo 2 = autorización (M2); sección 2.N = prueba R2-0N; Z = caso.
  R2-01 evaluar_acceso(rol, recurso)   -> 2.1.x   R2-02 rol o recurso desconocido -> 2.2.x   R2-03 traducción de política a OpenFlow -> 2.3.x
Corren sin ONOS ni red: la política se lee de policy.yaml y los flujos se generan en memoria.
"""
import copy

import pytest

import policy_tables as pt

DPID = "of:0000000000000001"
RECURSO_CRITICO = "network_management"          # la "consola SDN": solo el administrador (soporte) la alcanza


@pytest.fixture
def pol():
    return pt.cargar_politica()


def _destinos(flujos):
    """{(ip, puerto)} de los flujos que dejan pasar TCP a un servicio."""
    res = set()
    for f in flujos:
        c = {x["type"]: x for x in f["selector"]["criteria"]}
        if "IPV4_DST" in c and "TCP_DST" in c and f["treatment"]["instructions"]:
            res.add((c["IPV4_DST"]["ip"].split("/")[0], c["TCP_DST"]["tcpPort"]))
    return res


# =====================================================================================================
# R2-01 (2.1.x) evaluar_acceso: el Administrador entra a la consola SDN y el Docente no.
# =====================================================================================================
def test_R2_01_2_1_1_el_administrador_accede_a_la_consola_sdn(pol):
    r = pt.evaluar_acceso(pol, "soporte", RECURSO_CRITICO)           # soporte = administrator en la matriz
    assert r["decision"] == "PERMIT" and r["recurso"] == RECURSO_CRITICO and r["motivo"]


def test_R2_01_2_1_2_el_docente_no_accede_a_la_consola_sdn_y_se_indica_el_motivo(pol):
    r = pt.evaluar_acceso(pol, "docente", RECURSO_CRITICO)           # docente = professor
    assert r["decision"] == "DENY" and r["recurso"] == RECURSO_CRITICO
    assert "permiso" in r["motivo"]                                  # rol sin permiso sobre el recurso


def test_R2_01_2_1_3_la_denegacion_no_instala_ninguna_regla_que_permita_el_acceso(pol):
    ip, puerto = pol["services"][RECURSO_CRITICO]["ip"], pol["services"][RECURSO_CRITICO]["tcp"]
    flujos = pt.flujos_tabla_rol(pol, DPID, "docente", pol["roles"]["docente"]["tabla"])
    assert (ip, puerto) not in _destinos(flujos)


@pytest.mark.parametrize("rol_ldap,rol_matriz", [("docente", "professor"), ("alumno", "student"), ("soporte", "administrator"), ("invitado", "guest")])
def test_R2_01_2_1_4_acepta_el_rol_del_ldap_o_el_de_la_matriz(pol, rol_ldap, rol_matriz):
    assert pt.evaluar_acceso(pol, rol_ldap, "grade_system")["decision"] == pt.evaluar_acceso(pol, rol_matriz, "grade_system")["decision"]


@pytest.mark.parametrize("rol,recurso,esperado", [
    ("professor", "grade_system", "PERMIT"), ("student", "grade_system", "DENY"),       # notas: el alumno no las ve
    ("student", "virtual_library", "PERMIT"), ("guest", "virtual_library", "DENY"),
    ("guest", "university_portal", "PERMIT"),                                           # el invitado solo ve el portal de la universidad
    ("professor", "student_records", "DENY"), ("administrator", "student_records", "PERMIT"),
])
def test_R2_01_2_1_5_matriz_rol_recurso(pol, rol, recurso, esperado):
    assert pt.evaluar_acceso(pol, rol, recurso)["decision"] == esperado


# =====================================================================================================
# R2-02 (2.2.x) Rol o recurso inexistente: se deniega por defecto.
# =====================================================================================================
@pytest.mark.parametrize("rol", ["Invitado", "visitante", "", "ADMINISTRATOR", None])
def test_R2_02_2_2_1_un_rol_que_no_existe_se_deniega(pol, rol):
    r = pt.evaluar_acceso(pol, rol, "virtual_library")
    assert r["decision"] == "DENY" and "rol no registrado" in r["motivo"]


@pytest.mark.parametrize("recurso", ["no_existe", "", "internet", "../etc/passwd", None])
def test_R2_02_2_2_2_un_recurso_fuera_del_inventario_se_deniega_incluso_al_administrador(pol, recurso):
    # `internet` figura en la matriz pero no tiene host en `services`: no se instala, así que se deniega (cierra por defecto).
    r = pt.evaluar_acceso(pol, "soporte", recurso)
    assert r["decision"] == "DENY" and "inventario" in r["motivo"]


def test_R2_02_2_2_3_servicios_permitidos_de_un_rol_desconocido_es_vacio(pol):
    assert pt.servicios_permitidos(pol, "rol-inexistente") == {}


def test_R2_02_2_2_4_la_tabla_de_un_rol_termina_en_un_drop_explicito(pol):
    f = pt.flujos_tabla_rol(pol, DPID, "invitado", pol["roles"]["invitado"]["tabla"])[-1]
    assert f["priority"] == 0 and f["treatment"]["instructions"] == []         # sin instrucciones = DROP


def test_R2_02_2_2_5_un_recurso_sin_regla_para_el_rol_se_deniega(pol):
    p = copy.deepcopy(pol)
    p["rules"] = [r for r in p["rules"] if not (r["src"] == "student" and r["dst"] == "print_server")]    # se borra su regla
    r = pt.evaluar_acceso(p, "student", "print_server")
    assert r["decision"] == "DENY" and "sin regla" in r["motivo"]


# =====================================================================================================
# R2-03 (2.3.x) Traducción de la política a reglas OpenFlow; si dos políticas chocan, gana la que deniega.
# =====================================================================================================
def test_R2_03_2_3_1_cada_servicio_permitido_genera_una_regla_con_match_accion_y_prioridad(pol):
    flujos = pt.flujos_tabla_rol(pol, DPID, "docente", 10)
    grade = pol["services"]["grade_system"]
    regla = next(f for f in flujos if (grade["ip"] + "/32") in str(f["selector"]) and f"{grade['tcp']}" in str(f["selector"]))
    assert regla["priority"] == pt.PRIO_ROL and regla["tableId"] == 10 and regla["deviceId"] == DPID
    assert {c["type"] for c in regla["selector"]["criteria"]} >= {"VLAN_VID", "ETH_TYPE", "IP_PROTO", "IPV4_DST", "TCP_DST"}
    assert regla["treatment"]["instructions"]                                   # acción: entregar (no DROP)


def test_R2_03_2_3_2_solo_se_instalan_reglas_para_lo_permitido(pol):
    esperados = {(pol["services"][s]["ip"], pol["services"][s]["tcp"])
                 for s, ok in pt.servicios_permitidos(pol, "professor").items() if ok and s in pol["services"]}
    assert esperados <= _destinos(pt.flujos_tabla_rol(pol, DPID, "docente", 10))
    assert not (_destinos(pt.flujos_tabla_rol(pol, DPID, "docente", 10)) - esperados - {(pol["portal"]["ip"], pol["portal"]["puerto"])})


@pytest.mark.parametrize("orden", [(True, False), (False, True)])
def test_R2_03_2_3_3_si_una_politica_permite_y_otra_deniega_el_mismo_recurso_prevalece_la_que_deniega(pol, orden):
    p = copy.deepcopy(pol)
    p["rules"] = [r for r in p["rules"] if not (r["src"] == "student" and r["dst"] == "print_server")]
    p["rules"] += [{"src": "student", "dst": "print_server", "allow": ok} for ok in orden]     # en los dos órdenes posibles
    assert pt.servicios_permitidos(p, "student")["print_server"] is False
    srv = p["services"]["print_server"]
    assert (srv["ip"], srv["tcp"]) not in _destinos(pt.flujos_tabla_rol(p, DPID, "alumno", 20))
    assert pt.evaluar_acceso(p, "student", "print_server")["decision"] == "DENY"


def test_R2_03_2_3_4_cada_rol_usa_su_propia_tabla_y_su_propia_vlan(pol):
    for rol, info in pol["roles"].items():
        flujos = pt.flujos_tabla_rol(pol, DPID, rol, info["tabla"])
        assert all(f["tableId"] == info["tabla"] for f in flujos)
        assert all({"type": "VLAN_VID", "vlanId": info["tabla"]} in f["selector"]["criteria"] for f in flujos[:-1])


def test_R2_03_2_3_5_las_reglas_generadas_son_permanentes_y_no_se_duplican(pol):
    flujos = pt.flujos_tabla_rol(pol, DPID, "soporte", 40)
    assert all(f["isPermanent"] and f["timeout"] == 0 for f in flujos)
    claves = [str(sorted(map(str, f["selector"]["criteria"]))) + str(f["priority"]) for f in flujos]
    assert len(claves) == len(set(claves))
