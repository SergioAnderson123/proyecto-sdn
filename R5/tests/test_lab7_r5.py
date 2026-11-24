"""Lab 7 (TEL354): 6 pruebas unitarias de R5 (seguridad perimetral) sobre R5/codigo/integrador.py.

Numeración X.Y.Z del informe: módulo 3 = integrador M09; sección 3.N = prueba R5-0N; Z = caso.
Cada prueba corre sin ONOS ni Suricata (ONOS se sustituye por un doble). Se ejecutan una por una con
R5/pruebas/r5_lab7.sh <1..6> o todas con:  python -m pytest R5/tests/test_lab7_r5.py -v
"""
import importlib
import json
import os
import sys
import time

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "codigo"))
os.environ.update({"ONOS_URL": "http://onos.test", "ONOS_USER": "u", "ONOS_PASS": "p",
                   "HOME_NET": "10.0.0.0/24", "LISTA_BLANCA": "10.0.0.99/32,10.0.0.10/32",
                   "CORE_DEVICE": "of:0000000000000001", "MAX_SEVERITY": "2", "P_R5": "42000", "BLOCK_SEC": "300"})
integ = importlib.import_module("integrador")


class Resp:
    def __init__(self, cuerpo=None, codigo=200):
        self._c, self.status_code, self.text = cuerpo or {}, codigo, json.dumps(cuerpo or {})

    def json(self):
        return self._c


class OnosFalso:
    def __init__(self):
        self.llamadas, self.hosts, self.flujos = [], [], []

    def __call__(self, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta, kw))
        if metodo == "GET" and ruta == "/hosts":
            return Resp({"hosts": self.hosts})
        if metodo == "GET" and ruta.startswith("/flows/application"):
            return Resp({"flows": self.flujos})
        if metodo == "POST" and ruta == "/flows":
            dev = kw["json"]["flows"][0]["deviceId"]
            return Resp({"flows": [{"deviceId": dev, "flowId": "777"}]})
        return Resp()

    def posts(self):
        return [c for c in self.llamadas if c[0] == "POST"]


@pytest.fixture
def onos(monkeypatch):
    falso = OnosFalso()
    monkeypatch.setattr(integ, "onos", falso)
    return falso


def alerta(src, dst, sev=2, direction="to_server", sid=9000002, firma="LOCAL Port scan SYN"):
    return {"event_type": "alert", "timestamp": "2026-11-23T15:22:00.000000+0000", "src_ip": src, "dest_ip": dst,
            "direction": direction, "alert": {"severity": sev, "signature_id": sid, "signature": firma}}


def estado():
    return {"bloqueadas": {}, "inode": None, "offset": 0}


# =====================================================================================================
# R5-01 (3.1.x) parse_ids_alert(line): lectura de la alerta del IDS, válida e inválida.  Criterio: detección del ataque.
# =====================================================================================================
LINEA_VALIDA = json.dumps({
    "timestamp": "2026-11-23T15:22:00.000000+0000", "event_type": "alert", "src_ip": "203.0.113.103", "src_port": 51234,
    "dest_ip": "10.0.0.10", "dest_port": 80, "proto": "TCP", "direction": "to_server",
    "ether": {"src_mac": "AA:BB:CC:00:00:01", "dest_mac": "AA:BB:CC:00:00:02"},
    "alert": {"severity": 2, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}})


def test_R5_01_3_1_1_alerta_valida_devuelve_direcciones_puertos_protocolo_firma_y_severidad():
    a = integ.parse_ids_alert(LINEA_VALIDA)
    assert (a["src_ip"], a["src_port"], a["dest_ip"], a["dest_port"], a["proto"]) == ("203.0.113.103", 51234, "10.0.0.10", 80, "TCP")
    assert (a["sid"], a["firma"], a["severidad"]) == (9000002, "LOCAL Port scan SYN", 2)
    assert a["timestamp"] == "2026-11-23T15:22:00.000000+0000" and a["direction"] == "to_server"
    assert (a["mac_src"], a["mac_dst"]) == ("aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02")        # en minúsculas
    assert a["evento"]["alert"]["signature_id"] == 9000002                                     # el evento original sigue disponible


def test_R5_01_3_1_1b_alerta_sin_puertos_ni_mac_es_valida(onos):
    # ICMP no tiene puertos y el eve-log puede no traer `ether`: los campos opcionales quedan en None y la alerta sigue siendo válida.
    linea = json.dumps({"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "proto": "ICMP",
                        "alert": {"severity": 1, "signature_id": 9000021, "signature": "LOCAL R4 Inundacion ICMP interna"}})
    a = integ.parse_ids_alert(linea)
    assert a["src_port"] is None and a["dest_port"] is None and a["mac_src"] is None and a["mac_dst"] is None


@pytest.mark.parametrize("ev", [
    {"event_type": "alert"},                                                                  # sin bloque alert
    {"event_type": "alert", "alert": {"severity": 1}},                                        # sin src_ip ni dest_ip
    {"event_type": "alert", "src_ip": "x", "dest_ip": "y", "alert": {"severity": 1}},         # IP inválidas
    {"event_type": "alert", "src_ip": "203.0.113.5", "alert": {"severity": 1}},               # sin destino
    {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10"},                 # sin severidad
    {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {}},    # severidad ausente
    {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {"severity": "alta"}},   # severidad no numérica
    {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": "texto"},               # alert no es objeto
])
def test_R5_01_3_1_2_alerta_incompleta_se_descarta_sin_romper(onos, ev):
    assert integ.parse_ids_alert(json.dumps(ev)) is None             # no lanza excepción
    st = estado()
    integ.procesar(ev, st)                                           # y aunque llegara a procesar(), tampoco bloquea
    assert st["bloqueadas"] == {} and onos.posts() == []


@pytest.mark.parametrize("linea", ['{"event_type": "alert", ', "no es json", "", "   ", "[]", "5", "null", '"texto"', None])
def test_R5_01_3_1_3_linea_corrupta_o_que_no_es_un_objeto_se_descarta(linea):
    assert integ.parse_ids_alert(linea) is None


@pytest.mark.parametrize("tipo", ["stats", "flow", "dns", "anomaly", ""])
def test_R5_01_3_1_4_evento_que_no_es_alerta_se_descarta(tipo):
    linea = json.dumps({"event_type": tipo, "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {"severity": 1}})
    assert integ.parse_ids_alert(linea) is None


def test_R5_01_3_1_5_linea_a_medio_escribir_no_se_procesa_ni_avanza_el_offset(onos, tmp_path, monkeypatch):
    eve = tmp_path / "eve-alerts.json"
    monkeypatch.setattr(integ, "EVE_PATH", str(eve))
    completa = LINEA_VALIDA + "\n"
    mitad = json.dumps({"event_type": "alert", "src_ip": "203.0.113.9", "dest_ip": "10.0.0.10",
                        "alert": {"severity": 1, "signature_id": 9000003, "signature": "LOCAL SYN flood"}})
    eve.write_text(completa + mitad[:30], encoding="utf-8", newline="")          # Suricata aún está escribiendo la 2.ª línea
    st = estado()
    assert integ.consumir_eve(st) is False
    assert list(st["bloqueadas"]) == ["203.0.113.103"] and st["offset"] == len(completa.encode())   # solo la completa
    eve.write_text(completa + mitad + "\n", encoding="utf-8", newline="")        # Suricata termina de escribirla
    assert integ.consumir_eve(st) is False
    assert sorted(st["bloqueadas"]) == ["203.0.113.103", "203.0.113.9"]


def test_R5_01_3_1_6_linea_corrupta_en_el_archivo_se_salta_y_las_siguientes_se_procesan(onos, tmp_path, monkeypatch):
    eve = tmp_path / "eve-alerts.json"
    monkeypatch.setattr(integ, "EVE_PATH", str(eve))
    eve.write_text("basura que no es json\n" + LINEA_VALIDA + "\n", encoding="utf-8", newline="")
    st = estado()
    integ.consumir_eve(st)
    assert list(st["bloqueadas"]) == ["203.0.113.103"] and st["offset"] == eve.stat().st_size


# =====================================================================================================
# R5-02 (3.2.x) Decisión según severidad y dirección del tráfico.  Criterio: mitigación.
# =====================================================================================================
@pytest.mark.parametrize("sev,bloquea", [(1, True), (2, True), (3, False), (4, False)])
def test_R5_02_3_2_1_severidad_en_el_limite(onos, sev, bloquea):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.10", sev=sev), st)
    assert ("203.0.113.7" in st["bloqueadas"]) is bloquea      # límite: MAX_SEVERITY = 2


@pytest.mark.parametrize("ev,esperado", [
    (alerta("203.0.113.7", "10.0.0.50"), ("203.0.113.7", "core")),                     # externo -> interno
    (alerta("10.0.0.21", "198.51.100.9"), ("10.0.0.21", "borde")),                     # interno -> externo (C2)
    (alerta("10.0.0.21", "10.0.0.50"), ("10.0.0.21", "borde")),                        # interno <-> interno
    (alerta("10.0.0.50", "203.0.113.7", direction="to_client"), ("203.0.113.7", "core")),   # firma sobre la respuesta
    (alerta("198.51.100.1", "203.0.113.7"), None),                                     # externo <-> externo
])
def test_R5_02_3_2_2_decide_a_quien_y_donde_bloquear(ev, esperado):
    assert integ.decidir(ev) == esperado


def test_R5_02_3_2_3_externo_se_bloquea_en_el_core_e_interno_en_su_borde(onos):
    onos.hosts = [{"ipAddresses": ["10.0.0.21"], "locations": [{"elementId": "of:00000000000000a1"}]}]
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    integ.procesar(alerta("10.0.0.21", "198.51.100.9"), st)
    assert st["bloqueadas"]["203.0.113.7"]["device"] == "of:0000000000000001"
    assert st["bloqueadas"]["10.0.0.21"]["device"] == "of:00000000000000a1"


# =====================================================================================================
# R5-03 (3.3.x) Lista blanca: una IP legítima nunca se bloquea.  Criterio: falsos positivos.
# =====================================================================================================
@pytest.mark.parametrize("src,dst", [("10.0.0.99", "203.0.113.7"), ("10.0.0.10", "198.51.100.9")])
def test_R5_03_3_3_1_ip_en_lista_blanca_no_se_bloquea(onos, src, dst):
    st = estado()
    integ.procesar(alerta(src, dst), st)
    assert st["bloqueadas"] == {} and onos.posts() == []


def test_R5_03_3_3_2_ip_no_listada_se_bloquea_y_la_blanca_vecina_no(onos):
    st = estado()
    integ.procesar(alerta("10.0.0.98", "203.0.113.7"), st)      # vecina de 10.0.0.99, pero no está en la lista
    integ.procesar(alerta("10.0.0.99", "203.0.113.7"), st)
    assert list(st["bloqueadas"]) == ["10.0.0.98"]


def test_R5_03_3_3_3_la_lista_blanca_protege_solo_a_la_ip_que_se_iba_a_bloquear(onos):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.10"), st)      # destino en lista blanca: el atacante sí se bloquea
    assert "203.0.113.7" in st["bloqueadas"]


# =====================================================================================================
# R5-04 (3.4.x) Regla DROP del perímetro: campos, prioridad y timeout.  Criterio: mitigación.
# =====================================================================================================
def test_R5_04_3_4_1_la_regla_descarta_solo_el_origen_con_prioridad_y_timeout():
    f = integ.regla_drop("of:0000000000000001", "203.0.113.7")["flows"][0]
    assert f["priority"] == 42000 and f["timeout"] == 300 and f["isPermanent"] is False
    assert f["treatment"]["instructions"] == []                              # sin instrucciones = DROP
    crit = {(c["type"], c.get("ip") or c.get("ethType")) for c in f["selector"]["criteria"]}
    assert crit == {("ETH_TYPE", "0x0800"), ("IPV4_SRC", "203.0.113.7/32")}   # solo esa IP, no el resto


def test_R5_04_3_4_2_el_atacante_externo_se_bloquea_en_todos_los_cores(monkeypatch, onos):
    monkeypatch.setattr(integ, "CORE_DEVICES", ["of:c1", "of:c2", "of:c3"])
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    flujos = onos.posts()[0][2]["json"]["flows"]
    assert [f["deviceId"] for f in flujos] == ["of:c1", "of:c2", "of:c3"]
    assert onos.posts()[0][2]["params"] == {"appId": "org.campus.r5"}


def test_R5_04_3_4_3_registra_el_incidente_con_tiempos_y_flow_id(onos):
    st = estado()
    t0 = time.time()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st, t_lectura=t0)
    v = st["bloqueadas"]["203.0.113.7"]
    assert v["flow_id"] == "777" and v["donde"] == "core" and v["t_alerta"]
    assert v["t_lectura"] <= v["t_post"] <= v["t_201"] and v["vence"] > v["t_201"]


# =====================================================================================================
# R5-05 (3.5.x) Sin duplicar reglas y liberación (desbloqueo manual).  Criterio: mitigación sin saturar la TCAM.
# =====================================================================================================
def test_R5_05_3_5_1_una_ip_ya_bloqueada_no_genera_otra_regla(onos):
    st = estado()
    for _ in range(3):
        integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    assert len(onos.posts()) == 1


def test_R5_05_3_5_2_reconciliar_olvida_la_ip_cuyo_flujo_ya_no_existe(onos):
    st = estado()
    st["bloqueadas"]["203.0.113.7"] = {"vence": time.time() + 100, "t_201": time.time() - 60}
    onos.flujos = []                                                         # el flujo se borró (r5ctl desbloquear)
    integ.reconciliar(st)
    assert st["bloqueadas"] == {}


def test_R5_05_3_5_3_reconciliar_respeta_el_periodo_de_gracia_y_los_flujos_vigentes(onos):
    st = estado()
    st["bloqueadas"]["203.0.113.7"] = {"vence": time.time() + 100, "t_201": time.time()}            # recién creada
    st["bloqueadas"]["203.0.113.8"] = {"vence": time.time() + 100, "t_201": time.time() - 60}       # vigente
    onos.flujos = [{"selector": {"criteria": [{"type": "IPV4_SRC", "ip": "203.0.113.8/32"}]}}]
    integ.reconciliar(st)
    assert set(st["bloqueadas"]) == {"203.0.113.7", "203.0.113.8"}


def test_R5_05_3_5_4_tras_liberar_la_ip_reincidente_se_vuelve_a_bloquear(onos):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    st["bloqueadas"].pop("203.0.113.7")                                      # liberada
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    assert len(onos.posts()) == 2 and "203.0.113.7" in st["bloqueadas"]


# =====================================================================================================
# R5-06 (3.6.x) Resiliencia ante fallas de ONOS: failover, reintento y errores permanentes.  Criterio: disponibilidad.
# =====================================================================================================
def _responder(monkeypatch, secuencia):
    """Sustituye requests.request: `secuencia` = lista de (código | excepción) por intento."""
    llamadas = []

    def falso(metodo, url, **kw):
        llamadas.append(url)
        r = secuencia[len(llamadas) - 1]
        if isinstance(r, Exception):
            raise r
        return Resp({"ok": 1}, r)
    monkeypatch.setattr(integ.requests, "request", falso)
    monkeypatch.setattr(integ, "ONOS_URL", "http://n1:8181/a,http://n2:8181/a,http://n3:8181/a")
    monkeypatch.setattr(integ, "_nodo", 0)
    return llamadas


def test_R5_06_3_6_1_si_un_nodo_cae_pasa_al_siguiente_y_lo_recuerda(monkeypatch):
    llamadas = _responder(monkeypatch, [requests.ConnectionError("caido"), 200, 200])
    assert integ.onos("GET", "/hosts").status_code == 200
    integ.onos("GET", "/hosts")
    assert llamadas == ["http://n1:8181/a/hosts", "http://n2:8181/a/hosts", "http://n2:8181/a/hosts"]


def test_R5_06_3_6_2_nodo_con_5xx_cede_ante_el_siguiente(monkeypatch):
    llamadas = _responder(monkeypatch, [503, 200])
    assert integ.onos("POST", "/flows").status_code == 200 and len(llamadas) == 2


@pytest.mark.parametrize("codigo", [401, 403, 408, 429])
def test_R5_06_3_6_3_errores_transitorios_se_reintentan(monkeypatch, codigo):
    _responder(monkeypatch, [codigo])
    with pytest.raises(integ.Reintentar):
        integ.onos("GET", "/hosts")


@pytest.mark.parametrize("codigo", [400, 404, 409])
def test_R5_06_3_6_4_errores_permanentes_se_descartan(monkeypatch, codigo):
    _responder(monkeypatch, [codigo])
    with pytest.raises(integ.Descartar):
        integ.onos("POST", "/flows")


def test_R5_06_3_6_5_todos_los_nodos_caidos_es_reintentable_y_no_pierde_la_alerta(monkeypatch):
    _responder(monkeypatch, [requests.ConnectionError("x")] * 3)
    with pytest.raises(integ.Reintentar):
        integ.onos("GET", "/hosts")
