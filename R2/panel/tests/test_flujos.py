"""Flujos de un switch en el panel (flujos.py): normalizar lo que devuelve ONOS, explicar cada regla, filtrar y armar los comandos de ovs-ofctl equivalentes; y la lectura con caché del maestro."""
import os
import re
import sys
import threading
import time

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
import flujos as fl  # noqa: E402

ACC1 = "of:0000e6925d87354b"
GW = "02:00:5E:00:00:FE"
COOKIE_VIG = 0xb30000d9c13347          # el cookie que se vio en el borde: app 0xb3, flujo 0x0000d9c13347


def _f(tabla, prio, criterios, instr, app="org.campus.vigilante", id_=None, estado="ADDED", paquetes=0, **kw):
    d = {"id": str(id_ if id_ is not None else COOKIE_VIG), "appId": app, "deviceId": ACC1, "tableId": tabla, "priority": prio, "state": estado, "packets": paquetes, "bytes": paquetes * 90, "life": 120,
         "isPermanent": True, "selector": {"criteria": criterios}, "treatment": {"instructions": instr}}
    d.update(kw)
    return d


SESION = _f(0, 40001, [{"type": "IN_PORT", "port": 1}, {"type": "ETH_SRC", "mac": "FA:16:3E:71:08:66"}],
            [{"type": "L2MODIFICATION", "subtype": "VLAN_PUSH", "ethernetType": "0x8100"}, {"type": "L2MODIFICATION", "subtype": "VLAN_ID", "vlanId": 20}, {"type": "TABLE", "tableId": 20}],
            app="org.campus.r2", id_=0x9e000000000001, paquetes=7)
ROL = _f(20, 30000, [{"type": "VLAN_VID", "vlanId": 20}, {"type": "ETH_TYPE", "ethType": "0x800"}, {"type": "IP_PROTO", "protocol": 6}, {"type": "IPV4_DST", "ip": "10.0.0.10/32"}, {"type": "TCP_DST", "tcpPort": 8001}],
         [{"type": "TABLE", "tableId": 60}], app="org.campus.r2", id_=0x9e000000000002, paquetes=3)
DROP_ROL = _f(20, 0, [], [], app="org.campus.r2", id_=0x9e000000000003)
ENTREGA = _f(60, 160, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": GW}, {"type": "IPV4_DST", "ip": "10.0.1.100/32"}],
             [{"type": "L2MODIFICATION", "subtype": "ETH_SRC", "mac": GW}, {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": "fa:16:3e:b6:96:08"}, {"type": "L3MODIFICATION", "subtype": "DEC_TTL"},
              {"type": "OUTPUT", "port": "1"}], paquetes=40)
RUTA = _f(60, 150, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": GW}, {"type": "IPV4_DST", "ip": "10.0.2.0/24"}], [{"type": "L3MODIFICATION", "subtype": "DEC_TTL"}, {"type": "OUTPUT", "port": "2"}])
BLOQUEO = _f(60, 130, [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": GW}, {"type": "IPV4_DST", "ip": "10.0.0.0/8"}], [])
DHCP = _f(0, 40510, [{"type": "IN_PORT", "port": 1}, {"type": "ETH_TYPE", "ethType": "0x800"}, {"type": "IP_PROTO", "protocol": 17}, {"type": "UDP_SRC", "udpPort": 68}, {"type": "UDP_DST", "udpPort": 67}],
          [{"type": "METER", "meterId": 3}, {"type": "OUTPUT", "port": "CONTROLLER"}], app="org.campus.meters", id_=0xb9000000000007, estado="PENDING_ADD", paquetes=2)
DESCARTE = _f(60, 1, [], [])
TODOS = [SESION, ROL, DROP_ROL, ENTREGA, RUTA, BLOQUEO, DHCP, DESCARTE]
N = [fl.normalizar(x) for x in TODOS]
POR = {(x["tabla"], x["prioridad"], x["app"]): x for x in N}


# ---------- normalizar ----------
def test_un_flujo_de_sesion_queda_aplanado_con_sus_campos_y_acciones():
    x = fl.normalizar(SESION)
    assert x["coincide"] == {"in_port": "1", "eth_src": "fa:16:3e:71:08:66"} and x["tabla"] == 0 and x["prioridad"] == 40001 and x["paquetes"] == 7
    assert x["acciones"] == ["modifica", "modifica", "goto"] and x["acciones_texto"] == "poner etiqueta VLAN; VLAN = 20; ir a la tabla 20"


def test_los_valores_se_normalizan_ethtype_con_ceros_ip_con_mascara_y_mac_en_minusculas():
    x = fl.normalizar(ROL)
    assert x["coincide"]["eth_type"] == "0x0800" and x["coincide"]["ip_dst"] == "10.0.0.10/32" and x["coincide"]["tcp_dst"] == "8001" and x["coincide"]["vlan"] == "20"
    assert fl.normalizar(ENTREGA)["coincide"]["eth_dst"] == GW.lower()
    assert fl.normalizar(_f(60, 1, [{"type": "IPV4_DST", "ip": "10.0.0.5"}], []))["coincide"]["ip_dst"] == "10.0.0.5/32"


def test_sin_instrucciones_es_un_descarte_y_la_salida_al_controlador_se_distingue_de_un_puerto():
    assert fl.normalizar(DESCARTE)["acciones"] == ["descarte"] and fl.normalizar(DESCARTE)["coincide_texto"] == "cualquiera"
    d = fl.normalizar(DHCP)
    assert d["acciones"] == ["meter", "controlador"] and d["estado"] == "PENDING_ADD"
    assert fl.normalizar(ENTREGA)["acciones"][-1] == "salida" and "decrementar TTL" in fl.normalizar(ENTREGA)["acciones_texto"]


def test_un_criterio_desconocido_no_rompe_y_se_marca():
    x = fl.normalizar(_f(0, 5, [{"type": "SCTP_SRC", "sctpPort": 9}], [{"type": "OUTPUT", "port": "1"}]))
    assert x["coincide"] == {"sctp_src": "?"}


def test_datos_incompletos_no_rompen():
    x = fl.normalizar({"tableId": 3, "priority": 9})
    assert x["tabla"] == 3 and x["paquetes"] == 0 and x["acciones"] == ["descarte"] and x["app"] == ""


# ---------- explicar ----------
def test_cada_regla_conocida_trae_su_explicacion():
    assert "Sesión 802.1X" in POR[(0, 40001, "org.campus.r2")]["explicacion"]
    assert "DHCP por puerto" in POR[(0, 40510, "org.campus.meters")]["explicacion"]
    assert "entrega a un host" in POR[(60, 160, "org.campus.vigilante")]["explicacion"]
    assert "IP privada sin ruta" in POR[(60, 130, "org.campus.vigilante")]["explicacion"]
    assert "ruta por prefijo" in POR[(60, 150, "org.campus.vigilante")]["explicacion"]


def test_las_tablas_de_rol_dicen_de_que_rol_son():
    assert "rol alumno" in POR[(20, 30000, "org.campus.r2")]["explicacion"] and "rol alumno" in POR[(20, 0, "org.campus.r2")]["explicacion"]
    assert "rol docente" in fl.explicar(10, 30000) and "rol cuarentena" in fl.explicar(99, 0)


def test_lo_que_no_esta_en_el_catalogo_lo_dice_en_vez_de_inventar():
    assert fl.explicar(0, 12345).startswith("Sin descripción") and fl.explicar(0, 1).startswith("Sin descripción")        # el 1 de la tabla 0 no es el descarte de la 60


def test_el_catalogo_cubre_todas_las_prioridades_del_mapa_de_r5():
    """Si alguien añade una prioridad a R5/deploy/prioridades.yaml y no al catálogo del panel, esta prueba lo dice."""
    texto = open(os.path.join(RAIZ, "..", "..", "R5", "deploy", "prioridades.yaml"), encoding="utf-8").read()
    prioridades = {int(m.group(2)) for m in re.finditer(r"^\s+(\w+):\s+(\d+)\s", texto, re.M) if m.group(1) not in ("tabla_cuarentena",)}
    prioridades -= {int(v) for v in re.findall(r"(?:docente|alumno|soporte|invitado):\s*(\d+)", texto)}                      # las tablas de rol (10, 20, 40, 50) no son prioridades
    catalogo = {p for _, p, _ in fl.CATALOGO}
    assert prioridades and not (prioridades - catalogo), sorted(prioridades - catalogo)


# ---------- filtrar ----------
def ids(lista):
    return [(x["tabla"], x["prioridad"]) for x in lista]


def test_filtra_por_tabla_prioridad_exacta_rango_y_extremos_abiertos():
    assert ids(fl.filtrar(N, {"tabla": "60"})) == [(60, 160), (60, 150), (60, 130), (60, 1)] or sorted(ids(fl.filtrar(N, {"tabla": "60"}))) == [(60, 1), (60, 130), (60, 150), (60, 160)]
    assert {t for t, _ in ids(fl.filtrar(N, {"tabla": "0,20"}))} == {0, 20}
    assert ids(fl.filtrar(N, {"prioridad": "160"})) == [(60, 160)]
    assert sorted(ids(fl.filtrar(N, {"prioridad": "130-160"}))) == [(60, 130), (60, 150), (60, 160)]
    assert all(p >= 40000 for _, p in ids(fl.filtrar(N, {"prioridad": "40000-"}))) and all(p <= 1 for _, p in ids(fl.filtrar(N, {"prioridad": "-1"})))


def test_filtra_por_app_estado_acciones_y_paquetes():
    assert {x["app"] for x in fl.filtrar(N, {"app": "campus.r2"})} == {"org.campus.r2"}
    assert ids(fl.filtrar(N, {"estado": "pending_add"})) == [(0, 40510)]
    assert ids(fl.filtrar(N, {"accion": "controlador"})) == [(0, 40510)]
    assert ids(fl.filtrar(N, {"accion": "meter"})) == [(0, 40510)]
    assert len(fl.filtrar(N, {"accion": "descarte"})) == 3
    assert {x["paquetes"] > 0 for x in fl.filtrar(N, {"con_paquetes": "1"})} == {True} and len(fl.filtrar(N, {"con_paquetes": "1"})) == 4


def test_filtra_por_campos_de_coincidencia_con_las_mismas_equivalencias_que_ovs():
    assert ids(fl.filtrar(N, {"in_port": "1"})) == [(0, 40001), (0, 40510)]
    assert ids(fl.filtrar(N, {"eth_src": "FA-16-3E-71-08-66"})) == [(0, 40001)]
    assert ids(fl.filtrar(N, {"eth_type": "0x800", "tabla": "20"})) == [(20, 30000)]
    assert ids(fl.filtrar(N, {"tcp_dst": "8001"})) == [(20, 30000)] and ids(fl.filtrar(N, {"udp_dst": "67"})) == [(0, 40510)]
    assert ids(fl.filtrar(N, {"vlan": "20"})) == [(20, 30000)] and ids(fl.filtrar(N, {"ip_proto": "17"})) == [(0, 40510)]


def test_ip_dst_filtra_como_ovs_contenido_y_ip_dst_cubre_pregunta_que_reglas_la_contienen():
    assert ids(fl.filtrar(N, {"ip_dst": "10.0.0.0/8"})) == [(20, 30000), (60, 160), (60, 150), (60, 130)] or sorted(ids(fl.filtrar(N, {"ip_dst": "10.0.0.0/8"}))) == [(20, 30000), (60, 130), (60, 150), (60, 160)]
    assert ids(fl.filtrar(N, {"ip_dst": "10.0.1.100"})) == [(60, 160)]                                  # exacta: no salen las rutas por prefijo que la contienen
    assert sorted(ids(fl.filtrar(N, {"ip_dst_cubre": "10.0.2.77"}))) == [(60, 130), (60, 150)]           # la ruta 10.0.2.0/24 y el bloqueo 10/8
    assert fl.filtrar(N, {"ip_dst": "172.16.0.0/12"}) == [] and fl.filtrar(N, {"ip_dst_cubre": "172.16.0.9"}) == []


def test_los_filtros_se_combinan_con_y_y_los_vacios_se_ignoran():
    assert ids(fl.filtrar(N, {"tabla": "60", "accion": "salida", "con_paquetes": "1", "app": "", "estado": " "})) == [(60, 160)]
    assert len(fl.filtrar(N, {})) == len(N) and len(fl.filtrar(N, {"tabla": "", "texto": None})) == len(N)


def test_el_texto_busca_en_los_campos_las_acciones_la_explicacion_y_la_app():
    assert ids(fl.filtrar(N, {"texto": "meters"})) == [(0, 40510)]
    assert ids(fl.filtrar(N, {"texto": "TTL", "tabla": "60"})) == [(60, 160), (60, 150)] or len(fl.filtrar(N, {"texto": "ttl", "tabla": "60"})) == 2
    assert ids(fl.filtrar(N, {"texto": "reescribe"})) == [(60, 160)]


@pytest.mark.parametrize("filtro", [{"prioridad": "abc"}, {"prioridad": "1-2-3"}, {"tabla": "x"}, {"ip_dst": "no-es-ip"}, {"ip_src": "10.0.0.0/99"}, {"ip_dst_cubre": "10.0.0.0/8"}, {"accion": "borrar"}])
def test_un_filtro_mal_escrito_se_avisa_no_se_ignora_en_silencio(filtro):
    with pytest.raises(fl.FiltroInvalido):
        fl.filtrar(N, filtro)


def test_un_valor_raro_no_provoca_nada_peor_que_no_encontrar():
    assert fl.filtrar(N, {"in_port": "'; rm -rf /", "texto": "<script>"}) == []


def test_ordenar_por_tabla_prioridad_paquetes_o_app():
    assert ids(fl.ordenar(N, "tabla"))[:2] == [(0, 40510), (0, 40001)]
    assert ids(fl.ordenar(N, "prioridad"))[0] == (0, 40510)
    assert fl.ordenar(N, "paquetes")[0]["paquetes"] == 40 and fl.ordenar(N, "app")[0]["app"] == "org.campus.meters"
    assert fl.ordenar(N, "otra") == fl.ordenar(N, "tabla")


def test_las_cuentas_para_los_desplegables():
    c = fl.cuentas(N)
    assert c["tablas"] == {0: 2, 20: 2, 60: 4} and c["apps"]["org.campus.r2"] == 3 and c["estados"] == {"ADDED": 7, "PENDING_ADD": 1}


# ---------- cookies ----------
def test_el_cookie_de_ovs_sale_del_id_de_onos_y_solo_si_es_coherente():
    assert 0xb30000d9c13347 == int(str(COOKIE_VIG))
    c = fl.cookies_por_app(TODOS)
    assert c["org.campus.vigilante"] == "0xb3000000000000" and c["org.campus.r2"] == "0x9e000000000000" and c["org.campus.meters"] == "0xb9000000000000"
    mezclado = [_f(60, 1, [], [], id_=0xb30000000001), _f(60, 2, [], [], id_=0xb40000000001)]
    assert "org.campus.vigilante" not in fl.cookies_por_app(mezclado) and fl.cookies_por_app([{"id": "x", "appId": "a"}]) == {}


# ---------- comandos de ovs-ofctl ----------
def test_el_comando_basico_filtra_por_tabla_y_campos_con_la_sintaxis_de_ovs():
    c = fl.comandos_ovs({"tabla": "60", "ip_dst": "10.0.1.100"})
    assert c["principal"] == 'sudo ovs-ofctl -O OpenFlow13 dump-flows br0 "table=60,ip,nw_dst=10.0.1.100"'
    assert fl.comandos_ovs({})["principal"] == "sudo ovs-ofctl -O OpenFlow13 dump-flows br0"


def test_tcp_udp_arp_y_vlan_usan_los_atajos_y_los_nombres_de_ovs():
    c = fl.comandos_ovs({"tabla": "20", "vlan": "20", "ip_dst": "10.0.0.10/32", "tcp_dst": "8001", "in_port": "1", "eth_src": "FA-16-3E-71-08-66"})["principal"]
    assert c == 'sudo ovs-ofctl -O OpenFlow13 dump-flows br0 "table=20,tcp,in_port=1,dl_src=FA:16:3E:71:08:66,nw_dst=10.0.0.10/32,tcp_dst=8001,dl_vlan=20"'
    assert '"table=0,udp,in_port=1,udp_src=68,udp_dst=67"' in fl.comandos_ovs({"tabla": "0", "udp_src": "68", "udp_dst": "67", "in_port": "1"})["principal"]
    assert '"arp,arp_op=1"' in fl.comandos_ovs({"eth_type": "0x0806", "arp_op": "1"})["principal"]
    assert '"dl_type=0x88cc"' in fl.comandos_ovs({"eth_type": "0x88cc"})["principal"]


def test_la_prioridad_el_estado_de_paquetes_y_las_acciones_se_aplican_con_grep_y_awk():
    assert fl.comandos_ovs({"tabla": "60", "prioridad": "160"})["principal"].endswith('| grep -E "priority=160[, ]"')
    r = fl.comandos_ovs({"prioridad": "100-200"})["principal"]
    assert "awk -F'priority='" in r and "p[1]>=100 && p[1]<=200" in r
    assert "p[1]>=40000)" in fl.comandos_ovs({"prioridad": "40000-"})["principal"] and "p[1]<=1)" in fl.comandos_ovs({"prioridad": "-1"})["principal"]
    assert fl.comandos_ovs({"con_paquetes": "1"})["principal"].endswith('| grep -v "n_packets=0,"')
    assert fl.comandos_ovs({"accion": "goto"})["principal"].endswith('| grep -E "goto_table"') and fl.comandos_ovs({"accion": "descarte"})["principal"].endswith('| grep -E "actions=drop"')
    assert fl.comandos_ovs({"texto": "gateway"})["principal"].endswith("| grep -i 'gateway'")


def test_varias_tablas_se_recorren_en_un_bucle_y_la_app_se_filtra_por_cookie_si_se_conoce():
    c = fl.comandos_ovs({"tabla": "0,60", "ip_dst": "10.0.0.10"})
    assert c["principal"].startswith("for t in 0 60; do echo") and '"table=$t,ip,nw_dst=10.0.0.10"' in c["principal"] and c["tablas"] == ["0", "60"]
    k = fl.comandos_ovs({"tabla": "60", "app": "vigilante"}, cookie_app="0xb3000000000000")
    assert 'cookie=0xb3000000000000/0xffff000000000000"' in k["principal"]
    sin = fl.comandos_ovs({"app": "r2"})
    assert "cookie" not in sin["principal"] and any("cookie" in n for n in sin["notas"])


def test_el_comando_de_traza_aparece_con_un_puerto_de_entrada_y_un_destino():
    t = fl.comandos_ovs({"in_port": "1", "eth_src": "fa:16:3e:71:08:66", "eth_dst": GW, "ip_src": "10.0.2.100", "ip_dst": "10.0.0.10", "tcp_dst": "8001"})["traza"]
    assert t == "sudo ovs-appctl ofproto/trace br0 'in_port=1,dl_src=fa:16:3e:71:08:66,dl_dst=02:00:5E:00:00:FE,tcp,nw_src=10.0.2.100,nw_dst=10.0.0.10,tcp_dst=8001'"
    assert fl.comandos_ovs({"in_port": "1", "ip_dst_cubre": "10.0.2.77"})["traza"].endswith("nw_dst=10.0.2.77'")
    assert fl.comandos_ovs({"ip_dst": "10.0.0.10"})["traza"] is None
    assert any("in_port" in n for n in fl.comandos_ovs({"ip_dst_cubre": "10.0.2.77"})["notas"])


def test_las_notas_explican_lo_que_ovs_no_puede_filtrar():
    n = " ".join(fl.comandos_ovs({"estado": "ADDED", "ip_dst": "10.0.0.10", "ip_dst_cubre": "10.0.2.77"})["notas"])
    assert "ONOS" in n and "CONTENIDO" in n and "Cubre" in n


@pytest.mark.parametrize("valor", ['"; rm -rf /; echo "', "$(reboot)", "`id`", "a b", "x|y", "a;b", "'", "a\nb"])
def test_ningun_valor_de_un_filtro_puede_colar_un_comando(valor):
    for campo in ("in_port", "eth_src", "eth_dst", "ip_src", "ip_dst", "tcp_dst", "vlan", "prioridad", "eth_type", "ip_proto"):
        r = fl.comandos_ovs({campo: valor, "tabla": "60"})
        assert r["principal"] == 'sudo ovs-ofctl -O OpenFlow13 dump-flows br0 "table=60"', (campo, r["principal"])
        assert any("no se admiten" in n for n in r["notas"])
    t = fl.comandos_ovs({"texto": valor})
    assert t["principal"].count("|") <= 1 and "rm -rf" not in t["principal"] and "$(" not in t["principal"] and "`" not in t["principal"]
    assert fl.comandos_ovs({"tabla": valor})["principal"] == "sudo ovs-ofctl -O OpenFlow13 dump-flows br0"
    assert "rm -rf" not in fl.comandos_ovs({"tabla": "60"}, cookie_app=valor)["principal"] and "cookie" not in fl.comandos_ovs({"tabla": "60"}, cookie_app=valor)["principal"]


# ---------- lectura de ONOS ----------
class Red:
    def __init__(self, respuesta=None, error=None):
        self.llamadas, self.respuesta, self.error, self.t = [], respuesta if respuesta is not None else {"flows": [SESION, ROL]}, error, 1000.0
        self.espera = 0

    def leer(self, url, **kw):
        self.llamadas.append((url, kw.get("timeout")))
        if self.espera:
            time.sleep(self.espera)
        return (None, self.error) if self.error else (self.respuesta, None)

    def ahora(self):
        return self.t


def lector(red, **kw):
    return fl.LectorFlujos({"ctrl1": "http://c1", "ctrl2": "http://c2", "ctrl3": "http://c3"}, red.leer, ahora=red.ahora, usuario="onos", clave="rocks", **kw)


def test_se_lee_solo_del_nodo_maestro_con_un_timeout_largo_y_se_devuelven_los_flujos_crudos():
    red = Red()
    r = lector(red).obtener(ACC1, "ctrl2")
    assert red.llamadas == [(f"http://c2/onos/v1/flows/{ACC1}", 16.0)] and r["nodo"] == "ctrl2" and len(r["flujos"]) == 2 and not r["antiguo"] and r["error"] is None


def test_la_caché_evita_repetir_lecturas_y_forzar_no_baja_del_minimo():
    red = Red()
    lec = lector(red)
    lec.obtener(ACC1, "ctrl2")
    red.t += 5
    assert lec.obtener(ACC1, "ctrl2")["desde_cache"] and len(red.llamadas) == 1                      # 5 s: dentro del TTL de 10
    red.t += 1
    lec.obtener(ACC1, "ctrl2", forzar=True)
    assert len(red.llamadas) == 2                                                                      # 6 s: forzar sí lee (pasó el mínimo de 3)
    lec.obtener(ACC1, "ctrl2", forzar=True)
    assert len(red.llamadas) == 2                                                                      # recién leído: forzar no puede machacar a ONOS
    red.t += 11
    lec.obtener(ACC1, "ctrl2")
    assert len(red.llamadas) == 3                                                                      # pasó el TTL


def test_sin_maestro_conocido_no_se_lee_de_otro_nodo():
    red = Red()
    for maestro in (None, "ctrl9", ""):
        r = lector(red).obtener(ACC1, maestro)
        assert r["flujos"] == [] and "no se sabe qué nodo manda" in r["error"]
    assert red.llamadas == []


def test_si_la_lectura_falla_se_enseña_la_anterior_marcada_como_antigua():
    red = Red()
    lec = lector(red)
    lec.obtener(ACC1, "ctrl2")
    red.t += 30
    red.error = "timeout"
    r = lec.obtener(ACC1, "ctrl2")
    assert r["antiguo"] and r["error"] == "ONOS ctrl2: timeout" and len(r["flujos"]) == 2
    sin_previo = lector(Red(error="HTTP 500")).obtener(ACC1, "ctrl1")
    assert sin_previo["flujos"] == [] and "HTTP 500" in sin_previo["error"] and not sin_previo["antiguo"]


def test_un_identificador_raro_nunca_llega_a_la_url():
    red = Red()
    for malo in ("of:123", "../../x", "of:0000e6925d87354b/../x", "", None, "of:0000E6925D87354B"):
        assert "inválido" in lector(red).obtener(malo, "ctrl1")["error"]
    assert red.llamadas == []


def test_una_respuesta_rara_de_onos_no_rompe():
    r = lector(Red(respuesta=["no", "es", "un", "dict"])).obtener(ACC1, "ctrl1")
    assert r["flujos"] == [] and "inesperada" in r["error"]


def test_dos_peticiones_a_la_vez_para_el_mismo_switch_leen_a_onos_una_sola_vez():
    red = Red()
    red.espera = 0.3
    lec = lector(red)
    res = []
    hilos = [threading.Thread(target=lambda: res.append(lec.obtener(ACC1, "ctrl2"))) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(red.llamadas) == 1 and len(res) == 4 and all(len(r["flujos"]) == 2 for r in res)
