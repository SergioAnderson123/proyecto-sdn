"""Pruebas del agente 802.1X: estados, descubrimiento de usuarios, una sola instancia de wpa_supplicant y la API HTTP."""
import importlib.util
import json
import subprocess
import threading
import types
import urllib.request
from collections import deque
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("agente", AQUI / "agente.py")
ag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ag)

SUPP = AQUI.parent / "shared" / "mininet" / "supplicants"
MAC = "fa:16:3e:6b:69:bb"


def test_descubre_los_seis_usuarios_de_los_suplicantes_del_repo():
    u = ag.descubrir_usuarios(SUPP)
    assert set(u) == {"profesor01", "profesor02", "alumno01", "alumno02", "alumno03", "support01"}
    assert u["profesor01"].endswith("wpa_profe_01.conf")


def test_roles_y_vlan():
    assert ag.rol_de("profesor02") == ("Docente", 10)
    assert ag.rol_de("alumno03") == ("Alumno", 20)
    assert ag.rol_de("support01") == ("Soporte (Administrador)", 40)
    assert ag.rol_de("otro")[1] is None


def test_parsear_wpa_status():
    d = ag.parsear_wpa("Selected interface 'ens4'\nSupplicant PAE state=AUTHENTICATED\nsuppPortStatus=Authorized\nEAP state=SUCCESS\n")
    assert d == {"Supplicant PAE state": "AUTHENTICATED", "suppPortStatus": "Authorized", "EAP state": "SUCCESS"}
    assert ag.parsear_wpa("") == {} and ag.parsear_wpa(None) == {}


@pytest.mark.parametrize("datos,vivo,log,esperado", [
    ({}, False, [], "desconectado"),
    ({"suppPortStatus": "Authorized"}, True, [], "autenticado"),
    ({"suppPortStatus": "Unauthorized", "Supplicant PAE state": "HELD"}, True, [], "rechazado"),
    ({"suppPortStatus": "Unauthorized", "EAP state": "FAILURE"}, True, [], "rechazado"),
    ({"suppPortStatus": "Unauthorized", "Supplicant PAE state": "AUTHENTICATING"}, True, [], "autenticando"),
    ({}, True, ["x CTRL-EVENT-EAP-SUCCESS EAP authentication completed successfully"], "autenticado"),
    ({}, True, ["x CTRL-EVENT-EAP-FAILURE EAP authentication failed"], "rechazado"),
    ({}, True, [], "autenticando"),
])
def test_estado_desde_wpa(datos, vivo, log, esperado):
    assert ag.estado_desde_wpa(datos, vivo, deque(log)) == esperado


def test_sesion_y_rechazo_del_policy_mapper():
    sesiones = {"total_sessions": 1, "sessions": {MAC: {"username": "profesor01@example.org", "vlan": 10, "rol": "Docente"}}}
    assert ag.sesion_de(MAC.upper(), sesiones)["vlan"] == 10
    assert ag.sesion_de(MAC, None) is None
    eventos = {"eventos": [
        {"t": 100, "tipo": "rechazado", "mac": MAC, "detalle": "viejo"},
        {"t": 200, "tipo": "rechazado", "mac": MAC, "detalle": "MAC registrada distinta de la fisica"},
        {"t": 210, "tipo": "admitido", "mac": MAC}, {"t": 220, "tipo": "rechazado", "mac": "00:00:00:00:00:99"}]}
    assert ag.ultimo_rechazo(MAC, eventos, desde=150)["detalle"].startswith("MAC registrada")
    assert ag.ultimo_rechazo(MAC, eventos, desde=300) is None
    assert ag.ultimo_rechazo(MAC, [{"t": 5, "tipo": "conflicto", "mac": MAC}], desde=0)["tipo"] == "conflicto"


LLAMADAS = []   # comandos que el agente ejecuta con subprocess.run (wpa_cli)


class ProcFalso:
    instancias = []

    def __init__(self, cmd, **kw):
        self.cmd, self.vivo, self.stdout = cmd, True, iter(["ens4: CTRL-EVENT-EAP-STARTED\n"])
        ProcFalso.instancias.append(self)

    def poll(self):
        return None if self.vivo else 0

    def terminate(self):
        self.vivo = False

    def wait(self, t=None):
        return 0

    def kill(self):
        self.vivo = False


@pytest.fixture
def agente(monkeypatch, tmp_path):
    ProcFalso.instancias.clear()
    # solo el modulo del agente ve el subprocess simulado (el resto de Python sigue con el real)
    LLAMADAS.clear()
    monkeypatch.setattr(ag.time, "sleep", lambda s: None)
    monkeypatch.setattr(ag, "subprocess", types.SimpleNamespace(
        Popen=ProcFalso, PIPE=-1, STDOUT=-2, TimeoutExpired=subprocess.TimeoutExpired,
        run=lambda cmd, **k: (LLAMADAS.append(cmd), types.SimpleNamespace(returncode=1, stdout=""))[1]))
    monkeypatch.setattr(ag.os, "remove", lambda p: None)
    a = ag.Agente(iface="ens4", carpeta=SUPP, defecto="profesor01", mapper="http://mapper.invalido")
    monkeypatch.setattr(a, "mac", lambda: MAC)
    return a


def test_conectar_lanza_wpa_supplicant_con_la_configuracion_del_usuario(agente):
    agente.conectar("alumno01")
    cmd = ProcFalso.instancias[0].cmd
    assert cmd[1:5] == ["-D", "wired", "-i", "ens4"]
    assert cmd[cmd.index("-c") + 1].endswith("wpa_alumno_01.conf") and cmd[cmd.index("-C") + 1] == "/run/wpa_supplicant"
    assert agente.usuario == "alumno01"


def test_nunca_hay_dos_instancias_a_la_vez(agente):
    agente.conectar("profesor01")
    agente.conectar("alumno02")
    primera, segunda = ProcFalso.instancias
    assert not primera.vivo and segunda.vivo     # la anterior se cierra antes de lanzar la nueva
    agente.desconectar()
    assert not segunda.vivo and agente.usuario is None and agente.deseado is None


def test_al_cambiar_de_identidad_o_desconectar_envia_logoff_antes_de_cerrar(agente):
    agente.conectar("profesor01")
    assert not [c for c in LLAMADAS if "logoff" in c]
    agente.conectar("alumno01")      # cambia de identidad: la anterior debe despedirse
    agente.desconectar()
    logoffs = [c for c in LLAMADAS if "logoff" in c]
    assert len(logoffs) == 2 and logoffs[0][-1] == "logoff" and "-i" in logoffs[0]


def test_usuario_desconocido_se_rechaza(agente):
    with pytest.raises(ValueError):
        agente.conectar("intruso")
    assert not ProcFalso.instancias


def test_estado_autenticado_con_los_datos_del_policy_mapper(agente, monkeypatch):
    agente.conectar("profesor01")
    monkeypatch.setattr(agente, "_wpa_cli", lambda: {"suppPortStatus": "Authorized"})
    monkeypatch.setattr(ag, "http_json", lambda url, timeout=3: {"sessions": {MAC: {"vlan": 10, "rol": "Docente", "dpid": "of:1", "port": "2"}}})
    e = agente.estado()
    assert e["estado"] == "autenticado" and e["sesion"]["dpid"] == "of:1" and "VLAN 10" in e["mensaje"]
    assert {u["id"] for u in e["usuarios"]} >= {"profesor01", "support01"} and e["mac"] == MAC


def test_la_pagina_no_satura_al_policy_mapper_con_una_consulta_por_cada_refresco(agente, monkeypatch):
    agente.conectar("profesor01")
    monkeypatch.setattr(agente, "_wpa_cli", lambda: {"suppPortStatus": "Authorized"})
    llamadas = []
    sesiones = {"sessions": {MAC: {"vlan": 10, "rol": "Docente", "dpid": "of:1", "port": "2"}}}
    monkeypatch.setattr(ag, "http_json", lambda url, timeout=3: (llamadas.append(url), sesiones)[1])
    reloj = [1000.0]
    monkeypatch.setattr(ag.time, "monotonic", lambda: reloj[0])
    for _ in range(5):                       # cinco refrescos de la pagina en menos de 5 s
        assert agente.estado()["sesion"]["vlan"] == 10
        reloj[0] += 1.0
    assert len(llamadas) == 1
    reloj[0] += 10.0                         # pasado el tiempo de la cache, vuelve a preguntar
    agente.estado()
    assert len(llamadas) == 2


def test_si_el_policy_mapper_no_tiene_aun_la_sesion_no_se_guarda_esa_respuesta(agente, monkeypatch):
    agente.conectar("profesor01")
    monkeypatch.setattr(agente, "_wpa_cli", lambda: {"suppPortStatus": "Authorized"})
    respuestas = iter([{"sessions": {}}, {"sessions": {MAC: {"vlan": 10, "rol": "Docente"}}}])
    monkeypatch.setattr(ag, "http_json", lambda url, timeout=3: next(respuestas))
    assert agente.estado()["sesion"] is None            # todavia no esta
    assert agente.estado()["sesion"]["vlan"] == 10      # a la siguiente ya la ve, sin esperar a que caduque la cache


def test_estado_rechazado_muestra_el_motivo_del_policy_mapper(agente, monkeypatch):
    agente.conectar("alumno01")
    monkeypatch.setattr(agente, "_wpa_cli", lambda: {"Supplicant PAE state": "HELD"})
    ev = {"t": agente.desde + 1, "tipo": "rechazado", "mac": MAC, "detalle": "MAC registrada distinta de la fisica; lista negra 300 s"}
    monkeypatch.setattr(ag, "http_json", lambda url, timeout=3: [ev])
    e = agente.estado()
    assert e["estado"] == "rechazado" and "lista negra" in e["mensaje"]


def test_desconectado_sin_proceso(agente):
    assert agente.estado()["estado"] == "desconectado"


def test_vigilante_relanza_si_el_suplicante_se_cae(agente, monkeypatch):
    agente.conectar("profesor01")
    ProcFalso.instancias[0].vivo = False       # se cae solo
    lanzados = []
    monkeypatch.setattr(agente, "_lanzar", lambda uid: lanzados.append(uid))
    monkeypatch.setattr(ag.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()) if lanzados else None)
    with pytest.raises(KeyboardInterrupt):
        agente.vigilar()
    assert lanzados == ["profesor01"]


# ---------------------------------------------------------------- aviso de cierre de sesion al Policy Mapper

@pytest.fixture
def avisos(monkeypatch):
    """Registra los POST /sessions/logout del agente (sin red)."""
    enviados = []
    monkeypatch.setattr(ag, "http_post_json", lambda url, cuerpo, cabeceras=None, timeout=3:
                        (enviados.append((url, cuerpo, cabeceras)), (200, '{"status":"cerrada"}'))[1])
    return enviados


@pytest.fixture
def agente_con_clave(agente):
    agente.token = "clave-compartida"
    return agente


def test_desconectar_avisa_al_policy_mapper_despues_del_logoff(agente_con_clave, avisos):
    agente_con_clave.conectar("profesor01")
    agente_con_clave.desconectar()
    assert avisos == [("http://mapper.invalido/sessions/logout", {"mac": MAC}, {"X-Agente-Token": "clave-compartida"})]
    assert any("logoff" in c for c in LLAMADAS)
    assert any("Policy Mapper avisado (desconexion): sesion cerrada" in l for l in agente_con_clave.log)


def test_cambiar_de_identidad_cierra_la_sesion_anterior(agente_con_clave, avisos):
    agente_con_clave.conectar("profesor01")
    agente_con_clave.conectar("alumno01")
    assert len(avisos) == 1 and any("cambio de identidad" in l for l in agente_con_clave.log)


def test_conectar_la_primera_vez_no_avisa_de_ningun_cierre(agente_con_clave, avisos):
    agente_con_clave.conectar("profesor01")
    assert avisos == []


def test_sin_clave_no_avisa_pero_lo_deja_en_el_registro(agente, avisos):
    agente.conectar("profesor01")
    agente.desconectar()
    assert avisos == [] and any("Sin R1_TOKEN" in l for l in agente.log)


# ---------------------------------------------------------------- vigilante de enlace (ip link set <iface> down)

def _enlace(agente, monkeypatch, estados):
    it = iter(estados)
    monkeypatch.setattr(agente, "operstate", lambda: next(it))


def test_enlace_caido_avisa_al_policy_mapper_por_la_red_de_gestion(agente_con_clave, avisos, monkeypatch):
    agente_con_clave.conectar("profesor01")
    _enlace(agente_con_clave, monkeypatch, ["down"])
    assert agente_con_clave.revisar_enlace(True) is False
    assert len(avisos) == 1 and any("Enlace caido" in l for l in agente_con_clave.log)
    assert agente_con_clave.proc is not None      # el suplicante sigue vivo: reautentica solo al volver el enlace


def test_enlace_recuperado_hace_logoff_y_logon(agente_con_clave, avisos, monkeypatch):
    agente_con_clave.conectar("profesor01")
    LLAMADAS.clear()
    _enlace(agente_con_clave, monkeypatch, ["up"])
    assert agente_con_clave.revisar_enlace(False) is True
    assert [c[-1] for c in LLAMADAS] == ["logoff", "logon"] and avisos == []


def test_un_enlace_estable_no_hace_nada(agente_con_clave, avisos, monkeypatch):
    agente_con_clave.conectar("profesor01")
    LLAMADAS.clear()
    _enlace(agente_con_clave, monkeypatch, ["up", "up"])
    assert agente_con_clave.revisar_enlace(True) is True and agente_con_clave.revisar_enlace(True) is True
    assert avisos == [] and not LLAMADAS


def test_sin_conexion_deseada_el_enlace_no_dispara_avisos(agente_con_clave, avisos, monkeypatch):
    _enlace(agente_con_clave, monkeypatch, ["down", "up"])
    assert agente_con_clave.revisar_enlace(True) is False
    assert agente_con_clave.revisar_enlace(False) is True
    assert avisos == []


# ---------------------------------------------------------------- API HTTP (sobre un servidor real en 127.0.0.1)

@pytest.fixture
def servidor(agente):
    try:
        srv = ag.ThreadingHTTPServer(("127.0.0.1", 0), ag.Manejador)
    except OSError:
        pytest.skip("este entorno no permite abrir sockets locales")
    ag.Manejador.agente = agente
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, cuerpo):
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api_pagina_estado_y_conectar(servidor):
    with urllib.request.urlopen(servidor + "/", timeout=5) as r:
        assert b"Autenticacion de red" in r.read()
    assert _post(servidor + "/api/conectar", {"usuario": "alumno03"}) == (200, {"ok": True})
    with urllib.request.urlopen(servidor + "/api/estado", timeout=5) as r:
        assert json.loads(r.read())["usuario"] == "alumno03"
    assert _post(servidor + "/api/conectar", {"usuario": "intruso"})[0] == 400
    assert _post(servidor + "/api/desconectar", {}) == (200, {"ok": True})


def test_api_rechaza_un_host_que_no_es_local(servidor):
    req = urllib.request.Request(servidor + "/api/estado", headers={"Host": "evil.example.com"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403
