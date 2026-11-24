"""`/api/switches` y `/api/flujos` del panel: lectura del maestro, filtros, paginación, comandos de ovs-ofctl, caché, errores y acceso (solo superadmin; el túnel local lee)."""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
sys.path.insert(0, AQUI)
import datos as d  # noqa: E402
import flujos as fl  # noqa: E402
import panel as p  # noqa: E402
from test_flujos import ACC1, ENTREGA, ROL, SESION, DROP_ROL, DHCP, RUTA, BLOQUEO  # noqa: E402
from test_panel import AHORA, CFG, IP_ADMIN, IP_DOCENTE, Fuentes, _estado_vig  # noqa: E402

BORDE = "of:000052ca28945241"
VM = {"acc-1": "sw-acc-1:5812", "borde": "switch-borde:"}
FLUJOS = [SESION, ROL, DROP_ROL, ENTREGA, RUTA, BLOQUEO, DHCP]


def _devs(maestro):
    return {f"http://c{i}/onos/v1/devices": ({"devices": [{"id": s, "available": True, "role": "MASTER" if n == maestro else "STANDBY"} for s in (ACC1, BORDE)]}, None)
            for i, n in ((1, "ctrl1"), (2, "ctrl2"), (3, "ctrl3"))}


def _vig():
    e = _estado_vig()
    e["topologia"]["switches"] = [{"id": ACC1, "nombre": "acc-1"}, {"id": BORDE, "nombre": "borde"}]
    return {"http://v3/estado": (e, None)}


class Onos:
    def __init__(self, flujos=None, error=None):
        self.llamadas, self.flujos, self.error = [], flujos if flujos is not None else FLUJOS, error

    def __call__(self, url, **kw):
        self.llamadas.append(url)
        return (None, self.error) if self.error else ({"flows": self.flujos}, None)


@pytest.fixture()
def servidor():
    servidores = []

    def _levantar(ip=IP_ADMIN, maestro="ctrl2", onos=None, con_lector=True):
        rec = p.Recolector(CFG, d.Historial(), leer=Fuentes(**_devs(maestro), **_vig()), reloj=lambda: AHORA)
        rec.ciclo()
        onos = onos or Onos()
        lector = fl.LectorFlujos(CFG["onos"], onos, ahora=lambda: AHORA, usuario="onos", clave="rocks") if con_lector else None
        srv = ThreadingHTTPServer(("127.0.0.1", 0), p.hacer_manejador(rec, d.Historial(), ["superadmin"], reloj=lambda: AHORA, ip_de=lambda m: ip, lector=lector, vm_switch=VM, acceso_ssh="10.20.11.14"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", onos

    yield _levantar
    for s in servidores:
        s.shutdown()
        s.server_close()


def pedir(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_la_lista_de_switches_trae_nombre_maestro_y_donde_ejecutar_los_comandos(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/switches")
    assert c == 200
    por = {s["nombre"]: s for s in j["switches"]}
    assert set(por) == {"acc-1", "borde"} and por["acc-1"]["maestro"] == "ctrl2" and por["acc-1"]["id"] == ACC1 and por["acc-1"]["disponible"]
    assert por["acc-1"]["ssh"] == {"vm": "sw-acc-1", "comando": "ssh -p 5812 ubuntu@10.20.11.14", "local": False}
    assert por["borde"]["ssh"] == {"vm": "switch-borde", "comando": "ssh ubuntu@10.20.11.14", "local": True}


def test_los_flujos_se_leen_del_maestro_y_llegan_normalizados_y_explicados(servidor):
    base, onos = servidor()
    c, j = pedir(base + "/api/flujos?switch=acc-1")
    assert c == 200 and onos.llamadas == [f"http://c2/onos/v1/flows/{ACC1}"] and j["nodo"] == "ctrl2" and not j["antiguo"] and j["error"] is None
    assert j["total"] == j["total_switch"] == 7 and j["switch"]["nombre"] == "acc-1"
    x = next(f for f in j["flujos"] if f["prioridad"] == 40001)
    assert x["coincide"]["in_port"] == "1" and "Sesión 802.1X" in x["explicacion"] and x["app"] == "org.campus.r2"
    assert j["cuentas"]["tablas"] == {"0": 2, "20": 2, "60": 3}
    assert c == 200 and pedir(base + f"/api/flujos?switch={ACC1}")[0] == 200                              # también por identificador


def test_los_filtros_se_aplican_en_el_servidor_y_arman_el_comando_para_el_switch(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/flujos?switch=acc-1&tabla=60&ip_dst=10.0.1.100&prioridad=100-200")
    assert c == 200 and [(f["tabla"], f["prioridad"]) for f in j["flujos"]] == [(60, 160)] and j["total"] == 1 and j["total_switch"] == 7
    assert j["comandos"]["principal"].startswith('sudo ovs-ofctl -O OpenFlow13 dump-flows br0 "table=60,ip,nw_dst=10.0.1.100"') and 'grep -E "priority=(1[0-9]{2}|200)[, ]"' in j["comandos"]["principal"]
    assert j["filtros"] == {"tabla": "60", "prioridad": "100-200", "ip_dst": "10.0.1.100"}
    c, j = pedir(base + "/api/flujos?switch=acc-1&ip_dst_cubre=10.0.2.77")
    assert j["total"] == 2 and j["comandos"]["traza"] is None                                            # la ruta 10.0.2.0/24 y el bloqueo 10/8 contienen esa IP; sin puerto de entrada no hay traza
    c, j = pedir(base + "/api/flujos?switch=acc-1&in_port=1&ip_dst_cubre=10.0.2.77")
    assert j["comandos"]["traza"].endswith("nw_dst=10.0.2.77,nw_ttl=64'") and j["total"] == 0                        # los dos filtros a la vez: ninguna regla tiene puerto de entrada y a la vez esa IP


def test_filtrar_por_app_deduce_el_cookie_de_ovs_de_los_flujos_de_esa_app(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/flujos?switch=acc-1&app=campus.r2&tabla=0")
    assert c == 200 and j["total"] == 1 and "cookie=0x9e000000000000/0xffff000000000000" in j["comandos"]["principal"]
    c, j = pedir(base + "/api/flujos?switch=acc-1&app=campus")                                          # varias apps: no se arriesga un cookie
    assert "cookie" not in j["comandos"]["principal"] and any("cookie" in n for n in j["comandos"]["notas"])


def test_la_paginacion_y_el_orden(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/flujos?switch=acc-1&limite=3&pagina=1&orden=prioridad")
    assert c == 200 and len(j["flujos"]) == 3 and j["pagina"] == 1 and j["total"] == 7
    assert [f["prioridad"] for f in j["flujos"]] == sorted((f["prioridad"] for f in j["flujos"]), reverse=True)
    c, j = pedir(base + "/api/flujos?switch=acc-1&limite=3&pagina=9")
    assert c == 200 and j["flujos"] == []
    assert pedir(base + "/api/flujos?switch=acc-1&limite=100000")[1]["limite"] == 500


@pytest.mark.parametrize("consulta", ["switch=acc-1&prioridad=abc", "switch=acc-1&ip_dst=no-es-ip", "switch=acc-1&tabla=x", "switch=acc-1&accion=borrar", "switch=acc-1&limite=abc", "switch=nada", "switch=", ""])
def test_un_pedido_malo_da_400_con_el_motivo_y_no_toca_a_onos(servidor, consulta):
    base, onos = servidor()
    c, j = pedir(base + "/api/flujos?" + consulta)
    assert c == 400 and j["error"]
    if "nada" in consulta or consulta == "" or consulta == "switch=":
        assert "acc-1" in j["switches"] and onos.llamadas == []


def test_dos_consultas_seguidas_leen_a_onos_una_sola_vez(servidor):
    base, onos = servidor()
    for filtro in ("", "&tabla=60", "&app=r2", "&con_paquetes=1"):
        assert pedir(base + "/api/flujos?switch=acc-1" + filtro)[0] == 200
    assert len(onos.llamadas) == 1


def test_si_onos_no_contesta_se_dice_sin_inventar_flujos(servidor):
    base, onos = servidor(onos=Onos(error="timeout"))
    c, j = pedir(base + "/api/flujos?switch=acc-1")
    assert c == 200 and j["flujos"] == [] and "timeout" in j["error"] and not j["antiguo"] and j["total_switch"] == 0


def test_si_no_se_sabe_quien_manda_no_se_lee_de_otro_nodo(servidor):
    base, onos = servidor(maestro="ninguno")
    c, j = pedir(base + "/api/flujos?switch=acc-1")
    assert c == 200 and "no se sabe qué nodo manda" in j["error"] and onos.llamadas == []


def test_solo_el_superadmin_y_el_tunel_local_leen_los_flujos(servidor):
    for ip, esperado in ((IP_DOCENTE, 403), ("10.9.9.9", 403), ("127.0.0.1", 200), (IP_ADMIN, 200)):
        base, _ = servidor(ip=ip)
        assert pedir(base + "/api/flujos?switch=acc-1")[0] == esperado, ip
        assert pedir(base + "/api/switches")[0] == esperado, ip


def test_sin_lector_la_ruta_no_existe(servidor):
    base, _ = servidor(con_lector=False)
    assert pedir(base + "/api/flujos?switch=acc-1")[0] == 404 and pedir(base + "/api/switches")[0] == 200


def test_la_linea_de_comandos_lee_la_configuracion_de_las_vm(tmp_path, monkeypatch):
    capturado = {}

    def falso(cfg, h, **k):
        capturado["cfg"] = cfg
        raise SystemExit(0)
    monkeypatch.setattr(p, "Recolector", falso)
    with pytest.raises(SystemExit):
        p.main(["--onos", "ctrl1=http://c1", "--mapper", "http://m", "--db", str(tmp_path / "p.sqlite"), "--vm-switch", "core-1=sw-core-1:5813,borde=switch-borde:"])
    assert p._pares("core-1=sw-core-1:5813,borde=switch-borde:") == {"core-1": "sw-core-1:5813", "borde": "switch-borde:"}


def test_la_pagina_de_flujos_no_construye_html_con_datos_ajenos():
    js = open(os.path.join(RAIZ, "static", "app.js"), encoding="utf-8").read()
    assert "pintarFlujos" in js and "innerHTML" not in js and "navigator.clipboard" in js
    html = open(os.path.join(RAIZ, "static", "index.html"), encoding="utf-8").read()
    for campo in ("tabla", "prioridad", "app", "estado", "in_port", "eth_src", "eth_dst", "ip_dst", "ip_dst_cubre", "tcp_dst", "accion", "con_paquetes", "texto"):
        assert f'id="fl-{campo}"' in html, campo
