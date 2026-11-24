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


# ---- HTTPS
def _certificado(tmp_path):
    import shutil
    import subprocess
    if shutil.which("openssl") is None:
        import pytest
        pytest.skip("sin openssl")
    cert, clave = tmp_path / "portal.crt", tmp_path / "portal.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(clave), "-out", str(cert),
                    "-subj", "/CN=portal.prueba", "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
    return str(cert), str(clave)


def test_el_portal_habla_https_y_no_http_cuando_hay_certificado(tmp_path, monkeypatch):
    import http.client
    import ssl
    import threading
    cert, clave = _certificado(tmp_path)
    srv = server.crear_servidor("127.0.0.1", 0, cert, clave)
    puerto = srv.server_address[1]
    monkeypatch.setattr(server, "PUERTO_PORTAL", puerto)       # es el puerto del portal, no el del destino desviado
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctx = ssl.create_default_context(cafile=cert)          # el certificado fijado en el cliente (el del portal)
        con = http.client.HTTPSConnection("127.0.0.1", puerto, context=ctx, timeout=5)
        con.request("GET", "/")
        r = con.getresponse()
        assert r.status == 200 and b"<form" in r.read()
        # sin fijar el certificado, un cliente que verifica rechaza al portal (un portal falso no podria presentar este)
        otro = http.client.HTTPSConnection("127.0.0.1", puerto, context=ssl.create_default_context(), timeout=5)
        try:
            otro.request("GET", "/")
            assert False, "debio fallar la verificacion"
        except ssl.SSLError:
            pass
        # y en claro no responde con la pagina (la contrasena no viaja sin cifrar)
        plano = http.client.HTTPConnection("127.0.0.1", puerto, timeout=3)
        try:
            plano.request("GET", "/")
            assert b"<form" not in plano.getresponse().read()
        except (http.client.HTTPException, OSError):
            pass
    finally:
        srv.shutdown()


def test_sin_certificado_el_portal_sigue_en_http_como_en_el_laboratorio():
    import threading
    srv = server.crear_servidor("127.0.0.1", 0)
    assert type(srv).__name__ == "ThreadingHTTPServer"
    srv.server_close()
