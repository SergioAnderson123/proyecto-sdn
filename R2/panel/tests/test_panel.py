"""El recolector y el servidor del panel (panel.py) con fuentes simuladas: una ronda de lecturas, la caché, el acceso SOLO para el rol superadmin (por la sesión del equipo, sin contraseña ni token),
que sea de solo lectura y que la página no construya HTML con datos ajenos."""
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
IP_ADMIN, IP_DOCENTE, IP_DESCONOCIDA = "10.0.1.150", "10.0.1.201", "10.9.9.9"
MAC_ADMIN, MAC_DOCENTE = "aa:aa:aa:aa:aa:01", "fa:16:3e:b6:96:08"
CFG = {"onos": {"ctrl1": "http://c1", "ctrl2": "http://c2", "ctrl3": "http://c3"}, "mapper": "http://map", "vigilantes": {"ctrl3": "http://v3", "ctrl2": "http://v2"}, "onos_usuario": "onos", "onos_clave": "rocks"}


def devs(disp):
    return {"devices": [{"id": i, "available": v, "role": "MASTER"} for i, v in disp.items()]}


def _estado_vig(caidos=(), conexa=True, rol="principal", activo=True):
    pares = [("core-1", 4, "core-2", 4), ("core-1", 2, "acc-1", 2), ("core-2", 3, "acc-1", 3)]
    enlaces = [{"a": a, "b": b, "a_nombre": a, "b_nombre": b, "a_puerto": pa, "b_puerto": pb, "estado": "caido" if (a, b) in caidos else "arbol"} for a, pa, b, pb in pares]
    return {"t": AHORA, "rol": rol, "activo": activo, "aplica": True, "estado": "sin cambios", "modo": "capa3",
            "topologia": {"raiz": {"id": "core-1", "nombre": "core-1"}, "switches": [], "enlaces": enlaces, "aislados": [] if conexa else [{"id": "acc-1", "nombre": "acc-1"}], "conexa": conexa},
            "historial": [{"t": AHORA - 5, "tipo": "aplicado", "detalle": "Recálculo aplicado", "cambios": {"borde": {"quitar": 0, "agregar": 4}}}]}


HOSTS = {"hosts": [{"mac": MAC_ADMIN.upper(), "ipAddresses": [IP_ADMIN]}, {"mac": MAC_DOCENTE.upper(), "ipAddresses": [IP_DOCENTE]}]}


class Fuentes:
    """Simula la red: url -> (datos, error). Cuenta cada petición."""

    def __init__(self, **cambios):
        self.llamadas = []
        sw = {"of:1": True, "of:2": True}
        self.datos = {
            "http://c1/onos/v1/devices": (devs(sw), None), "http://c2/onos/v1/devices": (devs(sw), None), "http://c3/onos/v1/devices": (devs(sw), None),
            "http://c1/onos/v1/hosts": (HOSTS, None),
            "http://map/sessions": ({"sessions": {
                MAC_DOCENTE.upper(): {"username": "profesor01@example.org", "rol": "Docente", "vlan": 10, "dpid": "of:1", "port": "1", "expira": AHORA + 600},
                MAC_ADMIN: {"username": "root@example.org", "rol": "Superadmin", "vlan": 99, "dpid": "of:1", "port": "2", "expira": None}}}, None),
            "http://map/events?limit=200": ({"events": [{"t": 1.5, "tipo": "admitido", "usuario": "profesor01@example.org", "mac": "fa:16:3e:b6:96:08", "detalle": "VLAN 10"},
                                                         {"t": 2.5, "tipo": "rechazado", "usuario": "<img src=x onerror=alert(1)>", "mac": "", "detalle": "Portal: usuario o contraseña incorrectos"}]}, None),
            "http://map/health/ha": ({"status": "ready"}, None),
            "http://v3/salud": ({"rol": "principal", "ultimo_ciclo": AHORA - 2, "estado": "sin cambios", "aplica": True, "activo": True}, None),
            "http://v2/salud": ({"rol": "respaldo", "ultimo_ciclo": AHORA - 3, "estado": "sin cambios", "aplica": True, "activo": False}, None),
            "http://v3/estado": (_estado_vig(), None), "http://v2/estado": (_estado_vig(rol="respaldo", activo=False), None),
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
    assert len(f.llamadas) == len(set(f.llamadas)) == 10                                    # 3 de ONOS, 1 de hosts, 3 del mapper, 2 /salud y 1 /estado (solo del vigilante que manda)
    assert r["switches"] == {"total": 2, "disponibles": 2} and r["sesiones"]["total"] == 2 and r["avisos"] == [] and r["mapper_ok"] is True
    est = rec.estado()
    assert est["listo"] and est["sesiones"][0]["usuario"] == "profesor01@example.org" and est["caidas"] == [] and est["hosts"] == HOSTS
    assert h.contar() == 2


def test_los_hosts_se_piden_al_primer_nodo_que_conteste_y_solo_a_uno():
    rec, _, f = recolector(**{"http://c1/onos/v1/hosts": (None, "timeout"), "http://c2/onos/v1/hosts": (HOSTS, None)})
    rec.ciclo()
    assert [u for u in f.llamadas if u.endswith("/hosts")] == ["http://c1/onos/v1/hosts", "http://c2/onos/v1/hosts"] and rec.estado()["hosts"] == HOSTS


def test_si_ningun_nodo_da_los_hosts_se_avisa_porque_nadie_podra_ser_identificado():
    rec, _, _ = recolector(**{"http://c1/onos/v1/hosts": (None, "timeout")})
    r = rec.ciclo()
    assert rec.estado()["hosts"] is None and "FUENTE_CAIDA" in [a["codigo"] for a in r["avisos"]]


def test_el_estado_se_pide_solo_al_vigilante_que_manda_y_se_guarda_con_su_nombre():
    rec, _, f = recolector()
    rec.ciclo()
    assert [u for u in f.llamadas if u.endswith("/estado")] == ["http://v3/estado"]
    assert rec.estado()["vigilante"]["nombre"] == "ctrl3" and rec.estado()["vigilante"]["estado"]["topologia"]["conexa"]


def test_si_el_principal_calla_se_le_pide_el_estado_al_respaldo_que_asume_el_mando():
    rec, _, f = recolector(**{"http://v3/salud": (None, "URLError"), "http://v2/salud": ({"rol": "respaldo", "ultimo_ciclo": AHORA - 1, "estado": "sin cambios", "aplica": True, "activo": True}, None)})
    rec.ciclo()
    assert [u for u in f.llamadas if u.endswith("/estado")] == ["http://v2/estado"] and rec.estado()["vigilante"]["nombre"] == "ctrl2"


def test_un_vigilante_de_la_version_anterior_sin_estado_no_rompe_el_panel():
    rec, _, _ = recolector(**{"http://v3/estado": (None, "HTTP 404")})
    r = rec.ciclo()
    assert rec.estado()["listo"] and rec.estado()["vigilante"] == {"nombre": "ctrl3", "estado": None} and "vigilante ctrl3 /estado: HTTP 404" in rec.estado()["caidas"]
    assert r["avisos"] == []


def test_un_cable_caido_o_la_red_partida_que_cuenta_el_vigilante_salen_como_avisos():
    rec, _, _ = recolector(**{"http://v3/estado": (_estado_vig(caidos=[("core-1", "acc-1")]), None)})
    a = rec.ciclo()["avisos"]
    assert [(x["codigo"], x["gravedad"]) for x in a] == [("CABLE_CAIDO", "media")] and "core-1/2 ↔ acc-1/2" in a[0]["texto"]
    rec2, _, _ = recolector(**{"http://v3/estado": (_estado_vig(conexa=False), None)})
    assert [x["codigo"] for x in rec2.ciclo()["avisos"]] == ["RED_PARTIDA"]


def _devs_rol(maestro):
    """Lo que de verdad responde ONOS: cada nodo da SU rol; solo el maestro dice MASTER."""
    return {"http://c%d/onos/v1/devices" % i: ({"devices": [{"id": s, "available": True, "role": "MASTER" if n == maestro else "STANDBY"} for s in ("of:1", "of:2")]}, None)
            for i, n in ((1, "ctrl1"), (2, "ctrl2"), (3, "ctrl3"))}


def test_si_manda_ctrl3_y_solo_ctrl1_y_ctrl2_sirven_802_1x_hay_un_aviso_alto_por_switch_con_su_nombre():
    cfg = dict(CFG, maestros_validos=["ctrl1", "ctrl2"])
    f = Fuentes(**_devs_rol("ctrl3"), **{"http://v3/estado": (dict(_estado_vig(), topologia=dict(_estado_vig()["topologia"], switches=[{"id": "of:1", "nombre": "core-1"}, {"id": "of:2", "nombre": "acc-1"}])), None)})
    rec = p.Recolector(cfg, d.Historial(), leer=f, reloj=lambda: AHORA)
    a = [x for x in rec.ciclo()["avisos"] if x["codigo"] == "MAESTRO_INVALIDO"]
    assert len(a) == 2 and "core-1 (of:1)" in a[0]["texto"] and "ctrl3" in a[0]["texto"]
    rec2 = p.Recolector(cfg, d.Historial(), leer=Fuentes(**_devs_rol("ctrl1")), reloj=lambda: AHORA)
    assert [x for x in rec2.ciclo()["avisos"] if x["codigo"] == "MAESTRO_INVALIDO"] == []


def test_sin_lista_de_nodos_validos_el_panel_no_juzga_quien_manda():
    rec = p.Recolector(CFG, d.Historial(), leer=Fuentes(**_devs_rol("ctrl3")), reloj=lambda: AHORA)
    assert [x["codigo"] for x in rec.ciclo()["avisos"]] == []


def test_la_linea_de_comandos_lee_la_lista_de_maestros_validos(tmp_path, monkeypatch):
    capturado = {}
    monkeypatch.setattr(p, "Recolector", lambda cfg, h, **k: capturado.setdefault("cfg", cfg) and (_ for _ in ()).throw(SystemExit(0)))
    base = ["--onos", "ctrl1=http://c1", "--mapper", "http://m", "--db", str(tmp_path / "p.sqlite")]
    with pytest.raises(SystemExit):
        p.main(["--maestros-validos", "ctrl1, ctrl2"] + base)
    assert capturado["cfg"]["maestros_validos"] == ["ctrl1", "ctrl2"]


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


def test_una_ronda_con_una_excepcion_no_tumba_el_hilo():
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
    """Levanta el panel simulando desde qué IP llega la conexión (en la red real sería la del equipo)."""
    def _levantar(ip=IP_ADMIN, roles=("superadmin",), **cambios):
        rec, h, f = recolector(**cambios)
        rec.ciclo()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), p.hacer_manejador(rec, h, roles, reloj=lambda: AHORA, ip_de=lambda manejador: ip))
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


API = ("/api/resumen", "/api/sesiones", "/api/eventos", "/api/vistas", "/api/vigilante")


def test_el_superadmin_entra_por_la_sesion_de_su_equipo_sin_contrasena_ni_token(servidor):
    base, *_ = servidor(ip=IP_ADMIN)
    for ruta in API:
        assert pedir(base + ruta)[0] == 200, ruta
    r = json.loads(pedir(base + "/api/resumen")[2])
    assert r["quien"] == "root@example.org (Superadmin)"


def test_un_docente_no_entra_aunque_tenga_sesion_y_se_le_dice_por_que(servidor):
    base, *_ = servidor(ip=IP_DOCENTE)
    for ruta in API:
        c, _, cuerpo = pedir(base + ruta)
        assert c == 403, ruta
    j = json.loads(cuerpo)
    assert "Docente" in j["motivo"] and "superadmin" in j["requiere"]


def test_un_equipo_sin_sesion_o_desconocido_no_entra(servidor):
    base, *_ = servidor(ip=IP_DESCONOCIDA)
    assert [pedir(base + r)[0] for r in API] == [403] * len(API)


def test_si_la_ip_la_tienen_dos_mac_distintas_es_ambigua_y_no_entra_nadie(servidor):
    doble = {"hosts": HOSTS["hosts"] + [{"mac": "bb:bb:bb:bb:bb:02", "ipAddresses": [IP_ADMIN]}]}
    base, *_ = servidor(ip=IP_ADMIN, **{"http://c1/onos/v1/hosts": (doble, None)})
    assert pedir(base + "/api/sesiones")[0] == 403


def test_sin_los_hosts_de_onos_nadie_se_identifica_y_solo_queda_el_tunel_local(servidor):
    base, *_ = servidor(ip=IP_ADMIN, **{"http://c1/onos/v1/hosts": (None, "timeout")})
    assert pedir(base + "/api/sesiones")[0] == 403
    base2, *_ = servidor(ip="127.0.0.1", **{"http://c1/onos/v1/hosts": (None, "timeout")})
    assert pedir(base2 + "/api/sesiones")[0] == 200


def test_las_cabeceras_que_dicen_ser_otra_ip_se_ignoran(servidor):
    base, *_ = servidor(ip=IP_DOCENTE)
    falsas = {"X-Forwarded-For": IP_ADMIN, "X-Real-IP": IP_ADMIN, "Forwarded": f"for={IP_ADMIN}", "Authorization": "Bearer lo-que-sea"}
    assert pedir(base + "/api/sesiones", cab=falsas)[0] == 403


def test_el_tunel_local_solo_puede_leer(servidor):
    base, *_ = servidor(ip="127.0.0.1")
    assert [pedir(base + r)[0] for r in API] == [200] * len(API)
    ok, motivo = d.decidir_acceso("127.0.0.1", "editar", HOSTS, [], ["superadmin"])
    assert not ok and "no basta" in motivo


def test_el_panel_es_de_solo_lectura_incluso_para_el_superadmin(servidor):
    base, *_ = servidor(ip=IP_ADMIN)
    for metodo in ("POST", "PUT", "DELETE", "PATCH"):
        assert pedir(base + "/api/sesiones", metodo, cuerpo=b"{}" if metodo != "DELETE" else None)[0] == 405, metodo
    assert pedir(base + "/sessions/revoke", "POST", cuerpo=b"{}")[0] == 405
    assert pedir(base + "/entrar", "POST", cuerpo=b"token=x")[0] == 405                       # ya no existe ningun acceso por token
    assert pedir(base + "/api/inexistente")[0] == 403 or pedir(base + "/api/inexistente")[0] == 404
    assert pedir(base + "/otra")[0] == 404


def test_la_pagina_y_el_estado_son_publicos_pero_no_revelan_datos(servidor):
    base, *_ = servidor(ip=IP_DESCONOCIDA)
    assert pedir(base + "/")[0] == 200 and pedir(base + "/app.js")[0] == 200 and pedir(base + "/style.css")[0] == 200
    assert json.loads(pedir(base + "/salud")[2]) == {"ok": True, "listo": True}
    pagina = pedir(base + "/")[2].decode()
    assert "profesor01" not in pagina and "of:" not in pagina


def test_las_respuestas_llevan_cabeceras_de_seguridad_y_no_se_guardan_en_cache(servidor):
    base, *_ = servidor()
    _, cab, _ = pedir(base + "/api/resumen")
    assert cab["Cache-Control"] == "no-store" and cab["X-Content-Type-Options"] == "nosniff" and "default-src 'self'" in cab["Content-Security-Policy"]
    _, cab2, _ = pedir(base + "/")
    assert cab2["X-Frame-Options"] == "DENY" and "Server" in cab2 and "Python" not in cab2["Server"]


def test_la_api_del_vigilante_entrega_la_topologia_y_el_historial_y_pide_el_mismo_rol(servidor):
    base, *_ = servidor(ip=IP_ADMIN)
    j = json.loads(pedir(base + "/api/vigilante")[2])
    assert j["listo"] and j["vigilante"]["nombre"] == "ctrl3" and len(j["vigilante"]["estado"]["topologia"]["enlaces"]) == 3 and j["vigilante"]["estado"]["historial"][0]["tipo"] == "aplicado"
    base2, *_ = servidor(ip=IP_DOCENTE)
    assert pedir(base2 + "/api/vigilante")[0] == 403


def test_las_api_devuelven_lo_que_hay_en_la_cache(servidor):
    base, _, _, f = servidor()
    n = len(f.llamadas)
    resumen = json.loads(pedir(base + "/api/resumen")[2])
    assert resumen["listo"] and resumen["resumen"]["switches"] == {"total": 2, "disponibles": 2} and resumen["ahora"] == AHORA
    ses = json.loads(pedir(base + "/api/sesiones")[2])["sesiones"]
    assert ses[0]["usuario"] == "profesor01@example.org" and ses[0]["expira_en_s"] == 600
    vistas = json.loads(pedir(base + "/api/vistas")[2])["vistas"]
    assert vistas["desiguales"] == [] and len(vistas["switches"]) == 2
    assert len(f.llamadas) == n                                                              # pedir páginas no consulta la red


def test_los_eventos_se_filtran_desde_la_url_y_un_valor_raro_no_rompe_nada(servidor):
    base, *_ = servidor()
    todos = json.loads(pedir(base + "/api/eventos")[2])
    assert [e["t"] for e in todos["eventos"]] == [2.5, 1.5] and todos["total_guardados"] == 2 and "rechazado" in todos["tipos"]
    assert [e["t"] for e in json.loads(pedir(base + "/api/eventos?tipo=admitido")[2])["eventos"]] == [1.5]
    assert [e["t"] for e in json.loads(pedir(base + "/api/eventos?usuario=PROFESOR&mac=fa:16")[2])["eventos"]] == [1.5]
    assert pedir(base + "/api/eventos?limite=abc&desde=x&hasta=")[0] == 200
    assert pedir(base + "/api/eventos?usuario=%27%3B%20DROP%20TABLE%20eventos%3B--")[0] == 200


def test_un_usuario_malicioso_del_portal_viaja_como_dato_en_json_no_como_html(servidor):
    base, *_ = servidor()
    c, cab, cuerpo = pedir(base + "/api/eventos?tipo=rechazado")
    assert cab["Content-Type"].startswith("application/json") and "<img src=x" in json.loads(cuerpo)["eventos"][0]["usuario"]


def test_la_pagina_nunca_construye_html_con_datos_ajenos():
    js = open(os.path.join(RAIZ, "static", "app.js"), encoding="utf-8").read()
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", js)
    assert "token" not in js.lower() and "/entrar" not in js
    html = open(os.path.join(RAIZ, "static", "index.html"), encoding="utf-8").read()
    assert "<script>" not in html and not re.search(r"\son\w+=", html) and "token" not in html.lower().replace("ni token", "")


# ---------- el arranque ----------
def test_se_niega_a_arrancar_sin_ningun_rol_de_administracion(tmp_path):
    base = ["--onos", "c1=http://c1", "--mapper", "http://m", "--db", str(tmp_path / "p.sqlite")]
    assert p.main(["--roles-admin", " , "] + base) == 2


def test_las_listas_de_nodos_se_leen_de_la_linea_de_comandos():
    assert p._pares("ctrl1=http://a:8181/, ctrl2=http://b:8181") == {"ctrl1": "http://a:8181", "ctrl2": "http://b:8181"}
    assert p._pares("") == {} and p._pares("sin-igual") == {}
