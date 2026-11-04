"""Pruebas unitarias del integrador M09 (R5/codigo/integrador.py).

No necesitan ONOS ni Suricata: la función `onos()` se sustituye por un doble.
Ejecutar:  python -m pytest R5/tests -v
"""
import importlib
import ipaddress
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "codigo"))
os.environ.update({"ONOS_URL": "http://onos.test", "ONOS_USER": "u", "ONOS_PASS": "p",
                   "HOME_NET": "10.0.0.0/24", "LISTA_BLANCA": "10.0.0.99/32,10.0.0.10/32",
                   "CORE_DEVICE": "of:0000000000000001"})
integ = importlib.import_module("integrador")


class Resp:
    def __init__(self, cuerpo=None, codigo=200):
        self._c, self.status_code, self.text = cuerpo or {}, codigo, json.dumps(cuerpo or {})

    def json(self):
        return self._c


class OnosFalso:
    """Doble de ONOS: registra las peticiones y devuelve respuestas configurables."""

    def __init__(self):
        self.llamadas, self.hosts, self.flujos, self.falla = [], [], [], None

    def __call__(self, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta, kw))
        if self.falla:
            raise self.falla
        if metodo == "GET" and ruta == "/hosts":
            return Resp({"hosts": self.hosts})
        if metodo == "GET" and ruta.startswith("/flows/application"):
            return Resp({"flows": self.flujos})
        if metodo == "POST" and ruta == "/flows":
            dev = kw["json"]["flows"][0]["deviceId"]
            return Resp({"flows": [{"deviceId": dev, "flowId": "777"}]})
        return Resp()


@pytest.fixture
def onos(monkeypatch):
    falso = OnosFalso()
    monkeypatch.setattr(integ, "onos", falso)
    return falso


def alerta(src, dst, sev=2, direction="to_server", sid=9000002, firma="LOCAL Port scan SYN"):
    return {"event_type": "alert", "timestamp": "2026-10-30T12:00:00.000000+0000", "src_ip": src, "dest_ip": dst,
            "direction": direction, "alert": {"severity": sev, "signature_id": sid, "signature": firma}}


def estado():
    return {"bloqueadas": {}, "inode": None, "offset": 0}


# ---------- decidir() ----------
@pytest.mark.parametrize("ev,esperado", [
    (alerta("10.0.1.10", "10.0.0.50"), ("10.0.1.10", "core")),            # externo -> interno
    (alerta("10.0.0.21", "10.0.1.50"), ("10.0.0.21", "borde")),           # interno -> externo (C2)
    (alerta("10.0.0.21", "10.0.0.50"), ("10.0.0.21", "borde")),           # interno <-> interno (R3/R4): borde del origen
    (alerta("10.0.0.99", "10.0.0.50"), None),                              # interno <-> interno con origen en lista blanca
    (alerta("10.0.1.10", "10.0.1.11"), None),                              # externo <-> externo
    (alerta("10.0.1.10", "10.0.0.10"), ("10.0.1.10", "core")),            # destino en lista blanca: se bloquea al atacante
    (alerta("10.0.0.99", "10.0.1.50"), None),                              # origen en lista blanca
    (alerta("10.0.0.10", "10.0.1.12", direction="to_client"), ("10.0.1.12", "core")),   # firma sobre la respuesta
    (alerta("10.0.1.12", "10.0.0.10", direction="to_client"), None),      # to_client: el atacante sería el servidor (lista blanca)
])
def test_decidir(ev, esperado):
    assert integ.decidir(ev) == esperado


def test_decidir_intranet_desactivable(monkeypatch):
    monkeypatch.setattr(integ, "INTRANET", False)
    assert integ.decidir(alerta("10.0.0.21", "10.0.0.50")) is None


@pytest.mark.parametrize("src,dst", [("2001:db8::1", "10.0.0.50"), ("10.0.1.10", "fe80::1"), ("", "10.0.0.50"), ("basura", "x")])
def test_decidir_ignora_ipv6_y_datos_invalidos(src, dst):
    assert integ.decidir(alerta(src, dst)) is None


def test_decidir_evento_sin_campos():
    assert integ.decidir({}) is None


# ---------- regla_drop() ----------
def test_regla_drop_contenido():
    r = integ.regla_drop("of:0000000000000001", "10.0.1.10")["flows"][0]
    assert r["priority"] == 42000 and 41000 <= r["priority"] <= 44999      # banda de R5
    assert r["treatment"]["instructions"] == []                             # sin instrucciones = DROP
    assert r["isPermanent"] is False and r["timeout"] == 300
    crit = {c["type"]: c for c in r["selector"]["criteria"]}
    assert crit["ETH_TYPE"]["ethType"] == "0x0800" and crit["IPV4_SRC"]["ip"] == "10.0.1.10/32"
    assert ipaddress.ip_network(crit["IPV4_SRC"]["ip"])


# ---------- procesar() ----------
def test_procesa_ataque_externo_en_core(onos):
    st = estado()
    integ.procesar(alerta("10.0.1.10", "10.0.0.50"), st)
    assert "10.0.1.10" in st["bloqueadas"]
    v = st["bloqueadas"]["10.0.1.10"]
    assert v["device"] == "of:0000000000000001" and v["flow_id"] == "777" and v["sid"] == 9000002
    assert v["t_post"] <= v["t_201"] and v["vence"] > time.time()
    post = [c for c in onos.llamadas if c[0] == "POST"]
    assert len(post) == 1 and post[0][2]["params"]["appId"] == integ.APP_ID


def test_ignora_severidad_alta(onos):
    st = estado()
    integ.procesar(alerta("10.0.1.10", "10.0.0.50", sev=3), st)
    assert st["bloqueadas"] == {} and onos.llamadas == []


def test_ignora_eventos_que_no_son_alertas(onos):
    st = estado()
    integ.procesar({"event_type": "flow", "src_ip": "10.0.1.10", "dest_ip": "10.0.0.50"}, st)
    assert st["bloqueadas"] == {} and onos.llamadas == []


def test_deduplica_misma_ip(onos):
    st = estado()
    for _ in range(50):                      # un escaneo genera decenas de alertas
        integ.procesar(alerta("10.0.1.10", "10.0.0.50"), st)
    assert len([c for c in onos.llamadas if c[0] == "POST"]) == 1


def test_host_interno_se_bloquea_en_su_borde(onos):
    onos.hosts = [{"ipAddresses": ["10.0.0.21"], "locations": [{"elementId": "of:0000000000000003", "port": "1"}]}]
    st = estado()
    integ.procesar(alerta("10.0.0.21", "10.0.1.50"), st)
    assert st["bloqueadas"]["10.0.0.21"]["device"] == "of:0000000000000003"


def test_host_interno_desconocido_cae_al_core(onos):
    st = estado()
    integ.procesar(alerta("10.0.0.21", "10.0.1.50"), st)      # ONOS no lo conoce
    assert st["bloqueadas"]["10.0.0.21"]["device"] == integ.CORE_DEVICE


def test_error_permanente_descarta_sin_bloquear(monkeypatch):
    def rechaza(*a, **k):
        raise integ.Descartar("HTTP 400")
    monkeypatch.setattr(integ, "onos", rechaza)
    st = estado()
    integ.procesar(alerta("10.0.1.10", "10.0.0.50"), st)       # no debe propagar la excepción
    assert st["bloqueadas"] == {}


def test_error_transitorio_se_propaga_y_no_registra(onos):
    onos.falla = integ.Reintentar("HTTP 503")
    st = estado()
    with pytest.raises(integ.Reintentar):
        integ.procesar(alerta("10.0.1.10", "10.0.0.50"), st)
    assert st["bloqueadas"] == {}                              # la alerta se reintentará


# ---------- onos(): clasificación de errores HTTP ----------
@pytest.mark.parametrize("codigo,excepcion", [(500, "Reintentar"), (503, "Reintentar"), (401, "Reintentar"), (403, "Reintentar"),
                                              (408, "Reintentar"), (429, "Reintentar"), (400, "Descartar"), (404, "Descartar")])
def test_clasificacion_http(monkeypatch, codigo, excepcion):
    monkeypatch.setattr(integ.requests, "request", lambda *a, **k: Resp({}, codigo))
    with pytest.raises(getattr(integ, excepcion)):
        integ.onos("GET", "/x")


def test_red_caida_es_reintentable(monkeypatch):
    def cae(*a, **k):
        raise integ.requests.ConnectionError("sin red")
    monkeypatch.setattr(integ.requests, "request", cae)
    with pytest.raises(integ.Reintentar):
        integ.onos("GET", "/x")


def test_exito_devuelve_respuesta(monkeypatch):
    monkeypatch.setattr(integ.requests, "request", lambda *a, **k: Resp({"ok": 1}, 201))
    assert integ.onos("POST", "/flows").status_code == 201


# ---------- reconciliar() ----------
def flujo_onos(ip):
    return {"selector": {"criteria": [{"type": "ETH_TYPE"}, {"type": "IPV4_SRC", "ip": f"{ip}/32"}]}}


def bloqueada(ip, hace=60):
    return {"vence": time.time() + 200, "t_201": time.time() - hace}


def test_reconcilia_olvida_ip_sin_flujo(onos):
    onos.flujos = [flujo_onos("10.0.1.11")]
    st = estado()
    st["bloqueadas"] = {"10.0.1.10": bloqueada("10.0.1.10"), "10.0.1.11": bloqueada("10.0.1.11")}
    integ.reconciliar(st)
    assert set(st["bloqueadas"]) == {"10.0.1.11"}


def test_reconcilia_respeta_periodo_de_gracia(onos):
    onos.flujos = []                                           # ONOS aún no refleja el flujo recién creado
    st = estado()
    st["bloqueadas"] = {"10.0.1.10": bloqueada("10.0.1.10", hace=1)}
    integ.reconciliar(st)
    assert "10.0.1.10" in st["bloqueadas"]


def test_reconcilia_sin_bloqueos_no_consulta(onos):
    integ.reconciliar(estado())
    assert onos.llamadas == []


def test_reconcilia_si_onos_falla_conserva_lista(onos):
    onos.falla = integ.Reintentar("HTTP 503")
    st = estado()
    st["bloqueadas"] = {"10.0.1.10": bloqueada("10.0.1.10")}
    with pytest.raises(integ.Reintentar):
        integ.reconciliar(st)
    assert "10.0.1.10" in st["bloqueadas"]


# ---------- estado en disco ----------
def test_estado_ida_y_vuelta(tmp_path, monkeypatch):
    monkeypatch.setattr(integ, "STATE_PATH", str(tmp_path / "sub" / "state.json"))
    st = estado()
    st.update(inode=123, offset=4567)
    st["bloqueadas"]["10.0.1.10"] = {"vence": 1.0}
    integ.guardar_estado(st)
    assert integ.cargar_estado() == st


def test_estado_inexistente_o_corrupto(tmp_path, monkeypatch):
    monkeypatch.setattr(integ, "STATE_PATH", str(tmp_path / "no_existe.json"))
    assert integ.cargar_estado() == {"bloqueadas": {}, "inode": None, "offset": 0}
    corrupto = tmp_path / "malo.json"
    corrupto.write_text("{no es json")
    monkeypatch.setattr(integ, "STATE_PATH", str(corrupto))
    assert integ.cargar_estado()["bloqueadas"] == {}


def test_estado_migra_formato_anterior(tmp_path, monkeypatch):
    viejo = tmp_path / "viejo.json"
    viejo.write_text(json.dumps({"bloqueadas": {"10.0.1.10": 1793404063.6}, "inode": 5, "offset": 9}))
    monkeypatch.setattr(integ, "STATE_PATH", str(viejo))
    assert integ.cargar_estado()["bloqueadas"]["10.0.1.10"] == {"vence": 1793404063.6}


def test_guardado_atomico_no_deja_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(integ, "STATE_PATH", str(tmp_path / "state.json"))
    integ.guardar_estado(estado())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]
