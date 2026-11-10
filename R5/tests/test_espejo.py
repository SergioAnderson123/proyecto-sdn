"""Pruebas de R5/lab/espejo.py: reglas del espejo en dos cores y dos sensores, y modo clásico de un solo Core."""
import ipaddress
import os
import sys

import pytest

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(RAIZ, "codigo"))
sys.path.insert(0, os.path.join(RAIZ, "lab"))
os.environ.setdefault("ONOS_PASS", "p")
import espejo  # noqa: E402
import topologia  # noqa: E402

EJEMPLO = os.path.join(RAIZ, "deploy", "topologia.example.yaml")
TOPO = topologia.cargar(EJEMPLO)
# puertos que ONOS dice tener en cada core (nombre -> número); el LAG, el segmento de control (ens3) y los puertos de espejo existen pero no son entradas
DISP = {nombre: n for n, nombre in enumerate(["ens3", "ens5", "ens6", "ens7", "ens8", "ens9", "ens10", "ens11", "ens12"], 1)}
PUERTOS = {c["device"]: dict(DISP) for c in TOPO["cores"]}


class Resp:
    def __init__(self, codigo=200, cuerpo=None):
        self.status_code, self._c, self.text = codigo, cuerpo or {}, str(cuerpo)

    def json(self):
        return self._c


def test_numero_de_reglas_es_cores_por_entradas_por_prefijos():
    reglas = espejo.reglas_topologia(TOPO, PUERTOS)
    prefijos = len(espejo.prefijos_ipv4(TOPO))
    assert prefijos == 2                                         # campus + red externa
    assert len(reglas) == sum(len(c["entradas"]) for c in TOPO["cores"]) * prefijos     # gateway, servidores y SU acceso, por core


def test_las_reglas_cubren_los_dos_cores():
    reglas = espejo.reglas_topologia(TOPO, PUERTOS)
    assert {r["deviceId"] for r in reglas} == {c["device"] for c in TOPO["cores"]}


def test_no_se_espeja_la_troncal_entre_cores_ni_la_gestion():
    reglas = espejo.reglas_topologia(TOPO, PUERTOS)
    por_device = {c["device"]: c for c in TOPO["cores"]}
    for r in reglas:
        core = por_device[r["deviceId"]]
        in_port = next(c["port"] for c in r["selector"]["criteria"] if c["type"] == "IN_PORT")
        permitidos = {str(DISP[n]) for n in core["entradas"]}
        # segmento de control (ens3), puertos de espejo y de gestión del sensor: nunca son entrada
        prohibidos = {str(DISP["ens3"])} | {str(DISP[e["puerto"]]) for e in core["espejo"].values()}
        assert in_port in permitidos and in_port not in prohibidos


def test_cada_paquete_se_copia_una_sola_vez_por_core():
    """Dos reglas del mismo core no pueden tener el mismo (in_port, prefijo): un paquete encajaría en las dos y se copiaría doble."""
    reglas = espejo.reglas_topologia(TOPO, PUERTOS)
    claves = [(r["deviceId"], tuple(sorted((c["type"], c.get("port") or c.get("ip")) for c in r["selector"]["criteria"]))) for r in reglas]
    assert len(claves) == len(set(claves))


def test_el_reenvio_normal_va_primero_y_cada_sensor_recibe_la_copia_con_su_mac():
    r = next(x for x in espejo.reglas_topologia(TOPO, PUERTOS) if x["deviceId"] == TOPO["cores"][0]["device"])
    ins = r["treatment"]["instructions"]
    assert ins[0] == {"type": "OUTPUT", "port": "NORMAL"}
    esp = TOPO["cores"][0]["espejo"]
    assert ins[1:] == [
        {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": esp["nodo-sec-1"]["mac"]},
        {"type": "OUTPUT", "port": str(DISP[esp["nodo-sec-1"]["puerto"]])},
        {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": esp["nodo-sec-2"]["mac"]},
        {"type": "OUTPUT", "port": str(DISP[esp["nodo-sec-2"]["puerto"]])},
    ]


def test_sin_mac_no_se_reescribe_la_direccion():
    topo = topologia.cargar(EJEMPLO)
    for c in topo["cores"]:
        for e in c["espejo"].values():
            e["mac"] = None
    ins = espejo.reglas_topologia(topo, PUERTOS)[0]["treatment"]["instructions"]
    assert all(i["type"] == "OUTPUT" for i in ins) and len(ins) == 3


def test_prioridad_dentro_de_la_banda_de_r5_y_bajo_el_drop():
    for r in espejo.reglas_topologia(TOPO, PUERTOS):
        assert 41000 <= r["priority"] < 42000                    # el DROP de R5 (42000) corta antes de copiar al IDS
        assert r["isPermanent"] is True and r["timeout"] == 0


def test_solo_se_espeja_ipv4_de_las_redes_declaradas():
    for r in espejo.reglas_topologia(TOPO, PUERTOS):
        crit = {c["type"]: c for c in r["selector"]["criteria"]}
        assert crit["ETH_TYPE"]["ethType"] == "0x0800"
        assert ipaddress.ip_network(crit["IPV4_SRC"]["ip"]).version == 4
        assert crit["IPV4_SRC"]["ip"] in ("10.0.0.0/24", "203.0.113.0/24")


def test_resolver_puertos_avisa_de_una_interfaz_inexistente():
    malos = {c["device"]: {k: v for k, v in DISP.items() if k != "ens11"} for c in TOPO["cores"]}
    with pytest.raises(SystemExit) as e:
        espejo.resolver_puertos(TOPO, malos)
    assert "ens11" in str(e.value) and "sw-core-1" in str(e.value)


def test_resolver_puertos_acepta_la_topologia_correcta():
    espejo.resolver_puertos(TOPO, PUERTOS)


def test_puertos_de_lee_el_nombre_de_interfaz_de_las_anotaciones(monkeypatch):
    cuerpo = {"ports": [{"port": "1", "annotations": {"portName": "ens3"}}, {"port": "2", "annotations": {"portName": "ens5"}},
                        {"port": "local", "annotations": {"portName": "br0"}}, {"port": "9"}]}
    monkeypatch.setattr(espejo, "onos", lambda *a, **k: Resp(200, cuerpo))
    assert espejo.puertos_de("of:1") == {"ens3": 1, "ens5": 2}


def test_onos_pasa_al_siguiente_nodo_si_uno_cae(monkeypatch):
    monkeypatch.setenv("ONOS_URL", "http://a/onos/v1,http://b/onos/v1")
    visto = []

    def falso(metodo, url, **kw):
        visto.append(url)
        if url.startswith("http://a"):
            raise espejo.requests.ConnectionError("caído")
        return Resp(200, {"ok": 1})
    monkeypatch.setattr(espejo.requests, "request", falso)
    assert espejo.onos("GET", "/devices").status_code == 200
    assert visto == ["http://a/onos/v1/devices", "http://b/onos/v1/devices"]


def test_onos_5xx_tambien_pasa_al_siguiente_nodo(monkeypatch):
    monkeypatch.setenv("ONOS_URL", "http://a/x,http://b/x")
    monkeypatch.setattr(espejo.requests, "request", lambda m, u, **k: Resp(503 if "//a" in u else 200))
    assert espejo.onos("GET", "/y").status_code == 200


def test_onos_sin_ningun_nodo_sale_con_error(monkeypatch):
    monkeypatch.setenv("ONOS_URL", "http://a/x,http://b/x")

    def cae(*a, **k):
        raise espejo.requests.ConnectionError("x")
    monkeypatch.setattr(espejo.requests, "request", cae)
    with pytest.raises(SystemExit):
        espejo.onos("GET", "/y")


# ---------- modo clásico (un solo Core) ----------
def test_modo_clasico_sigue_instalando_tres_reglas(monkeypatch):
    for k, v in {"CORE_DEVICE": "of:0000a223f2c04547", "PUERTO_UPLINK": "3", "PUERTO_IDS": "1", "ESPEJO_HOME_NET": "10.0.0.0/30",
                 "ESPEJO_RED_EXTERNA": "10.0.0.64/26", "IDS_MAC": "fa:16:3e:07:e8:61"}.items():
        monkeypatch.setenv(k, v)
    reglas = espejo.reglas_clasicas()
    assert len(reglas) == 3 and {r["deviceId"] for r in reglas} == {"of:0000a223f2c04547"}
    ins = reglas[0]["treatment"]["instructions"]
    assert [i["type"] for i in ins] == ["OUTPUT", "L2MODIFICATION", "OUTPUT"] and ins[-1]["port"] == "1"


def test_se_puede_nombrar_un_puerto_por_su_numero_si_onos_no_da_nombres():
    topo = topologia.cargar(EJEMPLO)
    topo["cores"][0]["entradas"] = ["5", "8"]                                    # números en lugar de nombres de interfaz
    topo["cores"][0]["espejo"]["nodo-sec-1"]["puerto"] = "11"
    topo["cores"][0]["espejo"]["nodo-sec-2"]["puerto"] = "12"
    espejo.resolver_puertos(topo, {c["device"]: {} for c in topo["cores"][:1]} | {c["device"]: dict(DISP) for c in topo["cores"][1:]})
    regla = next(r for r in espejo.reglas_topologia(topo, PUERTOS) if r["deviceId"] == topo["cores"][0]["device"])
    assert {c["port"] for c in regla["selector"]["criteria"] if c["type"] == "IN_PORT"} <= {"5", "8"}
    assert {"type": "OUTPUT", "port": "11"} in regla["treatment"]["instructions"]
