import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "R1", "shared", "policy_mapper"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "policy"))
import portal_auth  # noqa: E402
import server  # noqa: E402

POL = {"portal": {"grupos": {"profesor": "docente", "invitado": "invitado"}}}


def test_el_rol_sale_del_primer_grupo_conocido():
    assert portal_auth.rol_de_grupos(POL, ["otros", "invitado"]) == "invitado"
    assert portal_auth.rol_de_grupos(POL, ["otros"]) is None and portal_auth.rol_de_grupos(POL, []) is None


def test_clave_vacia_se_rechaza_sin_consultar_ldap():
    assert portal_auth.autenticar_ldap("guest01", "") is None and portal_auth.autenticar_ldap("", "x") is None


def test_el_portal_no_es_una_redireccion_abierta():
    assert server.destino_seguro("http://10.0.0.40:8003/x") == "http://10.0.0.40:8003/x"
    assert server.destino_seguro("http://evil.example.com/") == server.DESTINO_POR_DEFECTO
    assert server.destino_seguro("javascript:alert(1)") == server.DESTINO_POR_DEFECTO
    assert server.destino_seguro(None) == server.DESTINO_POR_DEFECTO


def test_la_pagina_escapa_los_datos_del_usuario():
    assert "<script>" not in server.formulario("http://10.0.0.40:8003/", "<script>alert(1)</script>")


# ---- ascenso: un equipo con sesion 802.1X que admite subir de rol
EQUIPO = {"usuario": "alumno01@example.org", "rol": "Alumno", "metodo": "802.1X", "ascenso_a": ["docente"], "base": None}
DESTINO = "http://10.0.0.40:8003/"


def test_un_equipo_que_admite_ascenso_ofrece_el_formulario_de_subir_de_rol():
    assert server.ascenso_disponible(EQUIPO) == ["docente"]
    pag = server.pagina_ascenso(EQUIPO, DESTINO)
    assert 'action="/login"' in pag and 'name="usuario"' in pag and 'type="password"' in pag and "Subir de rol" in pag
    assert "Docente" in pag and "Continuar como Alumno" in pag


def test_un_equipo_sin_ascenso_no_ofrece_subir_de_rol():
    assert server.ascenso_disponible({**EQUIPO, "ascenso_a": []}) == []
    assert server.ascenso_disponible({"usuario": "x", "rol": "Alumno", "metodo": "Portal"}) == []


def test_la_pagina_de_ascenso_escapa_los_datos_y_el_error():
    pag = server.pagina_ascenso({**EQUIPO, "usuario": "<b>pc</b>"}, DESTINO, "<script>alert(1)</script>")
    assert "<script>" not in pag and "<b>pc</b>" not in pag


def test_la_sesion_de_un_ascenso_avisa_que_el_equipo_vuelve_a_su_rol_base():
    sesion = {"usuario": "profesor01", "rol": "Docente", "metodo": "Portal", "base": {"usuario": "alumno01@example.org", "rol": "Alumno"}}
    pag = server.sesion_activa(sesion, DESTINO)
    assert "vuelve al rol" in pag and "Alumno" in pag and 'action="/logout"' in pag
    assert "vuelve al rol" not in server.sesion_activa({"usuario": "guest01", "rol": "Invitado", "metodo": "Portal"}, DESTINO)
