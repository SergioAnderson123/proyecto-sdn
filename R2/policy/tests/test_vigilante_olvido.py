"""Memoria de hosts del vigilante: un host que ONOS deja de listar NO se olvida (ONOS puede perder hosts un momento), salvo (a) en capa 3 si otro host listado reclama su IP y
(b) si se pidio olvidar tras un tiempo sin aparecer (`olvidar_host_s`). Los hosts fijos nunca se olvidan. Caso real (22-nov): un host borrado de ONOS dejaba su regla /32 para siempre."""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import hosts_fijos as hf  # noqa: E402,F401
import vigilante as vg  # noqa: E402
from test_capa3 import HOSTS  # noqa: E402
from test_vigilante_capa3 import NUEVO, host_onos, nuevo  # noqa: E402

AUSENTE = dict(NUEVO)                                                                  # aa:00:00:00:02:50, 10.0.2.150, acc-2/1
OTRO = {"mac": "aa:00:00:00:02:77", "ip": "10.0.2.150", "device": NUEVO["device"], "port": 1}      # otro equipo que recibe esa misma IP


def con_host_extra(**kw):
    v, onos, reloj, logs = nuevo(**kw)
    onos.hosts.append(host_onos(AUSENTE))
    assert v.ciclo() == "alta rápida"
    return v, onos, reloj, logs


def regla_de(onos, ip):
    return [f for fl in onos.flujos.values() for f in fl if any(c.get("ip") == f"{ip}/32" for c in f["selector"]["criteria"])]


def test_sin_los_parametros_nuevos_un_host_borrado_de_onos_sigue_recordado_y_con_su_regla():
    v, onos, reloj, _ = con_host_extra()
    onos.hosts = [h for h in onos.hosts if h["mac"] != AUSENTE["mac"]]
    reloj.pasar(100_000)
    assert v.ciclo() == "sin cambios"
    assert AUSENTE["mac"].upper() in v.hosts and regla_de(onos, AUSENTE["ip"])


def test_si_otro_host_listado_reclama_la_ip_el_ausente_se_olvida_y_su_regla_pasa_a_otro():
    v, onos, reloj, logs = con_host_extra()
    onos.hosts = [h for h in onos.hosts if h["mac"] != AUSENTE["mac"]] + [host_onos(OTRO)]       # el ausente ya no esta y otro equipo tiene su IP
    estado = v.ciclo()
    assert AUSENTE["mac"].upper() not in v.hosts and OTRO["mac"].upper() in v.hosts
    assert any("OLVIDO" in x and AUSENTE["mac"].upper() in x and "10.0.2.150" in x and OTRO["mac"].upper() in x for x in logs)
    assert estado in ("esperando", "aplicado", "alta rápida")
    reloj.pasar(16)
    v.ciclo()
    reglas = regla_de(onos, "10.0.2.150")
    assert len(reglas) == 1, "una sola regla /32 para esa IP, no dos con el mismo selector"
    assert any(i.get("mac", "").upper() == OTRO["mac"].upper() for i in reglas[0]["treatment"]["instructions"])


def test_un_host_ausente_con_otra_ip_no_se_olvida_porque_otro_use_una_ip_distinta():
    v, onos, reloj, logs = con_host_extra()
    onos.hosts = [h for h in onos.hosts if h["mac"] != AUSENTE["mac"]] + [host_onos(dict(OTRO, ip="10.0.2.160"))]
    assert v.ciclo() == "alta rápida"
    assert AUSENTE["mac"].upper() in v.hosts and not any("OLVIDO" in x for x in logs)


def test_un_host_que_sigue_listado_nunca_se_olvida_aunque_comparta_ip_con_otro():
    v, onos, reloj, logs = con_host_extra()
    onos.hosts.append(host_onos(OTRO))                                                # los dos listados con la misma IP: es un conflicto real, no se decide aqui
    v.ciclo()
    assert AUSENTE["mac"].upper() in v.hosts and OTRO["mac"].upper() in v.hosts and not any("OLVIDO" in x for x in logs)


def test_con_olvidar_host_un_host_que_onos_no_lista_se_olvida_al_pasar_el_tiempo_y_se_retira_su_regla():
    v, onos, reloj, logs = con_host_extra(olvidar_host_s=600)
    onos.hosts = [h for h in onos.hosts if h["mac"] != AUSENTE["mac"]]
    assert v.ciclo() == "sin cambios"                                                 # ONOS no lo lista, pero todavia no pasaron 600 s
    reloj.pasar(599)
    assert v.ciclo() == "sin cambios" and AUSENTE["mac"].upper() in v.hosts
    reloj.pasar(2)
    assert v.ciclo() == "esperando"                                                   # se olvida: sus dos reglas pasan a "sobra" y esperan lo de siempre
    assert AUSENTE["mac"].upper() not in v.hosts and any("OLVIDO" in x and "ONOS no lo lista desde hace" in x for x in logs)
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"
    assert regla_de(onos, AUSENTE["ip"]) == []


def test_un_host_que_vuelve_a_aparecer_reinicia_su_cuenta():
    v, onos, reloj, logs = con_host_extra(olvidar_host_s=600)
    host = host_onos(AUSENTE)
    onos.hosts = [h for h in onos.hosts if h["mac"] != AUSENTE["mac"]]
    reloj.pasar(500)
    v.ciclo()
    onos.hosts.append(host)                                                           # ONOS lo vuelve a listar
    v.ciclo()
    onos.hosts = [h for h in onos.hosts if h["mac"] != AUSENTE["mac"]]
    reloj.pasar(500)
    v.ciclo()
    assert AUSENTE["mac"].upper() in v.hosts and not any("OLVIDO" in x for x in logs)


def test_onos_sin_hosts_un_momento_no_hace_olvidar_a_todos_antes_del_plazo():
    v, onos, reloj, logs = nuevo(olvidar_host_s=600)
    antes = dict(v.hosts)
    v.ciclo()
    onos.hosts = []                                                                   # ONOS reiniciando: lista vacia
    reloj.pasar(120)
    assert v.ciclo() == "sin cambios"
    assert len(v.hosts) == len(HOSTS) and not any("OLVIDO" in x for x in logs) and antes is not None


def test_los_hosts_fijos_nunca_se_olvidan(tmp_path):
    import json
    archivo = tmp_path / "fijos.json"
    archivo.write_text(json.dumps([{"nombre": "extra", "mac": "02:00:00:00:99:99", "device": "acc-2", "port": 1, "ip": "10.0.2.99"}]), encoding="utf-8")
    fijos = hf.cargar(str(archivo), nombres={NUEVO["device"]: "acc-2"})
    v, onos, reloj, logs = nuevo(hosts_fijos=fijos, olvidar_host_s=10)
    v.ciclo()
    onos.hosts = [host_onos(OTRO | {"ip": "10.0.2.99"})]                              # otro equipo reclama la IP del fijo y ONOS no lista el fijo
    reloj.pasar(1000)
    v.ciclo()
    assert "02:00:00:00:99:99" in v.hosts and not any("OLVIDO" in x and "02:00:00:00:99:99" in x for x in logs)


def test_la_linea_de_comandos_acepta_olvidar_host(tmp_path, capsys):
    import json
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"gw_mac": "02:00:5E:00:00:FE", "subredes": [
        {"nombre": "servicios", "dispositivo": "core-1", "prefijo": "10.0.0.0/24", "gateway": "10.0.0.254", "puertos": [5]}]}), encoding="utf-8")
    assert vg.main(["--onos", "http://127.0.0.1:1", "--subredes", str(plan), "--acceso", str(tmp_path / "x.json"), "--olvidar-host", "600", "--una-vez"]) == 0
    assert "MODO CAPA 3" in capsys.readouterr().out
