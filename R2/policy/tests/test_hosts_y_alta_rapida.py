"""Hosts fijos, alta rápida de hosts nuevos y copia opcional del ARP al controlador (sin red, con el ONOS falso del vigilante)."""
import itertools
import json
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import caminos as c  # noqa: E402
import flujos_proactivos as fp  # noqa: E402
import hosts_fijos as hf  # noqa: E402
import recalculo as rc  # noqa: E402
import vigilante as vg  # noqa: E402
import simulador_pipeline as sim  # noqa: E402
from instalar_proactivo import flujo_arp_tabla0  # noqa: E402
from test_caminos import LINKS, PREF, A1, A2, B, C1, C2  # noqa: E402
from test_flujos_proactivos import ACCESO, HOSTS, _enlaces_por_puerto  # noqa: E402
from test_recalculo import DISP, TODOS, sin_cable  # noqa: E402
from test_vigilante import OnosFalso, Reloj, nuevo  # noqa: E402

NUEVA = "AA:00:00:00:00:77"
G = c.grafo_desde_links(LINKS)


def host_onos(mac, dev, puerto):
    return {"mac": mac, "locations": [{"elementId": dev, "port": str(puerto)}], "ipAddresses": []}


# ---------- alta rápida ----------
def test_un_host_nuevo_se_instala_de_inmediato_sin_esperar_la_estabilidad():
    v, onos, reloj, logs = nuevo()
    assert v.ciclo() == "sin cambios"
    onos.hosts.append(host_onos(NUEVA, A2, 2))
    assert v.ciclo() == "alta rápida"                                           # sin esperar los 15 s
    assert onos.borrados == 0 and onos.instalados == len(TODOS)                  # una regla por switch, nada que borrar
    assert any("ALTA RÁPIDA" in x for x in logs)
    assert v.ciclo() == "sin cambios"


def test_el_alta_rapida_no_consume_el_intervalo_minimo_de_un_cambio_de_topologia():
    v, onos, reloj, _ = nuevo()
    onos.hosts.append(host_onos(NUEVA, A2, 2))
    assert v.ciclo() == "alta rápida"
    onos.enlaces = sin_cable(C1, A1)                                            # justo después cae un cable
    v.ciclo()
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"                                              # se recalcula sin esperar 60 s por el alta


def test_sin_aplicar_el_alta_rapida_solo_avisa():
    v, onos, reloj, logs = nuevo(aplicar=False)
    onos.hosts.append(host_onos(NUEVA, A2, 2))
    assert v.ciclo() == "esperando"
    reloj.pasar(16)
    assert v.ciclo() == "simulación" and onos.instalados == 0


def test_un_host_que_se_mueve_no_usa_el_camino_rapido():
    v, onos, reloj, _ = nuevo()
    mac = HOSTS[0]["mac"]
    onos.hosts = [host_onos(mac, A2, 2)] + [h for h in onos.hosts if h["mac"] != mac]              # la misma MAC en otro puerto
    assert v.ciclo() == "esperando" and onos.borrados == 0                                           # hay que borrar reglas viejas: camino lento
    reloj.pasar(16)
    assert v.ciclo() == "aplicado" and onos.borrados > 0


def test_un_host_nuevo_junto_con_una_caida_de_cable_va_por_el_camino_lento():
    v, onos, reloj, _ = nuevo()
    onos.hosts.append(host_onos(NUEVA, A2, 2))
    onos.enlaces = sin_cable(C1, A1)
    assert v.ciclo() == "esperando"
    reloj.pasar(16)
    assert v.ciclo() == "aplicado"


def test_altas_puras_exige_solo_unicast_de_macs_nuevas():
    g = rc.grafo_vivo(LINKS, DISP)
    instalados = [dict(f, id=str(i)) for i, f in enumerate(rc.flujos_deseados(g, HOSTS, ACCESO, PREF))]
    mas = rc.flujos_deseados(g, HOSTS + [{"mac": NUEVA, "device": A2, "port": 2}], ACCESO, PREF)
    dif = rc.diferencia(mas, instalados)
    assert rc.altas_puras(dif, instalados) is True
    assert rc.altas_puras({"borrar": [instalados[0]], "agregar": dif["agregar"]}, instalados) is False         # algo que borrar
    assert rc.altas_puras({"borrar": [], "agregar": []}, instalados) is False                                   # nada que hacer
    difusion = [f for f in instalados if f["priority"] == fp.PRIO_DIFUSION][:1]
    assert rc.altas_puras({"borrar": [], "agregar": difusion}, []) is False                                     # no es unicast
    una_mac_conocida = rc.flujos_deseados(g, [{"mac": HOSTS[0]["mac"], "device": B, "port": 3}], ACCESO, PREF)
    assert rc.altas_puras({"borrar": [], "agregar": [f for f in una_mac_conocida if f["priority"] == fp.PRIO_UNICAST]}, instalados) is False


def test_ningun_estado_intermedio_del_alta_rapida_forma_un_lazo():
    """Se instalan las 5 reglas del host nuevo en TODOS los órdenes y, tras cada una, un paquete hacia esa MAC desde cualquier switch o llega o se descarta."""
    g = rc.grafo_vivo(LINKS, DISP)
    nuevas = [f for f in rc.flujos_deseados(g, HOSTS + [{"mac": NUEVA, "device": A2, "port": 2}], ACCESO, PREF)
              if f["selector"]["criteria"] == [{"type": "ETH_DST", "mac": NUEVA}]]
    assert len(nuevas) == len(TODOS)
    vecino = _enlaces_por_puerto()
    for orden in itertools.permutations(nuevas):
        puestas = {}
        for f in orden:
            puestas[f["deviceId"]] = int(f["treatment"]["instructions"][0]["port"])
            for origen in TODOS:
                actual, visto = origen, set()
                while actual in puestas and (actual, puestas[actual]) in vecino:
                    assert actual not in visto, f"lazo con orden {[x['deviceId'] for x in orden]}"
                    visto.add(actual)
                    actual = vecino[(actual, puestas[actual])][0]


# ---------- hosts fijos ----------
def test_un_host_fijo_recibe_reglas_aunque_onos_no_lo_liste():
    server = {"mac": "52:54:00:AA:BB:01", "device": C1, "port": 8, "ips": [], "nombre": "server-ens4"}
    v, onos, reloj, _ = nuevo(hosts_fijos=[server])
    assert v.ciclo() == "alta rápida"
    nuevos = [f for fl in onos.flujos.values() for f in fl if f["selector"]["criteria"] == [{"type": "ETH_DST", "mac": server["mac"].lower()}]]
    assert len(nuevos) == len(TODOS)
    assert v.ciclo() == "sin cambios"
    onos.hosts = []                                                              # ONOS no lista nada: el fijo sigue
    assert v.ciclo() == "sin cambios"


def test_un_host_fijo_manda_sobre_lo_que_diga_onos():
    mac = HOSTS[0]["mac"]
    v, onos, reloj, _ = nuevo(hosts_fijos=[{"mac": mac, "device": B, "port": 3, "ips": [], "nombre": "x"}])
    v.ciclo()
    assert v.hosts[mac.upper()]["device"] == B


def test_cargar_hosts_fijos_valida_el_archivo(tmp_path):
    def archivo(contenido):
        p = tmp_path / "h.json"
        p.write_text(json.dumps(contenido), encoding="utf-8")
        return str(p)

    ok = hf.cargar(archivo([{"nombre": "s", "mac": "52:54:00:aa:bb:01", "device": "core-1", "port": 5}]))
    assert ok[0]["mac"] == "52:54:00:AA:BB:01" and ok[0]["device"] == "of:00007e3401ecd842" and ok[0]["port"] == 5
    assert hf.cargar(archivo([{"mac": "52:54:00:aa:bb:01", "device": "of:00007e3401ecd842", "port": "5"}]))[0]["port"] == 5
    for mal in ({"mac": "zz", "device": "core-1", "port": 5}, {"mac": "52:54:00:aa:bb:01", "device": "nube", "port": 5},
                {"mac": "52:54:00:aa:bb:01", "device": "core-1"}, {"mac": "52:54:00:aa:bb:01", "device": "core-1", "port": "x"}):
        with pytest.raises(ValueError):
            hf.cargar(archivo([mal]))
    with pytest.raises(ValueError):
        hf.cargar(archivo({"mac": "52:54:00:aa:bb:01"}))
    repetida = {"mac": "52:54:00:aa:bb:01", "device": "core-1", "port": 5}
    with pytest.raises(ValueError):
        hf.cargar(archivo([repetida, dict(repetida, mac="52:54:00:AA:BB:01")]))


# ---------- copia del ARP al controlador ----------
def _con_tabla0(copiar):
    """Tabla 60 (difusión) + la regla de la tabla 0 que manda el ARP a la 60 (fase C), por switch."""
    return {d: [flujo_arp_tabla0(d)] + [f for f in _difusion(copiar) if f["deviceId"] == d] for d in TODOS}


def _difusion(copiar):
    return fp.flujos_difusion(G, C1, ACCESO, PREF, copiar_arp=copiar)


def _salidas(f):
    return [i["port"] for i in f["treatment"]["instructions"]]


def test_por_defecto_el_arp_no_se_copia_al_controlador():
    assert not any("CONTROLLER" in _salidas(f) for f in _difusion(False)) and _difusion(False) == fp.flujos_difusion(G, C1, ACCESO, PREF)


def test_la_copia_solo_va_en_las_entradas_de_acceso():
    troncales = {d: {p for _, p in G[d]} for d in G}
    for f in _difusion(True):
        d = f["deviceId"]
        entrada = int(next(cr["port"] for cr in f["selector"]["criteria"] if cr["type"] == "IN_PORT"))
        assert ("CONTROLLER" in _salidas(f)) == (entrada not in troncales[d]), (d, entrada)
    assert c.flujos_que_inundan(_difusion(True)) == []


@pytest.mark.parametrize("origen,dev,puerto", [("alumno", A1, 1), ("invitado", A2, 1), ("jhon", B, 3)])
def test_con_la_copia_cada_arp_sube_una_sola_vez_y_llega_igual_a_los_demas(origen, dev, puerto):
    hosts = {(A1, 1): "alumno", (A2, 1): "invitado", (B, 3): "jhon", (C1, 8): "srv1", (C2, 8): "srv2"}
    red = {"flujos": _con_tabla0(True), "fisico": {(d, p): (nb, q) for (d, p), (nb, q) in _enlaces_por_puerto().items()}, "hosts": hosts}
    pkt = {"eth_src": "aa:00:00:00:00:0" + origen[0], "eth_dst": "ff:ff:ff:ff:ff:ff", "eth_type": 0x0806, "vlan": None}
    res = sim.entregar(red, dev, puerto, pkt)
    assert res["controlador"] == [dev]                                           # una sola copia, en su primer switch
    assert res["entregado"] == {h: 1 for h in hosts.values() if h != origen} and not res["cortado"]
    sin = sim.entregar(dict(red, flujos=_con_tabla0(False)), dev, puerto, pkt)
    assert sin["controlador"] == [] and sin["entregado"] == res["entregado"]


def test_el_vigilante_con_copia_arp_reconoce_su_propio_estado_y_no_lo_cambia():
    reloj, onos, logs = Reloj(), OnosFalso(), []
    g = rc.grafo_vivo(LINKS, DISP)
    onos.flujos = {}
    for f in rc.flujos_deseados(g, HOSTS, ACCESO, PREF, copiar_arp=True):
        onos._poner(f)
    v = vg.Vigilante(onos, PREF, ACCESO, aplicar=True, log=logs.append, reloj=reloj, monitoreo={}, copiar_arp=True)
    assert v.ciclo() == "sin cambios"
    v2 = vg.Vigilante(onos, PREF, ACCESO, aplicar=True, log=logs.append, reloj=reloj, monitoreo={}, copiar_arp=False)
    assert v2.ciclo() == "esperando"                                              # apagar la copia es un cambio de difusión: camino lento
