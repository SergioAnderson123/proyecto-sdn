import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared", "policy_mapper"))
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
