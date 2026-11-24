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
# R5-02 (3.2.x) classify_alert(alerta) y decidir(evento): decisión según severidad y dirección del tráfico.  Criterio: mitigación.
# =====================================================================================================
def alerta_norm(sev):
    """La alerta normalizada (la que devuelve parse_ids_alert) con la severidad indicada."""
    return integ.normalizar_alerta(alerta("203.0.113.7", "10.0.0.10", sev=sev))


@pytest.mark.parametrize("sev,accion", [(1, "bloquear"), (2, "bloquear"), (3, "registrar"), (4, "registrar"), (99, "registrar")])
def test_R5_02_3_2_1_classify_alert_segun_la_severidad(sev, accion):
    assert integ.classify_alert(alerta_norm(sev)) == accion          # límite: MAX_SEVERITY = 2


@pytest.mark.parametrize("maximo,sev,accion", [(1, 1, "bloquear"), (1, 2, "registrar"), (3, 3, "bloquear"), (3, 4, "registrar")])
def test_R5_02_3_2_1b_el_limite_es_configurable(monkeypatch, maximo, sev, accion):
    monkeypatch.setattr(integ, "MAX_SEVERITY", maximo)
    assert integ.classify_alert(alerta_norm(sev)) == accion


@pytest.mark.parametrize("sev", [0, -1, -50])
def test_R5_02_3_2_2_severidad_fuera_de_rango_se_ignora(sev):
    # Suricata usa prioridades de 1 en adelante: 0 o negativo no es una severidad real y NO debe interpretarse como "la más grave".
    assert integ.classify_alert(alerta_norm(sev)) == "ignorar"


@pytest.mark.parametrize("sev,bloquea", [(1, True), (2, True), (3, False), (4, False)])
def test_R5_02_3_2_3_procesar_solo_bloquea_si_classify_alert_lo_pide(onos, sev, bloquea):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.10", sev=sev), st)
    assert ("203.0.113.7" in st["bloqueadas"]) is bloquea
    assert len(onos.posts()) == (1 if bloquea else 0)


@pytest.mark.parametrize("ev,esperado", [
    (alerta("203.0.113.7", "10.0.0.50"), ("203.0.113.7", "core")),                     # externo -> interno
    (alerta("10.0.0.21", "198.51.100.9"), ("10.0.0.21", "borde")),                     # interno -> externo (C2)
    (alerta("10.0.0.21", "10.0.0.50"), ("10.0.0.21", "borde")),                        # interno <-> interno
    (alerta("10.0.0.50", "203.0.113.7", direction="to_client"), ("203.0.113.7", "core")),   # firma sobre la respuesta
    (alerta("198.51.100.1", "203.0.113.7"), None),                                     # externo <-> externo
])
def test_R5_02_3_2_4_decide_a_quien_y_donde_bloquear(ev, esperado):
    assert integ.decidir(ev) == esperado


def test_R5_02_3_2_5_externo_se_bloquea_en_el_core_e_interno_en_su_borde(onos):
    onos.hosts = [{"ipAddresses": ["10.0.0.21"], "locations": [{"elementId": "of:00000000000000a1"}]}]
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    integ.procesar(alerta("10.0.0.21", "198.51.100.9"), st)
    assert st["bloqueadas"]["203.0.113.7"]["device"] == "of:0000000000000001"
    assert st["bloqueadas"]["10.0.0.21"]["device"] == "of:00000000000000a1"


# =====================================================================================================
# R5-03 (3.3.x) en_lista_blanca(ip) y lista blanca en decidir/procesar: una IP legítima nunca se bloquea.  Criterio: falsos positivos.
# =====================================================================================================
@pytest.mark.parametrize("ip,esperado", [
    ("10.0.0.99", True),            # en la lista
    ("10.0.0.10", True),            # en la lista
    ("10.0.0.98", False),           # vecina anterior
    ("10.0.0.100", False),          # vecina siguiente
    ("203.0.113.7", False),         # externa que no está
    ("no es una ip", False),        # dato inválido: no revienta y no se considera protegida
    ("", False),
])
def test_R5_03_3_3_1_en_lista_blanca_segun_la_ip(ip, esperado):
    assert integ.en_lista_blanca(ip) is esperado


@pytest.mark.parametrize("ip,esperado", [
    ("10.0.0.0", True), ("10.0.0.3", True),          # extremos del /30
    ("10.0.0.4", False),                             # justo fuera del /30
    ("192.0.2.5", True),                             # una IP externa conocida (p. ej. un NAT de salida) también puede protegerse
    ("192.0.2.6", False),
])
def test_R5_03_3_3_1b_la_lista_acepta_rangos_y_ips_externas(monkeypatch, ip, esperado):
    monkeypatch.setattr(integ, "LISTA_BLANCA", [integ.ipaddress.ip_network("10.0.0.0/30"), integ.ipaddress.ip_network("192.0.2.5/32")])
    assert integ.en_lista_blanca(ip) is esperado


@pytest.mark.parametrize("src,dst", [("10.0.0.99", "203.0.113.7"), ("10.0.0.10", "198.51.100.9")])
def test_R5_03_3_3_2_ip_interna_en_lista_blanca_no_se_bloquea(onos, src, dst):
    st = estado()
    integ.procesar(alerta(src, dst), st)
    assert st["bloqueadas"] == {} and onos.posts() == []


def test_R5_03_3_3_2b_ip_externa_en_lista_blanca_no_se_bloquea(onos, monkeypatch):
    monkeypatch.setattr(integ, "LISTA_BLANCA", [integ.ipaddress.ip_network("192.0.2.5/32")])
    st = estado()
    integ.procesar(alerta("192.0.2.5", "10.0.0.50"), st)          # externo -> interno: sin la lista blanca sería un bloqueo en el core
    assert st["bloqueadas"] == {} and onos.posts() == []


def test_R5_03_3_3_3_ip_no_listada_se_bloquea_y_la_blanca_vecina_no(onos):
    st = estado()
    integ.procesar(alerta("10.0.0.98", "203.0.113.7"), st)      # vecina de 10.0.0.99, pero no está en la lista
    integ.procesar(alerta("10.0.0.99", "203.0.113.7"), st)
    assert list(st["bloqueadas"]) == ["10.0.0.98"]


def test_R5_03_3_3_4_la_lista_blanca_protege_solo_a_la_ip_que_se_iba_a_bloquear(onos):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.10"), st)      # destino en lista blanca: el atacante sí se bloquea
    assert "203.0.113.7" in st["bloqueadas"]


def test_R5_03_3_3_4b_firma_sobre_la_respuesta_del_servidor_protegido(onos):
    st = estado()
    integ.procesar(alerta("10.0.0.10", "203.0.113.7", direction="to_client"), st)   # el servidor (en lista blanca) responde: el atacante es el destino
    assert list(st["bloqueadas"]) == ["203.0.113.7"]


def test_R5_03_3_3_5_la_lista_ignora_entradas_vacias(monkeypatch):
    monkeypatch.setenv("PRUEBA_LISTA", "10.0.0.40/32, ,10.0.0.50/32,")
    assert [str(r) for r in integ.redes("PRUEBA_LISTA", "")] == ["10.0.0.40/32", "10.0.0.50/32"]


@pytest.mark.parametrize("valor", ["10.0.0.0/33", "no-es-red", "10.0.0.5/24"])     # el último trae bits de host: ONOS/Suricata lo rechazan también
def test_R5_03_3_3_5b_un_prefijo_invalido_se_detecta_al_arrancar(monkeypatch, valor):
    monkeypatch.setenv("PRUEBA_LISTA", valor)
    with pytest.raises(ValueError):
        integ.redes("PRUEBA_LISTA", "")


# =====================================================================================================
# R5-04 (3.4.x) build_perimeter_rule(ip, device) y registrar_incidente: la regla DROP del perímetro y su registro.  Criterio: mitigación.
# =====================================================================================================
def test_R5_04_3_4_1_la_regla_descarta_solo_el_origen_con_prioridad_y_timeout():
    f = integ.build_perimeter_rule("203.0.113.7", "of:0000000000000001")
    assert f["deviceId"] == "of:0000000000000001"
    assert f["priority"] == 42000 and f["timeout"] == 300 and f["isPermanent"] is False
    assert f["treatment"]["instructions"] == []                              # sin instrucciones = DROP
    crit = {(c["type"], c.get("ip") or c.get("ethType")) for c in f["selector"]["criteria"]}
    assert crit == {("ETH_TYPE", "0x0800"), ("IPV4_SRC", "203.0.113.7/32")}   # solo esa IP, no el resto
    assert integ.regla_drop("of:0000000000000001", "203.0.113.7") == {"flows": [f]}   # el nombre anterior sigue entregando la misma regla


@pytest.mark.parametrize("ip", ["no es ip", "203.0.113.999", "2001:db8::1", ""])
def test_R5_04_3_4_1b_ip_invalida_o_ipv6_no_genera_regla(ip):
    with pytest.raises(ValueError):          # la regla es de IPv4: un dato inválido no debe terminar como un selector basura en el switch
        integ.build_perimeter_rule(ip, "of:0000000000000001")


def test_R5_04_3_4_1c_prioridad_y_timeout_son_configurables(monkeypatch):
    monkeypatch.setattr(integ, "PRIORITY", 43000)
    monkeypatch.setattr(integ, "BLOCK_SEC", 60)
    f = integ.build_perimeter_rule("203.0.113.7", "of:0000000000000001")
    assert f["priority"] == 43000 and f["timeout"] == 60


def test_R5_04_3_4_2_el_atacante_externo_se_bloquea_en_todos_los_cores(monkeypatch, onos):
    monkeypatch.setattr(integ, "CORE_DEVICES", ["of:c1", "of:c2", "of:c3"])
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    flujos = onos.posts()[0][2]["json"]["flows"]
    assert [f["deviceId"] for f in flujos] == ["of:c1", "of:c2", "of:c3"]
    assert onos.posts()[0][2]["params"] == {"appId": "org.campus.r5"}


def test_R5_04_3_4_2b_el_host_interno_se_bloquea_solo_en_su_borde(onos):
    onos.hosts = [{"ipAddresses": ["10.0.0.21"], "locations": [{"elementId": "of:00000000000000a1"}]}]
    st = estado()
    integ.procesar(alerta("10.0.0.21", "198.51.100.9"), st)
    flujos = onos.posts()[0][2]["json"]["flows"]
    assert [f["deviceId"] for f in flujos] == ["of:00000000000000a1"]


def test_R5_04_3_4_3_registra_el_incidente_con_origen_destino_accion_y_tiempos(onos):
    st = estado()
    t0 = time.time()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st, t_lectura=t0)
    v = st["bloqueadas"]["203.0.113.7"]
    assert v["flow_id"] == "777" and v["donde"] == "core" and v["t_alerta"]
    assert v["destino"] == "10.0.0.50" and v["accion"] == "DROP" and v["sensor"] == integ.SENSOR     # quién, contra quién y qué se hizo
    assert (v["sid"], v["firma"]) == (9000002, "LOCAL Port scan SYN")                              # tipo de ataque
    assert v["t_lectura"] <= v["t_post"] <= v["t_201"] and v["vence"] > v["t_201"]


def test_R5_04_3_4_4_en_simulacion_no_toca_onos_pero_registra_el_incidente(onos, monkeypatch):
    monkeypatch.setattr(integ, "DRY_RUN", True)
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    assert onos.posts() == []
    assert st["bloqueadas"]["203.0.113.7"]["flow_id"] is None and st["bloqueadas"]["203.0.113.7"]["accion"] == "DROP"


def test_R5_04_3_4_5_si_onos_no_devuelve_flowid_el_incidente_se_registra_igual(monkeypatch):
    def sin_id(metodo, ruta, **kw):
        return Resp({"flows": [{"deviceId": "of:0000000000000001"}]}) if metodo == "POST" else Resp()
    monkeypatch.setattr(integ, "onos", sin_id)
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    assert st["bloqueadas"]["203.0.113.7"]["flow_id"] is None          # sin flowId no se podrá borrar por id, pero el bloqueo existe


# =====================================================================================================
# R5-05 (3.5.x) Sin duplicar reglas y liberación (reconciliar, purgar_vencidas).  Criterio: mitigación sin saturar la TCAM.
# =====================================================================================================
def test_R5_05_3_5_1_una_ip_ya_bloqueada_no_genera_otra_regla(onos):
    st = estado()
    for _ in range(3):
        integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    integ.procesar(alerta("203.0.113.7", "10.0.0.50", sev=1, sid=9000003, firma="LOCAL SYN flood"), st)   # otra firma, mismo atacante
    assert len(onos.posts()) == 1


def test_R5_05_3_5_1b_dos_ips_distintas_generan_una_regla_cada_una(onos):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    integ.procesar(alerta("203.0.113.8", "10.0.0.50"), st)
    assert len(onos.posts()) == 2 and set(st["bloqueadas"]) == {"203.0.113.7", "203.0.113.8"}


def test_R5_05_3_5_1c_no_duplica_la_regla_tras_reiniciar_el_integrador(onos, tmp_path, monkeypatch):
    monkeypatch.setattr(integ, "STATE_PATH", str(tmp_path / "state.json"))
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    integ.guardar_estado(st)
    st = integ.cargar_estado()                                             # el integrador se reinicia y recupera su estado
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


def test_R5_05_3_5_3b_reconciliar_sin_bloqueos_no_consulta_a_onos(onos):
    integ.reconciliar(estado())
    assert onos.llamadas == []


def test_R5_05_3_5_3c_si_onos_cae_al_reconciliar_se_conserva_la_lista(monkeypatch):
    def cae(*a, **k):
        raise integ.Reintentar("ONOS caído")
    monkeypatch.setattr(integ, "onos", cae)
    st = estado()
    st["bloqueadas"]["203.0.113.7"] = {"vence": time.time() + 100, "t_201": time.time() - 60}
    with pytest.raises(integ.Reintentar):                                  # main() lo captura y reintenta más tarde
        integ.reconciliar(st)
    assert "203.0.113.7" in st["bloqueadas"]                               # sin datos de ONOS no se libera a nadie


@pytest.mark.parametrize("delta,olvidada", [(-1, True), (0, True), (100, False)])      # vence = ahora + delta
def test_R5_05_3_5_4_purgar_vencidas_olvida_solo_los_bloqueos_cumplidos(delta, olvidada):
    st = estado()
    st["bloqueadas"]["203.0.113.7"] = {"vence": 1000 + delta}
    assert integ.purgar_vencidas(st, ahora=1000) == (["203.0.113.7"] if olvidada else [])
    assert ("203.0.113.7" not in st["bloqueadas"]) is olvidada


def test_R5_05_3_5_5_tras_liberar_la_ip_reincidente_se_vuelve_a_bloquear(onos):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    st["bloqueadas"]["203.0.113.7"]["t_201"] -= 60                           # ya pasó el periodo de gracia
    onos.flujos = []                                                         # r5ctl desbloquear borró el flujo
    integ.reconciliar(st)
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    assert len(onos.posts()) == 2 and "203.0.113.7" in st["bloqueadas"]


def test_R5_05_3_5_5b_al_caducar_el_bloqueo_la_ip_reincidente_se_vuelve_a_bloquear(onos):
    st = estado()
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    integ.purgar_vencidas(st, ahora=time.time() + integ.BLOCK_SEC + 1)       # pasan más de BLOCK_SEC segundos sin actividad
    integ.procesar(alerta("203.0.113.7", "10.0.0.50"), st)
    assert len(onos.posts()) == 2


# =====================================================================================================
# R5-06 (3.6.x) Resiliencia ante fallas de ONOS y del Policy Mapper: failover, reintento, errores permanentes.  Criterio: disponibilidad.
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


@pytest.mark.parametrize("codigo", [500, 502, 503])
def test_R5_06_3_6_2b_si_todos_los_nodos_responden_5xx_es_reintentable(monkeypatch, codigo):
    llamadas = _responder(monkeypatch, [codigo] * 3)
    with pytest.raises(integ.Reintentar):
        integ.onos("GET", "/hosts")
    assert len(llamadas) == 3                                     # probó los tres nodos antes de rendirse


def test_R5_06_3_6_2c_un_timeout_se_trata_como_nodo_caido(monkeypatch):
    llamadas = _responder(monkeypatch, [requests.Timeout("lento"), 200])
    assert integ.onos("GET", "/hosts").status_code == 200 and len(llamadas) == 2


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


def test_R5_06_3_6_5b_sin_nodos_configurados_es_reintentable(monkeypatch):
    monkeypatch.setattr(integ, "ONOS_URL", "")
    with pytest.raises(integ.Reintentar):
        integ.onos("GET", "/hosts")


@pytest.mark.parametrize("espera,fallo,esperado", [(1, True, 2), (4, True, 8), (16, True, 30), (30, True, 30), (30, False, 1)])
def test_R5_06_3_6_6_la_espera_crece_de_forma_exponencial_acotada_y_se_reinicia(espera, fallo, esperado):
    assert integ.siguiente_espera(espera, fallo) == esperado      # BACKOFF_MIN = 1 s, BACKOFF_MAX = 30 s


def _eve(tmp_path, monkeypatch, *lineas):
    eve = tmp_path / "eve-alerts.json"
    monkeypatch.setattr(integ, "EVE_PATH", str(eve))
    with open(eve, "w", encoding="utf-8", newline="") as f:
        f.write("".join(json.dumps(l) + "\n" for l in lineas))
    return eve


def test_R5_06_3_6_7_si_onos_cae_no_se_pierde_ninguna_alerta(tmp_path, monkeypatch):
    eve = _eve(tmp_path, monkeypatch, alerta("203.0.113.7", "10.0.0.50"), alerta("203.0.113.8", "10.0.0.50"))

    def cae(*a, **k):
        raise integ.Reintentar("ONOS caído")
    monkeypatch.setattr(integ, "onos", cae)
    st = estado()
    assert integ.consumir_eve(st) is True
    assert st["offset"] == 0 and st["bloqueadas"] == {}            # ninguna se da por procesada: seguirán en el archivo
    falso = OnosFalso()
    monkeypatch.setattr(integ, "onos", falso)                      # ONOS vuelve
    assert integ.consumir_eve(st) is False
    assert sorted(st["bloqueadas"]) == ["203.0.113.7", "203.0.113.8"] and len(falso.posts()) == 2
    assert st["offset"] == eve.stat().st_size


def test_R5_06_3_6_7b_si_onos_cae_a_mitad_no_se_repite_lo_ya_procesado(tmp_path, monkeypatch):
    l1 = alerta("203.0.113.7", "10.0.0.50")
    eve = _eve(tmp_path, monkeypatch, l1, alerta("203.0.113.8", "10.0.0.50"))
    falso, intentos = OnosFalso(), {"posts": 0}

    def cae_en_el_segundo(metodo, ruta, **kw):
        if metodo == "POST":
            intentos["posts"] += 1
            if intentos["posts"] == 2:
                raise integ.Reintentar("ONOS caído")
        return falso(metodo, ruta, **kw)
    monkeypatch.setattr(integ, "onos", cae_en_el_segundo)
    st = estado()
    assert integ.consumir_eve(st) is True
    assert list(st["bloqueadas"]) == ["203.0.113.7"] and st["offset"] == len((json.dumps(l1) + "\n").encode())
    monkeypatch.setattr(integ, "onos", falso)
    assert integ.consumir_eve(st) is False
    assert sorted(st["bloqueadas"]) == ["203.0.113.7", "203.0.113.8"]
    assert len(falso.posts()) == 2                                  # la 1.a no se reenvió


def test_R5_06_3_6_8_un_error_permanente_no_bloquea_la_cola(tmp_path, monkeypatch):
    eve = _eve(tmp_path, monkeypatch, alerta("203.0.113.9", "10.0.0.50"), alerta("203.0.113.10", "10.0.0.50"))
    falso = OnosFalso()

    def rechaza_la_primera(metodo, ruta, **kw):
        if metodo == "POST" and "203.0.113.9/32" in json.dumps(kw["json"]):
            raise integ.Descartar("HTTP 400: regla inválida")
        return falso(metodo, ruta, **kw)
    monkeypatch.setattr(integ, "onos", rechaza_la_primera)
    st = estado()
    assert integ.consumir_eve(st) is False
    assert list(st["bloqueadas"]) == ["203.0.113.10"]               # la rechazada se descarta y la siguiente se procesa
    assert st["offset"] == eve.stat().st_size


HOST_BORDE = [{"ipAddresses": ["10.0.0.21"], "mac": "00:00:00:00:00:ab", "locations": [{"elementId": "of:00000000000000a1"}]}]


def test_R5_06_3_6_9_un_policy_mapper_caido_no_impide_el_bloqueo(onos, monkeypatch):
    def cae(*a, **k):
        raise integ.requests.ConnectionError("mapper caído")
    monkeypatch.setattr(integ, "MAPPER_URL", "http://m1:5000")
    monkeypatch.setattr(integ.requests, "post", cae)
    onos.hosts = HOST_BORDE
    st = estado()
    integ.procesar(alerta("10.0.0.21", "198.51.100.9"), st)
    assert "10.0.0.21" in st["bloqueadas"] and st["bloqueadas"]["10.0.0.21"]["sesion"] is None


def test_R5_06_3_6_9b_si_el_mapper_primario_cae_se_usa_la_replica(onos, monkeypatch):
    urls = []

    def post(url, json=None, timeout=None, **kw):
        urls.append(url)
        if "m1" in url:
            raise integ.requests.ConnectionError("m1 caído")
        return Resp({"status": "revocada"})
    monkeypatch.setattr(integ, "MAPPER_URL", "http://m1:5000,http://m2:5000")
    monkeypatch.setattr(integ.requests, "post", post)
    onos.hosts = HOST_BORDE
    st = estado()
    integ.procesar(alerta("10.0.0.21", "198.51.100.9"), st)
    assert st["bloqueadas"]["10.0.0.21"]["sesion"] == "revocada"
    assert urls == ["http://m1:5000/sessions/revoke", "http://m2:5000/sessions/revoke"]
