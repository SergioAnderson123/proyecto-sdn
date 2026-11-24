"""La configuración estática y las políticas en el panel: carga de la foto, comparación política-frente-a-instalado y los endpoints (`/api/config`, `/api/politica`, `/api/politica/comparar`)."""
import copy
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
sys.path.insert(0, os.path.join(RAIZ, "..", "policy"))
import configuracion as cf  # noqa: E402
import datos as d  # noqa: E402
import exportar_config as ex  # noqa: E402
import flujos as fl  # noqa: E402
import panel as p  # noqa: E402
from test_exportar_config import ACC1, LOCAL, POLICY, carpeta, copiar, poner  # noqa: E402,F401
from test_panel import AHORA, CFG, IP_ADMIN, IP_DOCENTE, Fuentes, _estado_vig  # noqa: E402
from test_panel_flujos import BORDE, VM, Onos, _devs, _vig  # noqa: E402


def _foto(carpeta, ruta):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(ex.exportar(carpeta, ahora=AHORA - 30), f, ensure_ascii=False)
    return ruta


def _crudos_de_r2(dpid):
    """Los flujos de R2 de un switch tal como los devolvería ONOS (con appId y estado), calculados con el mismo código y los mismos archivos que la foto."""
    import policy_tables as pt
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        copiar(t, ".", "policy.yaml", POLICY)
        poner(t, ".", "policy.local.yaml", LOCAL)
        pol = pt.cargar_politica(os.path.join(t, "policy.yaml"))
    return [dict(f, appId="org.campus.r2", id=str((0x9e << 48) + i), state="ADDED", packets=0, life=10) for i, f in enumerate(pt.flujos_dispositivo(pol, dpid), 1)]


# ---------- la carga de la foto ----------
def test_sin_archivo_se_dice_que_no_hay_foto_todavia_y_no_falla(tmp_path):
    c = cf.ConfigEstatica(str(tmp_path / "no-existe.json"))
    e = c.estado()
    assert not e["disponible"] and "panel_config.yml" in e["error"] and c.archivos() == [] and c.archivo("policy.yaml") is None and c.politica() is None
    assert not cf.ConfigEstatica(None).estado()["disponible"] and "--config-estatica" in cf.ConfigEstatica(None).estado()["error"]


def test_la_foto_se_carga_sin_los_contenidos_en_la_lista_y_con_ellos_por_archivo(carpeta, tmp_path):
    c = cf.ConfigEstatica(_foto(carpeta, str(tmp_path / "config.json")), reloj=lambda: AHORA)
    assert c.estado() == {"disponible": True, "generado": AHORA - 30, "edad_s": 30, "error": None}
    lista = c.archivos()
    assert [a["id"] for a in lista][:2] == ["policy.yaml", "policy.local.yaml"] and all("contenidos" not in a and "diferencias" not in a for a in lista)
    assert c.archivo("policy.yaml")["contenidos"] and c.archivo("no.yaml") is None
    assert "esperados" not in c.politica() and "esperados" in c.politica(con_esperados=True)


def test_se_vuelve_a_leer_sola_cuando_el_archivo_cambia_y_conserva_la_ultima_buena_si_se_rompe(carpeta, tmp_path):
    ruta = _foto(carpeta, str(tmp_path / "config.json"))
    c = cf.ConfigEstatica(ruta)
    assert c.estado()["generado"] == AHORA - 30
    nuevo = ex.exportar(carpeta, ahora=AHORA + 100)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(nuevo, f)
    assert c.estado()["generado"] == AHORA + 100
    with open(ruta, "w", encoding="utf-8") as f:
        f.write('{"archivos": [ esto no es json')
    e = c.estado()
    assert e["disponible"] and e["generado"] == AHORA + 100 and "no se pudo leer" in e["error"] and c.archivos()                    # sigue sirviendo la última foto buena
    with open(ruta, "w", encoding="utf-8") as f:
        f.write('["no", "tiene", "la", "forma"]')
    assert "forma esperada" in c.estado()["error"] and c.estado()["disponible"]


# ---------- comparar lo esperado con lo instalado ----------
def _n(crudos):
    return [fl.normalizar(x) for x in crudos]


def test_si_lo_instalado_es_exactamente_lo_que_la_politica_pide_coincide_todo(carpeta):
    esperados = ex.exportar(carpeta)["politica"]["esperados"][ACC1]["flujos"]
    r = fl.comparar(esperados, _n(_crudos_de_r2(ACC1)))
    assert r["faltan"] == [] and r["sobran"] == [] and r["coinciden"] == r["esperados"] == r["instalados"] > 20


def test_un_flujo_que_falta_y_uno_que_sobra_se_distinguen(carpeta):
    esperados = ex.exportar(carpeta)["politica"]["esperados"][ACC1]["flujos"]
    crudos = _crudos_de_r2(ACC1)
    quitado = next(f for f in crudos if f["tableId"] == 20 and f["priority"] == 0)
    crudos.remove(quitado)
    crudos.append({"id": "1", "appId": "org.campus.r2", "tableId": 20, "priority": 30000, "state": "ADDED", "selector": {"criteria": [{"type": "TCP_DST", "tcpPort": 9999}]},
                   "treatment": {"instructions": [{"type": "TABLE", "tableId": 60}]}})
    r = fl.comparar(esperados, _n(crudos))
    assert [(x["tabla"], x["prioridad"]) for x in r["faltan"]] == [(20, 0)] and [(x["tabla"], x["prioridad"], x["coincide"]["tcp_dst"]) for x in r["sobran"]] == [(20, 30000, "9999")]
    assert r["coinciden"] == r["esperados"] - 1


def test_los_duplicados_se_cuentan_como_multiconjunto():
    x = {"tabla": 0, "prioridad": 5, "coincide_texto": "in_port=1", "acciones_texto": "descartar"}
    assert fl.comparar([x, x], [x])["faltan"] == [x] and fl.comparar([x], [x, x])["sobran"] == [x] and fl.comparar([x, x], [x, x])["coinciden"] == 2
    assert fl.comparar([], []) == {"esperados": 0, "instalados": 0, "coinciden": 0, "faltan": [], "sobran": []}


# ---------- los endpoints ----------
@pytest.fixture()
def servidor(carpeta, tmp_path):
    servidores = []

    def _levantar(ip=IP_ADMIN, flujos_onos=None, con_foto=True, maestro="ctrl2"):
        ruta = _foto(carpeta, str(tmp_path / "config.json")) if con_foto else str(tmp_path / "no-hay.json")
        rec = p.Recolector(CFG, d.Historial(), leer=Fuentes(**_devs(maestro), **_vig()), reloj=lambda: AHORA)
        rec.ciclo()
        onos = Onos(flujos=flujos_onos if flujos_onos is not None else _crudos_de_r2(ACC1))
        lector = fl.LectorFlujos(CFG["onos"], onos, ahora=lambda: AHORA)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), p.hacer_manejador(rec, d.Historial(), ["superadmin"], reloj=lambda: AHORA, ip_de=lambda m: ip, lector=lector, vm_switch=VM,
                                                                      config=cf.ConfigEstatica(ruta, reloj=lambda: AHORA)))
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


def test_la_lista_de_archivos_dice_el_estado_de_cada_uno(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/config")
    assert c == 200 and j["disponible"] and j["edad_s"] == 30
    por = {a["id"]: a for a in j["archivos"]}
    assert por["policy.yaml"]["estado"] == "igual" and por["policy.yaml"]["faltan"] == ["ctrl-3"] and "contenidos" not in por["policy.yaml"]
    assert set(por) == {"policy.yaml", "policy.local.yaml", "plan_capa3.json", "hosts_fijos_capa3.json", "vigilante-capa3.service", "prioridades.yaml"}


def test_un_archivo_trae_su_contenido_y_uno_desconocido_da_404(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/config/archivo?id=hosts_fijos_capa3.json")
    assert c == 200 and "10.0.0.10" in next(iter(j["archivo"]["contenidos"].values())) and j["archivo"]["copias"][0]["fuente"] == "repo"
    assert pedir(base + "/api/config/archivo?id=../../etc/passwd")[0] == 404 and pedir(base + "/api/config/archivo")[0] == 404


def test_la_politica_llega_interpretada_sin_la_lista_pesada_de_esperados(servidor):
    base, _ = servidor()
    c, j = pedir(base + "/api/politica")
    pol = j["politica"]
    assert c == 200 and pol["origen"] == "ctrl-1" and "esperados" not in pol and pol["matriz"]["professor"]["grade_system"] is True and pol["flujos_por_rol"]["alumno"]


def test_sin_foto_los_endpoints_lo_dicen_en_vez_de_fallar(servidor):
    base, _ = servidor(con_foto=False)
    c, j = pedir(base + "/api/config")
    assert c == 200 and not j["disponible"] and j["archivos"] == [] and "panel_config.yml" in j["error"]
    assert pedir(base + "/api/politica")[1]["politica"] is None
    c, j = pedir(base + "/api/politica/comparar?switch=acc-1")
    assert c == 409 and "panel_config.yml" in j["error"]


def test_comparar_con_lo_instalado_da_cero_diferencias_cuando_todo_esta_en_su_sitio(servidor):
    base, onos = servidor()
    c, j = pedir(base + "/api/politica/comparar?switch=acc-1")
    assert c == 200 and j["origen"] == "ctrl-1" and j["nodo"] == "ctrl2" and j["comparacion"]["n_faltan"] == 0 and j["comparacion"]["n_sobran"] == 0 and j["comparacion"]["coinciden"] > 20
    assert onos.llamadas == [f"http://c2/onos/v1/flows/{ACC1}"]


def test_comparar_ignora_los_flujos_de_otras_apps_y_enseña_lo_que_falta(servidor):
    crudos = _crudos_de_r2(ACC1)
    quitado = next(f for f in crudos if f["tableId"] == 0 and f["priority"] == 40500)
    crudos.remove(quitado)
    crudos.append({"id": "7", "appId": "org.campus.vigilante", "tableId": 60, "priority": 160, "state": "ADDED", "selector": {"criteria": []}, "treatment": {"instructions": []}})
    base, _ = servidor(flujos_onos=crudos)
    c, j = pedir(base + "/api/politica/comparar?switch=acc-1")
    cmp = j["comparacion"]
    assert c == 200 and cmp["n_faltan"] == 1 and cmp["n_sobran"] == 0 and cmp["faltan"][0]["prioridad"] == 40500 and "DHCP" in cmp["faltan"][0]["explicacion"]


def test_comparar_un_switch_que_la_politica_no_conoce_o_un_nombre_malo(servidor):
    base, _ = servidor()
    assert pedir(base + "/api/politica/comparar?switch=nada")[0] == 400
    c, j = pedir(base + "/api/politica/comparar?switch=borde")
    assert c == 200 and j["comparacion"] is not None                                   # el borde sí figura en la política (5 switches); sus flujos de R2 son otros que los de acc-1
    assert j["comparacion"]["n_faltan"] > 0                                            # el ONOS de prueba devuelve los flujos de acc-1: al borde le faltan los suyos


def test_si_onos_no_contesta_la_comparacion_lo_dice_sin_inventar(servidor):
    base, onos = servidor()
    onos.error = "timeout"
    c, j = pedir(base + "/api/politica/comparar?switch=acc-1")
    assert c == 200 and j["comparacion"] is None and "timeout" in j["error"]


def test_las_rutas_de_configuracion_piden_el_mismo_rol_que_el_resto(servidor):
    for ip, esperado in ((IP_DOCENTE, 403), ("10.9.9.9", 403), ("127.0.0.1", 200)):
        base, _ = servidor(ip=ip)
        for ruta in ("/api/config", "/api/politica", "/api/config/archivo?id=policy.yaml", "/api/politica/comparar?switch=acc-1"):
            assert pedir(base + ruta)[0] == esperado, (ip, ruta)


def test_la_pagina_trae_las_pestanas_de_politicas_y_configuracion_sin_construir_html_con_datos_ajenos():
    js = open(os.path.join(RAIZ, "static", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(RAIZ, "static", "index.html"), encoding="utf-8").read()
    assert 'data-v="politicas"' in html and 'data-v="config"' in html and 'id="v-politicas"' in html and 'id="v-config"' in html
    assert "pintarPoliticas" in js and "pintarConfig" in js and "innerHTML" not in js


# ---------- lo que se vio al probar en la red real: ONOS devuelve los criterios en otro orden ----------
def test_dos_flujos_iguales_con_los_criterios_en_distinto_orden_son_el_mismo(carpeta):
    esperados = ex.exportar(carpeta)["politica"]["esperados"][ACC1]["flujos"]
    crudos = _crudos_de_r2(ACC1)
    for f in crudos:                                                                 # ONOS devuelve el selector en otro orden: se invierte el de todos
        f["selector"] = {"criteria": list(reversed(f["selector"]["criteria"]))}
        f["treatment"] = {"instructions": f["treatment"]["instructions"]}
    r = fl.comparar(esperados, _n(crudos))
    assert r["faltan"] == [] and r["sobran"] == [] and r["coinciden"] == r["esperados"] > 20


def test_el_texto_de_los_campos_sale_siempre_en_el_mismo_orden():
    a = fl.normalizar({"tableId": 20, "priority": 1, "selector": {"criteria": [{"type": "TCP_DST", "tcpPort": 80}, {"type": "VLAN_VID", "vlanId": 20}, {"type": "ETH_TYPE", "ethType": "0x800"}, {"type": "IN_PORT", "port": 1}]}})
    b = fl.normalizar({"tableId": 20, "priority": 1, "selector": {"criteria": [{"type": "IN_PORT", "port": 1}, {"type": "ETH_TYPE", "ethType": "0x800"}, {"type": "VLAN_VID", "vlanId": 20}, {"type": "TCP_DST", "tcpPort": 80}]}})
    assert a["coincide_texto"] == b["coincide_texto"] == "in_port=1, eth_type=0x0800, vlan=20, tcp_dst=80" and list(a["coincide"]) == ["in_port", "eth_type", "vlan", "tcp_dst"]


def test_el_orden_de_las_acciones_no_cuenta_al_comparar_pero_una_accion_distinta_si():
    base = {"tabla": 20, "prioridad": 30000, "coincide_texto": "vlan=20"}
    a = dict(base, acciones_texto="quitar etiqueta VLAN; ir a la tabla 60")
    b = dict(base, acciones_texto="ir a la tabla 60; quitar etiqueta VLAN")
    c = dict(base, acciones_texto="ir a la tabla 60")
    assert fl.comparar([a], [b])["coinciden"] == 1 and fl.comparar([a], [c])["coinciden"] == 0


def test_la_regla_de_arp_por_defecto_de_onos_ya_tiene_explicacion():
    assert "Núcleo de ONOS" in fl.explicar(0, 5) and fl.explicar(10, 5).startswith("Sin descripción")


def test_onos_devuelve_el_descarte_como_noaction_y_es_lo_mismo_que_una_lista_vacia(carpeta):
    vacio = fl.normalizar({"tableId": 20, "priority": 0, "appId": "org.campus.r2", "selector": {"criteria": []}, "treatment": {"instructions": []}})
    noaction = fl.normalizar({"tableId": 20, "priority": 0, "appId": "org.campus.r2", "selector": {"criteria": []}, "treatment": {"instructions": [{"type": "NOACTION"}]}})
    assert noaction["acciones"] == vacio["acciones"] == ["descarte"] and noaction["acciones_texto"] == vacio["acciones_texto"] == "descartar"
    assert fl.comparar([vacio], [noaction])["coinciden"] == 1
    assert fl.filtrar([noaction], {"accion": "descarte"}) == [noaction]


def test_con_los_cambios_de_la_red_real_la_comparacion_da_cero_diferencias(carpeta):
    """Los tres rasgos que ONOS tiene y mis datos de prueba no tenían: criterios en otro orden, DROP como NOACTION y acciones reordenadas."""
    esperados = ex.exportar(carpeta)["politica"]["esperados"][ACC1]["flujos"]
    crudos = copy.deepcopy(_crudos_de_r2(ACC1))
    for f in crudos:
        f["selector"] = {"criteria": list(reversed(f["selector"]["criteria"]))}
        ins = f["treatment"]["instructions"]
        f["treatment"] = {"instructions": ins if ins else [{"type": "NOACTION"}]}
    r = fl.comparar(esperados, _n(crudos))
    assert r["faltan"] == [] and r["sobran"] == [] and r["coinciden"] == r["esperados"] > 20
