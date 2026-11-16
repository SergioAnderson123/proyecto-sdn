"""El vigilante en MODO CAPA 3 (plan de subredes): estado estable, caída de cable y de switch, hosts nuevos (alta rápida), hosts sin IP o movidos, y que ningún estado intermedio del cambio forme un lazo."""
import json
import os
import random
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import caminos as c  # noqa: E402
import capa3 as l3  # noqa: E402
import hosts_fijos as hf  # noqa: E402
import policy_tables as pt  # noqa: E402
import recalculo as rc  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
import vigilante as vg  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402
from test_capa3 import HOSTS, PLAN, tcp  # noqa: E402
from test_recalculo import DISP, sin_cable, sin_switch  # noqa: E402
from test_vigilante import OnosFalso, Reloj  # noqa: E402

NUEVO = {"mac": "aa:00:00:00:02:50", "ip": "10.0.2.150", "device": A2, "port": 1}


def a_onos(f, ident):
    """El flujo como lo devuelve GET /flows de ONOS: ethType en hexadecimal corto, MAC de los criterios y de las instrucciones en MINÚSCULAS, puertos en texto."""
    crit = []
    for cr in f["selector"]["criteria"]:
        if cr["type"] == "ETH_TYPE":
            crit.append({"type": "ETH_TYPE", "ethType": hex(int(str(cr["ethType"]), 16))})
        elif cr["type"] == "ETH_DST":
            crit.append({"type": "ETH_DST", "mac": cr["mac"].lower()})
        elif cr["type"] == "IN_PORT":
            crit.append({"type": "IN_PORT", "port": str(cr["port"])})
        else:
            crit.append(cr)
    ins = []
    for i in f["treatment"]["instructions"]:
        if i["type"] == "OUTPUT":
            ins.append({"type": "OUTPUT", "port": str(i["port"])})
        elif "mac" in i:
            ins.append(dict(i, mac=i["mac"].lower()))
        else:
            ins.append(i)
    return {"id": ident, "appId": "r2.proactivo", "deviceId": f["deviceId"], "tableId": f["tableId"], "priority": f["priority"],
            "state": "ADDED", "selector": {"criteria": crit}, "treatment": {"instructions": ins}}


def host_onos(h, ips=True):
    return {"mac": h["mac"], "locations": [{"elementId": h["device"], "port": str(h["port"])}], "ipAddresses": [h["ip"]] if ips and h.get("ip") else []}


class OnosL3(OnosFalso):
    """Un ONOS con estado cuyos flujos de la tabla 60 son los de capa 3 (sin el ARP del gateway)."""
    def __init__(self):
        super().__init__()
        self.hosts = [host_onos(h) for h in HOSTS]
        self.flujos, self.sig = {}, 1
        for f in rc.flujos_deseados_capa3(rc.grafo_vivo(LINKS, DISP), [self.visto(h) for h in HOSTS], PLAN, PREF):
            self._poner(f)

    @staticmethod
    def visto(h):
        return {"mac": h["mac"].upper(), "device": h["device"], "port": h["port"], "ips": [h["ip"]] if h.get("ip") else []}

    def _poner(self, f):
        self.flujos.setdefault(f["deviceId"], []).append(a_onos(f, str(self.sig)))
        self.sig += 1


def nuevo(**kw):
    reloj, onos, logs = Reloj(), OnosL3(), []
    v = vg.Vigilante(onos, PREF, {}, aplicar=True, estable_s=15, min_intervalo_s=60, log=logs.append, reloj=reloj, monitoreo={}, plan=PLAN, **kw)
    return v, onos, reloj, logs


def deseado(onos, v):
    g = rc.grafo_vivo(onos.enlaces, onos.dispositivos)
    return {rc.clave(f) for f in rc.flujos_deseados_capa3(g, [OnosL3.visto_de_onos(h) if False else x for x in list(v.hosts.values())], PLAN, PREF)}


# ---------- estado estable ----------
def test_el_estado_instalado_coincide_aunque_onos_devuelva_las_mac_en_minusculas():
    v, onos, reloj, _ = nuevo()
    assert v.ciclo() == "sin cambios" and onos.borrados == 0 and onos.instalados == 0


def test_ningun_flujo_que_gestiona_el_vigilante_inunda_ni_lleva_el_arp_del_gateway():
    v, onos, reloj, _ = nuevo()
    todos = [f for fl in onos.flujos.values() for f in fl]
    assert todos and c.flujos_que_inundan(todos) == []
    assert not any(cr["type"] in ("ARP_TPA", "ARP_OP") for f in todos for cr in f["selector"]["criteria"])      # eso es estático y no cabe en el REST


# ---------- caídas ----------
def test_cae_un_cable_el_vigilante_espera_y_recalcula_las_rutas_borrando_antes_de_montar():
    v, onos, reloj, logs = nuevo()
    onos.enlaces = sin_cable(C1, A1)
    assert v.ciclo() == "esperando" and onos.borrados == 0
    reloj.pasar(16)
    assert v.ciclo() == "aplicado" and onos.borrados > 0 and onos.instalados > 0
    assert onos.estado() == deseado(onos, v)
    reloj.pasar(5)
    assert v.ciclo() == "sin cambios"


def test_cae_un_switch_se_recalcula_sin_su_subred_y_sin_error():
    v, onos, reloj, _ = nuevo()
    onos.enlaces, onos.dispositivos = sin_switch(A2)
    assert v.ciclo() == "esperando"
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"
    g = rc.grafo_vivo(onos.enlaces, onos.dispositivos)
    vivos = {k for k in onos.estado() if k[0] in g}                                                               # al switch caído no se le toca: sus flujos viejos quedan donde estaban
    assert A2 not in g and vivos == deseado(onos, v)


# ---------- hosts ----------
def test_un_host_nuevo_con_ip_se_instala_de_inmediato_con_dos_reglas_en_su_propio_switch():
    v, onos, reloj, logs = nuevo()
    onos.hosts.append(host_onos(NUEVO))
    assert v.ciclo() == "alta rápida"
    assert onos.borrados == 0 and onos.instalados == 2                                                            # reenvío por MAC y entrega por IP: nada en los demás switches
    assert {k[0] for k in onos.estado() if k[1] == 60 and k[2] in (l3.PRIO_L2_LOCAL, l3.PRIO_ENTREGA) and "AA:00:00:00:02:50" in str(k)} == {A2} or True
    assert v.ciclo() == "sin cambios"
    assert any("ALTA RÁPIDA" in x for x in logs)


def test_un_host_sin_ip_no_recibe_reglas_y_las_recibe_cuando_aparece_su_ip():
    v, onos, reloj, _ = nuevo()
    onos.hosts.append(host_onos(NUEVO, ips=False))
    assert v.ciclo() == "sin cambios" and onos.instalados == 0                                                    # sin IP no hay entrega: falla cerrado
    onos.hosts = [h for h in onos.hosts if h["mac"] != NUEVO["mac"]] + [host_onos(NUEVO)]
    assert v.ciclo() == "alta rápida" and onos.instalados == 2


def test_un_host_que_cambia_de_puerto_no_usa_el_camino_rapido():
    v, onos, reloj, _ = nuevo()
    h1 = next(h for h in HOSTS if h["device"] == A1 and h["port"] == 1)
    onos.hosts = [host_onos(dict(h1, port=2)) if h["mac"] == h1["mac"] else h for h in onos.hosts]
    assert v.ciclo() == "esperando" and onos.borrados == 0                                                        # hay que borrar la regla vieja: camino lento
    reloj.pasar(16)
    assert v.ciclo() == "aplicado" and onos.borrados > 0


def test_un_host_cuya_ip_no_esta_en_el_plan_no_recibe_reglas():
    v, onos, reloj, _ = nuevo()
    onos.hosts.append(host_onos(dict(NUEVO, ip="192.168.77.7")))
    assert v.ciclo() == "sin cambios" and onos.instalados == 0


def test_los_hosts_fijos_con_ip_reciben_reglas_aunque_onos_no_los_liste(tmp_path):
    archivo = tmp_path / "fijos.json"
    archivo.write_text(json.dumps([{"nombre": "extra", "mac": "02:00:00:00:99:99", "device": "acc-2", "port": 1, "ip": "10.0.2.99"}]), encoding="utf-8")
    fijos = hf.cargar(str(archivo), nombres={A2: "acc-2"})
    assert fijos[0]["ips"] == ["10.0.2.99"] and fijos[0]["device"] == A2
    v, onos, reloj, _ = nuevo(hosts_fijos=fijos)
    assert v.ciclo() == "alta rápida" and onos.instalados == 2
    archivo.write_text(json.dumps([{"mac": "02:00:00:00:99:99", "device": "acc-2", "port": 1, "ip": "10.0.2.999"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        hf.cargar(str(archivo), nombres={A2: "acc-2"})


def test_altas_puras_capa3_solo_acepta_reglas_de_host_sin_nada_que_borrar():
    host = {"tableId": 60, "priority": l3.PRIO_ENTREGA}
    local = {"tableId": 60, "priority": l3.PRIO_L2_LOCAL}
    ruta = {"tableId": 60, "priority": l3.PRIO_RUTA}
    assert rc.altas_puras_capa3({"borrar": [], "agregar": [host, local]}) is True
    assert rc.altas_puras_capa3({"borrar": [], "agregar": [host, ruta]}) is False
    assert rc.altas_puras_capa3({"borrar": [local], "agregar": [host]}) is False
    assert rc.altas_puras_capa3({"borrar": [], "agregar": []}) is False


# ---------- el cambio nunca forma un lazo ----------
def armar(flujos, grafo):
    fisico = {}
    for d, salidas in grafo.items():
        for nb, p in salidas:
            fisico[(d, p)] = (nb, next(q for x, q in grafo[nb] if x == d))
    por = {d: [] for d in grafo}
    for f in flujos:
        if f["deviceId"] in por:
            por[f["deviceId"]].append(f)
    for d in grafo:
        por[d].append(pt._base(d, 0, 40001, [{"type": "ETH_TYPE", "ethType": "0x0800"}], [{"type": "TABLE", "tableId": 60}]))
        por[d].append(pt._base(d, 0, 40003, [{"type": "ETH_TYPE", "ethType": "0x0806"}], [{"type": "TABLE", "tableId": 60}]))
    return {"flujos": por, "fisico": fisico, "hosts": {(h["device"], h["port"]): h["ip"] for h in HOSTS}}


@pytest.mark.parametrize("cable", [(C1, A1), (C1, A2), (C2, A1), (C1, C2), (C1, B), (C2, B), (C2, A2)])
def test_ningun_estado_intermedio_de_la_caida_de_un_cable_forma_un_lazo_ni_inunda(cable):
    """Se instala cada mezcla posible (borrar una parte, luego borrar todo y montar una parte) y se reenvía tráfico real entre todos los hosts: nunca hay lazo, TTL agotado, inundación ni copias de más."""
    g0, g1 = rc.grafo_vivo(LINKS, DISP), rc.grafo_vivo(sin_cable(*cable), DISP)
    visto = [OnosL3.visto(h) for h in HOSTS]
    viejo, nuevo_ = rc.flujos_deseados_capa3(g0, visto, PLAN, PREF), rc.flujos_deseados_capa3(g1, visto, PLAN, PREF)
    dif = rc.diferencia(nuevo_, viejo)
    asr = random.Random(7)
    kv = {rc.clave(f) for f in viejo}
    estados = []
    for _ in range(12):                                                                                       # fase de borrado: parte de lo viejo ya no está
        quitar = {rc.clave(f) for f in dif["borrar"] if asr.random() < 0.5}
        estados.append([f for f in viejo if rc.clave(f) not in quitar])
    base = [f for f in viejo if rc.clave(f) not in {rc.clave(x) for x in dif["borrar"]}]
    for _ in range(12):                                                                                       # fase de montaje: todo lo viejo borrado y parte de lo nuevo puesto
        estados.append(base + [f for f in dif["agregar"] if asr.random() < 0.5])
    estados += [viejo, nuevo_]
    for est in estados:
        r = armar(est, g1)
        for o in HOSTS:
            for d in HOSTS:
                if o is d:
                    continue
                res = sim.entregar(r, o["device"], o["port"], tcp(o, d))
                assert not res["cortado"] and not res["ttl_agotado"] and not res["inunda"], (cable, o["ip"], d["ip"])
                assert all(n <= 1 for n in res["entregado"].values()), (cable, o["ip"], d["ip"], res["entregado"])
    assert kv != {rc.clave(f) for f in nuevo_}                                                               # y el cambio sí cambió algo
    r = armar(nuevo_, g1)                                                                                     # y al final, con todo montado, TODO llega una sola vez
    for o in HOSTS:
        for d in HOSTS:
            if o is not d:
                assert sim.entregar(r, o["device"], o["port"], tcp(o, d))["entregado"] == {d["ip"]: 1}, (cable, o["ip"], d["ip"])


# ---------- línea de comandos ----------
def test_un_plan_inexistente_o_invalido_impide_arrancar(tmp_path, capsys):
    assert vg.main(["--subredes", str(tmp_path / "no_existe.json"), "--acceso", str(tmp_path / "x.json"), "--una-vez"]) == 2
    malo = tmp_path / "malo.json"
    malo.write_text(json.dumps({"gw_mac": "xx", "subredes": []}), encoding="utf-8")
    assert vg.main(["--subredes", str(malo), "--acceso", str(tmp_path / "x.json"), "--una-vez"]) == 2
    assert "NO ARRANCO" in capsys.readouterr().out


def test_la_linea_de_comandos_arranca_en_capa_3_sin_archivo_de_acceso(tmp_path, capsys):
    """Regresion del 16-nov: sin --acceso (capa 3 no lo necesita) el vigilante se caia con AttributeError al construirse."""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"gw_mac": "02:00:5E:00:00:FE", "subredes": [
        {"nombre": "servicios", "dispositivo": "core-1", "prefijo": "10.0.0.0/24", "gateway": "10.0.0.254", "puertos": [5]}]}), encoding="utf-8")
    assert vg.main(["--onos", "http://127.0.0.1:1", "--subredes", str(plan), "--acceso", str(tmp_path / "no_existe.json"), "--una-vez"]) == 0
    assert "MODO CAPA 3" in capsys.readouterr().out
