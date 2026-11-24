"""El recolector y el servidor del panel (panel.py) con fuentes simuladas: una ronda de lecturas, la caché, el acceso por token, que sea solo de lectura y que la página no construya HTML con datos ajenos."""
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
import datos as d  # noqa: E402
import panel as p  # noqa: E402

AHORA = 2_000_000.0
TOKEN = "un-token-de-prueba-largo"
CFG = {"onos": {"ctrl1": "http://c1", "ctrl2": "http://c2", "ctrl3": "http://c3"}, "mapper": "http://map", "vigilantes": {"ctrl3": "http://v3", "ctrl2": "http://v2"}, "onos_usuario": "onos", "onos_clave": "rocks"}


def devs(disp):
    return {"devices": [{"id": i, "available": v, "role": "MASTER"} for i, v in disp.items()]}


class Fuentes:
    """Simula la red: url -> (datos, error). Cuenta cada petición."""

    def __init__(self, **cambios):
        self.llamadas = []
        sw = {"of:1": True, "of:2": True}
        self.datos = {
            "http://c1/onos/v1/devices": (devs(sw), None), "http://c2/onos/v1/devices": (devs(sw), None), "http://c3/onos/v1/devices": (devs(sw), None),
            "http://map/sessions": ({"sessions": {"FA:16:3E:B6:96:08": {"username": "profesor01@example.org", "rol": "Docente", "vlan": 10, "dpid": "of:1", "port": "1", "expira": AHORA + 600}}}, None),
            "http://map/events?limit=200": ({"events": [{"t": 1.5, "tipo": "admitido", "usuario": "profesor01@example.org", "mac": "fa:16:3e:b6:96:08", "detalle": "VLAN 10"},
                                                         {"t": 2.5, "tipo": "rechazado", "usuario": "<img src=x onerror=alert(1)>", "mac": "", "detalle": "Portal: usuario o contraseña incorrectos"}]}, None),
            "http://map/health/ha": ({"status": "ready"}, None),
            "http://v3/salud": ({"rol": "principal", "ultimo_ciclo": AHORA - 2, "estado": "sin cambios", "aplica": True, "activo": True}, None),
            "http://v2/salud": ({"rol": "respaldo", "ultimo_ciclo": AHORA - 3, "estado": "sin cambios", "aplica": True, "activo": False}, None),
        }
        self.datos.update(cambios)

    def __call__(self, url, **_):
        self.llamadas.append(url)
        return self.datos.get(url, (None, "URLError"))


def recolector(**cambios):
    f = Fuentes(**cambios)
    h = d.Historial()
    return p.Recolector(CFG, h, leer=f, reloj=lambda: AHORA), h, f


# ---------- el recolector ----------
def test_una_ronda_lee_cada_fuente_una_vez_y_arma_el_resumen():
    rec, h, f = recolector()
    r = rec.ciclo()
    assert len(f.llamadas) == len(set(f.llamadas)) == 8                                     # 3 nodos de ONOS, 3 del mapper y 2 vigilantes: pocas peticiones
    assert r["switches"] == {"total": 2, "disponibles": 2} and r["sesiones"]["total"] == 1 and r["avisos"] == [] and r["mapper_ok"] is True
    est = rec.estado()
    assert est["listo"] and est["sesiones"][0]["usuario"] == "profesor01@example.org" and est["caidas"] == []
    assert h.contar() == 2


def test_las_pantallas_leen_la_cache_y_no_provocan_lecturas():
    rec, _, f = recolector()
    rec.ciclo()
    n = len(f.llamadas)
    for _ in range(50):
        rec.estado()
    assert len(f.llamadas) == n


def test_antes_de_la_primera_ronda_el_estado_dice_que_no_esta_listo():
    rec, _, _ = recolector()
    assert rec.estado()["listo"] is False


def test_la_ronda_repetida_no_duplica_eventos():
    rec, h, _ = recolector()
    rec.ciclo()
    rec.ciclo()
    assert h.contar() == 2


def test_la_vista_mala_de_onos_sale_como_aviso_de_la_ronda():
    mala = (devs({"of:1": False, "of:2": False}), None)
    rec, _, _ = recolector(**{"http://c1/onos/v1/devices": mala, "http://c3/onos/v1/devices": mala})
    codigos = [a["codigo"] for a in rec.ciclo()["avisos"]]
    assert codigos.count("VISTA_DESIGUAL") == 2 and codigos.count("NODO_SIN_SWITCHES") == 2


def test_un_nodo_de_onos_caido_se_avisa_y_no_estropea_lo_demas():
    rec, _, _ = recolector(**{"http://c2/onos/v1/devices": (None, "timeout")})
    r = rec.ciclo()
    assert "NODO_ONOS_SIN_RESPUESTA" in [a["codigo"] for a in r["avisos"]]
    assert r["switches"]["disponibles"] == 2 and "ONOS ctrl2: timeout" in rec.estado()["caidas"]


def test_el_mapper_en_503_es_no_listo_y_una_caida_de_conexion_es_sin_dato():
    rec, _, _ = recolector(**{"http://map/health/ha": (None, "HTTP 503")})
    r = rec.ciclo()
    assert r["mapper_ok"] is False and "MAPPER_NO_LISTO" in [a["codigo"] for a in r["avisos"]]
    rec2, _, _ = recolector(**{"http://map/health/ha": (None, "URLError")})
    r2 = rec2.ciclo()
    assert r2["mapper_ok"] is None and "MAPPER_NO_LISTO" not in [a["codigo"] for a in r2["avisos"]]


def test_si_el_mapper_no_contesta_las_sesiones_quedan_vacias_pero_el_panel_sigue():
    rec, _, _ = recolector(**{"http://map/sessions": (None, "URLError"), "http://map/events?limit=200": (None, "URLError")})
    r = rec.ciclo()
    assert r["sesiones"]["total"] == 0 and rec.estado()["listo"]
    assert any(c.startswith("mapper /sessions") for c in rec.estado()["caidas"])


def test_si_el_vigilante_principal_no_responde_y_el_respaldo_no_esta_activo_es_un_aviso_alto():
    rec, _, _ = recolector(**{"http://v3/salud": (None, "URLError")})
    assert "VIGILANTE_SIN_ACTIVO" in [a["codigo"] for a in rec.ciclo()["avisos"]]


def test_una_ronda_con_una_excepcion_no_tumba_el_hilo(monkeypatch):
    rec, _, _ = recolector()
    veces = {"n": 0}
    original = rec.ciclo

    def a_veces():
        veces["n"] += 1
        if veces["n"] == 1:
            raise RuntimeError("fallo")
        return original()
    rec.ciclo = a_veces
    parar = threading.Event()
    parar.wait = lambda t: parar.set() if veces["n"] >= 2 else None                         # sin esperas reales
    rec.correr(0, parar)
    assert veces["n"] == 2 and rec.estado()["listo"]


# ---------- el servidor ----------
@pytest.fixture()
def servidor():
    def _levantar(token=TOKEN, **cambios):
        rec, h, f = recolector(**cambios)
        rec.ciclo()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), p.hacer_manejador(rec, h, token, reloj=lambda: AHORA))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", rec, h, f
    servidores = []
    yield _levantar
    for s in servidores:
        s.shutdown()
        s.server_close()


def pedir(url, metodo="GET", cab=None, cuerpo=None):
    req = urllib.request.Request(url, method=metodo, headers=cab or {}, data=cuerpo)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_sin_token_los_datos_dan_401_pero_la_pagina_y_el_estado_no(servidor):
    base, *_ = servidor()
    for ruta in ("/api/resumen", "/api/sesiones", "/api/eventos", "/api/vistas"):
        assert pedir(base + ruta)[0] == 401, ruta
    assert pedir(base + "/")[0] == 200 and pedir(base + "/app.js")[0] == 200 and pedir(base + "/style.css")[0] == 200
    assert json.loads(pedir(base + "/salud")[2]) == {"ok": True, "listo": True}              # no revela datos


def test_el_token_por_cabecera_o_por_cookie_da_acceso_y_uno_malo_no(servidor):
    base, *_ = servidor()
    assert pedir(base + "/api/sesiones", cab={"Authorization": "Bearer " + TOKEN})[0] == 200
    assert pedir(base + "/api/sesiones", cab={"Authorization": "Bearer otro"})[0] == 401
    assert pedir(base + "/api/sesiones", cab={"Cookie": "panel=" + TOKEN})[0] == 200
    assert pedir(base + "/api/sesiones", cab={"Cookie": "panel=malo"})[0] == 401


def test_el_acceso_por_formulario_pone_una_cookie_httponly(servidor):
    base, *_ = servidor()
    c, cab, _ = pedir(base + "/entrar", "POST", {"Content-Type": "application/x-www-form-urlencoded"}, ("token=" + TOKEN).encode())
    assert c == 200 and "HttpOnly" in cab["Set-Cookie"] and "SameSite=Strict" in cab["Set-Cookie"]
    c, _, _ = pedir(base + "/entrar", "POST", {"Content-Type": "application/x-www-form-urlencoded"}, b"token=malo")
    assert c == 401


def test_el_panel_es_de_solo_lectura_cualquier_otro_metodo_o_ruta_de_escritura_se_rechaza(servidor):
    base, *_ = servidor(token="")
    for metodo in ("PUT", "DELETE", "PATCH"):
        assert pedir(base + "/api/sesiones", metodo)[0] == 405, metodo
    assert pedir(base + "/api/sesiones", "POST", cuerpo=b"{}")[0] == 405
    assert pedir(base + "/sessions/revoke", "POST", cuerpo=b"{}")[0] == 405
    assert pedir(base + "/api/inexistente")[0] == 404


def test_las_respuestas_llevan_cabeceras_de_seguridad_y_no_se_guardan_en_cache(servidor):
    base, *_ = servidor()
    _, cab, _ = pedir(base + "/api/resumen", cab={"Authorization": "Bearer " + TOKEN})
    assert cab["Cache-Control"] == "no-store" and cab["X-Content-Type-Options"] == "nosniff" and "default-src 'self'" in cab["Content-Security-Policy"]
    _, cab2, _ = pedir(base + "/")
    assert cab2["X-Frame-Options"] == "DENY" and "Server" in cab2 and "Python" not in cab2["Server"]


def test_las_api_devuelven_lo_que_hay_en_la_cache(servidor):
    base, _, _, f = servidor()
    aut = {"Authorization": "Bearer " + TOKEN}
    n = len(f.llamadas)
    resumen = json.loads(pedir(base + "/api/resumen", cab=aut)[2])
    assert resumen["listo"] and resumen["resumen"]["switches"] == {"total": 2, "disponibles": 2} and resumen["ahora"] == AHORA
    ses = json.loads(pedir(base + "/api/sesiones", cab=aut)[2])["sesiones"]
    assert ses[0]["usuario"] == "profesor01@example.org" and ses[0]["expira_en_s"] == 600
    vistas = json.loads(pedir(base + "/api/vistas", cab=aut)[2])["vistas"]
    assert vistas["desiguales"] == [] and len(vistas["switches"]) == 2
    assert len(f.llamadas) == n                                                              # pedir páginas no consulta la red


def test_los_eventos_se_filtran_desde_la_url_y_un_valor_raro_no_rompe_nada(servidor):
    base, *_ = servidor()
    aut = {"Authorization": "Bearer " + TOKEN}
    todos = json.loads(pedir(base + "/api/eventos", cab=aut)[2])
    assert [e["t"] for e in todos["eventos"]] == [2.5, 1.5] and todos["total_guardados"] == 2 and "rechazado" in todos["tipos"]
    assert [e["t"] for e in json.loads(pedir(base + "/api/eventos?tipo=admitido", cab=aut)[2])["eventos"]] == [1.5]
    assert [e["t"] for e in json.loads(pedir(base + "/api/eventos?usuario=PROFESOR&mac=fa:16", cab=aut)[2])["eventos"]] == [1.5]
    assert pedir(base + "/api/eventos?limite=abc&desde=x&hasta=", cab=aut)[0] == 200
    assert pedir(base + "/api/eventos?usuario=%27%3B%20DROP%20TABLE%20eventos%3B--", cab=aut)[0] == 200


def test_un_usuario_malicioso_del_portal_viaja_como_dato_en_json_no_como_html(servidor):
    base, *_ = servidor()
    c, cab, cuerpo = pedir(base + "/api/eventos?tipo=rechazado", cab={"Authorization": "Bearer " + TOKEN})
    assert cab["Content-Type"].startswith("application/json") and "<img src=x" in json.loads(cuerpo)["eventos"][0]["usuario"]


def test_la_pagina_nunca_construye_html_con_datos_ajenos():
    js = open(os.path.join(RAIZ, "static", "app.js"), encoding="utf-8").read()
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", js)
    html = open(os.path.join(RAIZ, "static", "index.html"), encoding="utf-8").read()
    assert "<script>" not in html and not re.search(r"\son\w+=", html)                       # sin scripts ni manejadores en línea (la CSP los bloquearía)


# ---------- el arranque ----------
def test_se_niega_a_escuchar_fuera_de_loopback_sin_un_token_largo(monkeypatch, tmp_path):
    monkeypatch.delenv("PANEL_TOKEN", raising=False)
    base = ["--onos", "c1=http://c1", "--mapper", "http://m", "--db", str(tmp_path / "p.sqlite")]
    assert p.main(["--escuchar", "192.168.201.23"] + base) == 2
    monkeypatch.setenv("PANEL_TOKEN", "corto")
    assert p.main(["--escuchar", "0.0.0.0"] + base) == 2


def test_las_listas_de_nodos_se_leen_de_la_linea_de_comandos():
    assert p._pares("ctrl1=http://a:8181/, ctrl2=http://b:8181") == {"ctrl1": "http://a:8181", "ctrl2": "http://b:8181"}
    assert p._pares("") == {} and p._pares("sin-igual") == {}
