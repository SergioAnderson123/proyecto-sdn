import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

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


# ---- clientes de otra subred (capa 3): el portal manda al mapper la IP de origen de la conexion, y la MAC solo si su ARP la tiene
class _MapperFalso:
    """Un Policy Mapper de mentira: apunta lo que recibe y contesta lo que se le diga."""
    def __init__(self):
        self.estado_pedidos, self.logins, self.logouts, self.tokens = [], [], [], []
        self.sesion = None                       # lo que devuelve /portal/estado
        self.login = (200, {"status": "ok", "usuario": "guest01", "rol": "Invitado", "tabla": 50})
        self.logout_respuestas = []              # una por cierre, en orden: (codigo, cuerpo) o un numero de segundos que el mapper tarda en contestar
        mapper = self

        class H(BaseHTTPRequestHandler):
            def _j(self, codigo, cuerpo):
                b = json.dumps(cuerpo).encode()
                try:
                    self.send_response(codigo)
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                except OSError:                  # el portal ya se rindió y cerró la conexión
                    pass

            def do_GET(self):
                mapper.estado_pedidos.append(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query, keep_blank_values=True)))
                self._j(200, {"sesion": mapper.sesion})

            def do_POST(self):
                cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mapper.tokens.append(self.headers.get("X-Portal-Token"))
                if self.path == "/portal/login":
                    mapper.logins.append(cuerpo)
                    return self._j(*mapper.login)
                mapper.logouts.append(cuerpo)
                respuesta = mapper.logout_respuestas.pop(0) if mapper.logout_respuestas else (200, {"status": "cerrada"})
                if isinstance(respuesta, (int, float)):          # tarda más de lo que el portal espera
                    time.sleep(respuesta)
                    respuesta = (200, {"status": "cerrada"})
                self._j(*respuesta)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def portal(monkeypatch):
    """El portal REAL en un puerto libre de 127.0.0.1 (el cliente es 127.0.0.1) frente a un mapper de mentira; sin ARP por defecto."""
    mapper = _MapperFalso()
    srv = server.crear_servidor("127.0.0.1", 0)
    puerto = srv.server_address[1]
    monkeypatch.setattr(server, "MAPPER", mapper.url)
    monkeypatch.setattr(server, "TOKEN", "t0ken")
    monkeypatch.setattr(server, "PUERTO_PORTAL", puerto)
    monkeypatch.setattr(server, "mac_de", lambda ip: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{puerto}"

    def pedir(ruta, datos=None):
        peticion = urllib.request.Request(base + ruta, data=None if datos is None else urllib.parse.urlencode(datos).encode(), method="GET" if datos is None else "POST")
        try:
            with urllib.request.urlopen(peticion, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
    yield mapper, pedir
    srv.shutdown()
    mapper.srv.shutdown()


def test_sin_arp_el_portal_pregunta_al_mapper_por_la_ip_con_la_mac_vacia_y_muestra_la_sesion(portal):
    mapper, pedir = portal
    mapper.sesion = {"usuario": "profesor01", "rol": "Docente", "metodo": "802.1X", "ascenso_a": [], "base": None}
    codigo, pagina = pedir("/")
    assert codigo == 200 and "profesor01" in pagina and 'action="/logout"' in pagina
    assert mapper.estado_pedidos == [{"mac": "", "ip": "127.0.0.1"}]


def test_sin_sesion_se_muestra_el_formulario(portal):
    mapper, pedir = portal
    codigo, pagina = pedir("/")
    assert codigo == 200 and 'action="/login"' in pagina and "profesor01" not in pagina


def test_el_login_manda_la_ip_y_el_token_y_la_mac_vacia_si_el_arp_no_la_tiene(portal):
    mapper, pedir = portal
    codigo, pagina = pedir("/login", {"usuario": "guest01", "clave": "guest123", "destino": ""})
    assert codigo == 200 and "guest01" in pagina
    assert mapper.logins == [{"usuario": "guest01", "clave": "guest123", "mac": "", "ip": "127.0.0.1"}] and mapper.tokens == ["t0ken"]


def test_si_el_arp_tiene_la_mac_se_manda_junto_a_la_ip(portal, monkeypatch):
    mapper, pedir = portal
    monkeypatch.setattr(server, "mac_de", lambda ip: "fa:16:3e:00:00:02")
    pedir("/login", {"usuario": "guest01", "clave": "guest123", "destino": ""})
    pedir("/")
    assert mapper.logins[0]["mac"] == "fa:16:3e:00:00:02" and mapper.logins[0]["ip"] == "127.0.0.1"
    assert mapper.estado_pedidos[-1] == {"mac": "fa:16:3e:00:00:02", "ip": "127.0.0.1"}


def test_el_cierre_de_sesion_tambien_manda_la_ip(portal):
    mapper, pedir = portal
    pedir("/logout", {})
    assert mapper.logouts == [{"mac": "", "ip": "127.0.0.1"}]


# ---- el cierre de sesion se confirma (24-nov: con el mapper lento el cierre se perdia y el portal redirigia como si nada)
@pytest.fixture
def cierre_rapido(monkeypatch):
    monkeypatch.setattr(server, "CIERRE_ESPERA_S", 0.3)
    monkeypatch.setattr(server, "CIERRE_PAUSA_S", 0)
    monkeypatch.setattr(server, "CIERRE_INTENTOS", 3)


def test_un_cierre_confirmado_vuelve_al_inicio_sin_reintentos(portal, cierre_rapido):
    mapper, pedir = portal
    codigo, pagina = pedir("/logout", {})
    assert codigo == 200 and 'action="/login"' in pagina and "No se pudo confirmar" not in pagina
    assert len(mapper.logouts) == 1


def test_sin_sesion_tambien_cuenta_como_confirmado_porque_el_cierre_es_idempotente(portal, cierre_rapido):
    mapper, pedir = portal
    mapper.logout_respuestas = [(200, {"status": "sin_sesion"})]
    codigo, pagina = pedir("/logout", {})
    assert codigo == 200 and "No se pudo confirmar" not in pagina and len(mapper.logouts) == 1


def test_si_el_mapper_tarda_mas_de_lo_que_espera_el_portal_reintenta_y_confirma(portal, cierre_rapido):
    mapper, pedir = portal
    mapper.logout_respuestas = [1.0, 1.0]                    # dos intentos que vencen; el tercero responde
    codigo, pagina = pedir("/logout", {})
    assert codigo == 200 and "No se pudo confirmar" not in pagina
    assert len(mapper.logouts) == 3 and all(l == {"mac": "", "ip": "127.0.0.1"} for l in mapper.logouts)


def test_si_el_mapper_nunca_confirma_el_portal_no_redirige_como_si_nada(portal, cierre_rapido):
    mapper, pedir = portal
    mapper.logout_respuestas = [1.0, 1.0, 1.0]
    codigo, pagina = pedir("/logout", {})
    assert codigo == 503 and "No se pudo confirmar el cierre" in pagina and 'action="/logout"' in pagina and "Reintentar" in pagina
    assert len(mapper.logouts) == 3


@pytest.mark.parametrize("respuesta", [(500, {"detail": "x"}), (503, {}), (403, {"detail": "token"}), (200, {}), (200, {"status": "otra_cosa"})])
def test_un_error_o_una_respuesta_desconocida_del_mapper_no_cuenta_como_cierre(portal, cierre_rapido, respuesta):
    mapper, pedir = portal
    mapper.logout_respuestas = [respuesta] * 3
    codigo, pagina = pedir("/logout", {})
    assert codigo == 503 and "No se pudo confirmar el cierre" in pagina and len(mapper.logouts) == 3


def test_con_el_mapper_caido_tambien_se_avisa(portal, cierre_rapido, monkeypatch):
    mapper, pedir = portal
    monkeypatch.setattr(server, "MAPPER", "http://127.0.0.1:1")
    codigo, pagina = pedir("/logout", {})
    assert codigo == 503 and "No se pudo confirmar el cierre" in pagina


def test_el_valor_por_defecto_espera_mas_que_las_transacciones_lentas_del_mapper():
    assert server.CIERRE_ESPERA_S >= 8 and server.CIERRE_INTENTOS >= 3      # el mapper llegó a tardar 5 a 11 s por transacción


def test_el_motivo_que_da_el_mapper_se_muestra_al_usuario(portal):
    mapper, pedir = portal
    mapper.login = (409, {"detail": "No se pudo identificar el equipo; reintente"})
    codigo, pagina = pedir("/login", {"usuario": "guest01", "clave": "guest123", "destino": ""})
    assert "No se pudo identificar el equipo; reintente" in pagina and 'action="/login"' in pagina


def test_credenciales_malas_dan_401_y_el_formulario_con_el_motivo(portal):
    mapper, pedir = portal
    mapper.login = (401, {"detail": "Usuario o contraseña incorrectos"})
    codigo, pagina = pedir("/login", {"usuario": "guest01", "clave": "mal", "destino": ""})
    assert codigo == 401 and "Usuario o contrase" in pagina


def test_un_equipo_con_sesion_8021x_que_admite_ascenso_ve_la_pagina_de_subir_de_rol_aunque_no_haya_arp(portal):
    mapper, pedir = portal
    mapper.sesion = {"usuario": "alumno01@example.org", "rol": "Alumno", "metodo": "802.1X", "ascenso_a": ["docente"], "base": None}
    codigo, pagina = pedir("/")
    assert "Subir de rol" in pagina


def test_el_mapper_caido_no_tumba_el_portal(portal, monkeypatch):
    mapper, pedir = portal
    monkeypatch.setattr(server, "MAPPER", "http://127.0.0.1:1")                 # nadie escucha
    codigo, pagina = pedir("/login", {"usuario": "guest01", "clave": "guest123", "destino": ""})
    assert "no responde" in pagina
