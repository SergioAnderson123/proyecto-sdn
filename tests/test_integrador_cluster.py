"""Integrador M09 en el slice de 14 VM: DROP en los dos cores, clúster ONOS de tres nodos y Policy Mapper con réplica.

No necesitan ONOS ni Suricata. Ejecutar:  python -m pytest R5/tests -v
"""
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "codigo"))
os.environ.update({"ONOS_URL": "http://onos.test", "ONOS_USER": "u", "ONOS_PASS": "p",
                   "HOME_NET": "10.0.0.0/24", "LISTA_BLANCA": "10.0.0.99/32,10.0.0.10/32",
                   "CORE_DEVICE": "of:0000000000000001"})
integ = importlib.import_module("integrador")

CORE_1, CORE_2, ACC_1 = "of:00000000000000a1", "of:00000000000000a2", "of:00000000000000b1"


class Resp:
    def __init__(self, cuerpo=None, codigo=200):
        self._c, self.status_code, self.text = cuerpo or {}, codigo, json.dumps(cuerpo or {})

    def json(self):
        return self._c


class OnosCluster:
    """Doble de ONOS que acepta varias reglas por petición y devuelve un flowId por regla."""

    def __init__(self):
        self.llamadas, self.hosts = [], []

    def __call__(self, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta, kw))
        if metodo == "GET" and ruta == "/hosts":
            return Resp({"hosts": self.hosts})
        if metodo == "POST" and ruta == "/flows":
            return Resp({"flows": [{"deviceId": f["deviceId"], "flowId": str(100 + i)} for i, f in enumerate(kw["json"]["flows"])]})
        return Resp()


@pytest.fixture
def dos_cores(monkeypatch):
    monkeypatch.setattr(integ, "CORE_DEVICES", [CORE_1, CORE_2])
    monkeypatch.setattr(integ, "CORE_DEVICE", CORE_1)
    falso = OnosCluster()
    monkeypatch.setattr(integ, "onos", falso)
    return falso


def alerta(src, dst, sev=2):
    return {"event_type": "alert", "timestamp": "2026-11-09T12:00:00.000000+0000", "src_ip": src, "dest_ip": dst,
            "direction": "to_server", "alert": {"severity": sev, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}}


def estado():
    return {"bloqueadas": {}, "inode": None, "offset": 0}


# ---------- DROP en los dos cores ----------
def test_atacante_externo_se_bloquea_en_los_dos_cores_con_una_sola_peticion(dos_cores):
    st = estado()
    integ.procesar(alerta("203.0.113.10", "10.0.0.40"), st)
    posts = [c for c in dos_cores.llamadas if c[0] == "POST"]
    assert len(posts) == 1                                                       # una petición, no una por core
    reglas = posts[0][2]["json"]["flows"]
    assert [r["deviceId"] for r in reglas] == [CORE_1, CORE_2]
    assert all(r["priority"] == 42000 and r["treatment"]["instructions"] == [] for r in reglas)
    v = st["bloqueadas"]["203.0.113.10"]
    assert v["device"] == f"{CORE_1},{CORE_2}" and v["flow_id"] == "100,101" and v["donde"] == "core"


def test_host_interno_se_bloquea_solo_en_su_acceso(dos_cores):
    dos_cores.hosts = [{"ipAddresses": ["10.0.0.21"], "locations": [{"elementId": ACC_1, "port": "5"}]}]
    st = estado()
    integ.procesar(alerta("10.0.0.21", "203.0.113.50"), st)
    reglas = [c for c in dos_cores.llamadas if c[0] == "POST"][0][2]["json"]["flows"]
    assert [r["deviceId"] for r in reglas] == [ACC_1]                           # el acceso donde ONOS ubica al host, no los cores
    assert st["bloqueadas"]["10.0.0.21"]["device"] == ACC_1


def test_host_interno_desconocido_cae_a_los_dos_cores(dos_cores):
    st = estado()
    integ.procesar(alerta("10.0.0.21", "203.0.113.50"), st)                     # ONOS no lo conoce
    assert st["bloqueadas"]["10.0.0.21"]["device"] == f"{CORE_1},{CORE_2}"


def test_la_misma_ip_no_se_bloquea_dos_veces(dos_cores):
    st = estado()
    for _ in range(20):
        integ.procesar(alerta("203.0.113.10", "10.0.0.40"), st)
    assert len([c for c in dos_cores.llamadas if c[0] == "POST"]) == 1


def test_el_registro_dice_que_sensor_bloqueo(dos_cores, monkeypatch):
    monkeypatch.setattr(integ, "SENSOR", "nodo-sec-2")
    st = estado()
    integ.procesar(alerta("203.0.113.10", "10.0.0.40"), st)
    assert st["bloqueadas"]["203.0.113.10"]["sensor"] == "nodo-sec-2"


def test_servidores_de_recursos_no_se_bloquean_por_defecto():
    """La lista blanca por defecto protege a los servidores (.40, .50) y al servidor DHCP de ONOS (.250)."""
    por_defecto = integ.redes("__NO_DEFINIDA__", "10.0.0.40/32,10.0.0.50/32,10.0.0.250/32")
    assert all(any(ip in n for n in por_defecto) for ip in map(integ.ip4, ("10.0.0.40", "10.0.0.50", "10.0.0.250")))


# ---------- clúster ONOS ----------
def pedir_a(monkeypatch, urls, comportamiento):
    """Sustituye requests.request: `comportamiento(url)` devuelve una Resp o lanza una excepción. Devuelve la lista de URL pedidas."""
    monkeypatch.setattr(integ, "ONOS_URL", urls)
    monkeypatch.setattr(integ, "_nodo", 0)
    pedidas = []

    def falso(metodo, url, **kw):
        pedidas.append(url)
        return comportamiento(url)
    monkeypatch.setattr(integ.requests, "request", falso)
    return pedidas


def test_onos_pasa_al_siguiente_nodo_si_uno_no_responde(monkeypatch):
    def comp(url):
        if url.startswith("http://n1"):
            raise integ.requests.ConnectionError("caído")
        return Resp({"ok": 1})
    pedidas = pedir_a(monkeypatch, "http://n1/onos/v1,http://n2/onos/v1,http://n3/onos/v1", comp)
    assert integ.onos("GET", "/hosts").status_code == 200
    assert pedidas == ["http://n1/onos/v1/hosts", "http://n2/onos/v1/hosts"]


def test_onos_recuerda_el_nodo_que_funciono(monkeypatch):
    def comp(url):
        if url.startswith("http://n1"):
            raise integ.requests.ConnectionError("caído")
        return Resp()
    pedidas = pedir_a(monkeypatch, "http://n1/x,http://n2/x", comp)
    integ.onos("GET", "/a")
    integ.onos("GET", "/b")
    assert pedidas == ["http://n1/x/a", "http://n2/x/a", "http://n2/x/b"]      # la segunda petición va directa al nodo vivo


def test_onos_un_5xx_pasa_al_siguiente_nodo(monkeypatch):
    pedidas = pedir_a(monkeypatch, "http://n1/x,http://n2/x", lambda url: Resp({}, 503 if "//n1" in url else 200))
    assert integ.onos("GET", "/a").status_code == 200
    assert len(pedidas) == 2


def test_onos_un_4xx_no_cambia_de_nodo(monkeypatch):
    pedidas = pedir_a(monkeypatch, "http://n1/x,http://n2/x", lambda url: Resp({}, 404))
    with pytest.raises(integ.Descartar):
        integ.onos("GET", "/a")
    assert len(pedidas) == 1                                                   # un 404 es una respuesta válida de ONOS, no un nodo caído


def test_onos_con_todos_los_nodos_caidos_se_reintenta_despues(monkeypatch):
    def comp(url):
        raise integ.requests.ConnectionError("caído")
    pedidas = pedir_a(monkeypatch, "http://n1/x,http://n2/x,http://n3/x", comp)
    with pytest.raises(integ.Reintentar):
        integ.onos("GET", "/a")
    assert len(pedidas) == 3


def test_lista_urls_ignora_espacios_barras_y_vacios():
    assert integ.lista_urls(" http://a:1/ , http://b:1,, ") == ["http://a:1", "http://b:1"]
    assert integ.lista_urls("") == [] and integ.lista_urls(None) == []


# ---------- Policy Mapper con réplica ----------
MAC = "00:00:00:00:00:ab"


class PostMapper:
    def __init__(self, comportamiento):
        self.urls, self.comportamiento = [], comportamiento

    def __call__(self, url, json=None, timeout=None, **kw):
        self.urls.append(url)
        return self.comportamiento(url)


def test_i11_usa_la_replica_si_el_primer_mapper_no_responde(monkeypatch):
    def comp(url):
        if "m1" in url:
            raise integ.requests.ConnectionError("caído")
        return Resp({"status": "revocada"})
    falso = PostMapper(comp)
    monkeypatch.setattr(integ, "MAPPER_URL", "http://m1:5000,http://m2:5000")
    monkeypatch.setattr(integ.requests, "post", falso)
    assert integ.revocar_sesion("10.0.0.21", MAC) == "revocada"
    assert falso.urls == ["http://m1:5000/sessions/revoke", "http://m2:5000/sessions/revoke"]


def test_i11_no_llama_a_la_replica_si_el_primero_responde(monkeypatch):
    falso = PostMapper(lambda url: Resp({"status": "revocada"}))
    monkeypatch.setattr(integ, "MAPPER_URL", "http://m1:5000,http://m2:5000")
    monkeypatch.setattr(integ.requests, "post", falso)
    assert integ.revocar_sesion("10.0.0.21", MAC) == "revocada"
    assert falso.urls == ["http://m1:5000/sessions/revoke"]


def test_i11_con_los_dos_mapper_caidos_devuelve_none_sin_romper_el_bloqueo(monkeypatch):
    def comp(url):
        raise integ.requests.ConnectionError("caído")
    monkeypatch.setattr(integ, "MAPPER_URL", "http://m1:5000,http://m2:5000")
    monkeypatch.setattr(integ.requests, "post", PostMapper(comp))
    assert integ.revocar_sesion("10.0.0.21", MAC) is None
