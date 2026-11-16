"""El vigilante en MODO AISLADO (capa 3 en una tabla aparte, p. ej. la 62, con subredes de prueba): solo puede tocar esa tabla y sus reglas de entrada de la tabla 0; la producción (tabla 60 y el resto de la tabla 0) queda intacta."""
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import recalculo as rc  # noqa: E402
import vigilante as vg  # noqa: E402
from test_caminos import PREF, A1  # noqa: E402
from test_capa3 import HOSTS, PLAN  # noqa: E402
from test_vigilante import OnosFalso, Reloj  # noqa: E402
from test_vigilante_capa3 import a_onos, host_onos  # noqa: E402

TABLA = 62


class OnosAislado(OnosFalso):
    """ONOS con la producción de hoy (L2 en la tabla 60) y una regla propia de otro módulo en la tabla 0 con la misma prioridad de entrada pero otra MAC."""
    def __init__(self):
        super().__init__()
        self.hosts = [host_onos(h) for h in HOSTS]
        self.ajena = {"priority": rc.PRIO_ENTRADA, "timeout": 0, "isPermanent": True, "deviceId": A1, "tableId": 0,
                      "selector": {"criteria": [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": "AA:BB:CC:00:00:01"}]},
                      "treatment": {"instructions": [{"type": "TABLE", "tableId": 60}]}}
        self._poner(self.ajena)
        self.produccion = set(self.estado())

    def _poner(self, f):
        self.flujos.setdefault(f["deviceId"], []).append(a_onos(f, str(self.sig)))
        self.sig += 1

    def aislados(self):
        return {k for k in self.estado() if k not in self.produccion}


def nuevo(estable=0, **kw):
    reloj, onos, logs = Reloj(), OnosAislado(), []
    v = vg.Vigilante(onos, PREF, {}, aplicar=True, estable_s=estable, min_intervalo_s=0, log=logs.append, reloj=reloj, monitoreo={}, plan=PLAN, tabla=TABLA, **kw)
    return v, onos, reloj, logs


def test_con_estable_cero_aplica_en_un_ciclo_y_solo_en_la_tabla_aislada_y_su_entrada():
    v, onos, _, _ = nuevo()
    assert v.ciclo() == "aplicado"
    nuevos = onos.aislados()
    assert nuevos and onos.borrados == 0
    assert {k[1] for k in nuevos} <= {0, TABLA}                      # nunca la 60
    assert all(k[1] == TABLA or (k[1] == 0 and k[2] == rc.PRIO_ENTRADA) for k in nuevos)
    assert onos.produccion <= onos.estado()                           # la producción sigue entera


def test_el_segundo_ciclo_no_cambia_nada_y_la_regla_ajena_de_la_tabla_0_no_se_reconoce_como_suya():
    v, onos, _, _ = nuevo()
    v.ciclo()
    antes = onos.estado()
    assert v.ciclo() == "sin cambios" and onos.estado() == antes
    assert not rc.es_entrada(onos.ajena, PLAN)


def test_con_estable_positivo_espera_antes_de_aplicar():
    v, onos, reloj, _ = nuevo(estable=15)
    assert v.ciclo() == "esperando" and not onos.aislados()


def test_retirar_deja_todo_como_estaba_y_no_toca_la_produccion():
    v, onos, reloj, _ = nuevo()
    inicial = onos.estado()
    v.ciclo()
    r = vg.Vigilante(onos, PREF, {}, aplicar=True, estable_s=0, min_intervalo_s=0, log=lambda *_: None, reloj=reloj, monitoreo={}, plan=PLAN, tabla=TABLA, retirar=True)
    reloj.pasar(120)
    assert r.ciclo() == "aplicado"
    assert onos.estado() == inicial


def test_en_modo_aislado_un_cable_caido_recalcula_solo_lo_aislado():
    from test_vigilante import cae_el_cable_core1_acc1
    v, onos, reloj, _ = nuevo()
    v.ciclo()
    cae_el_cable_core1_acc1(onos)
    reloj.pasar(120)
    assert v.ciclo() == "aplicado"
    assert onos.produccion <= onos.estado()                           # lo de producción sigue entero tras recalcular lo aislado


def test_linea_de_comandos_rechaza_combinaciones_peligrosas(tmp_path, capsys):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({"gw_mac": "02:00:5E:99:99:FE", "subredes": [
        {"nombre": "s", "dispositivo": "core-1", "prefijo": "10.99.0.0/24", "gateway": "10.99.0.254", "puertos": [5]}]}), encoding="utf-8")
    base = ["--onos", "http://127.0.0.1:1", "--una-vez"]
    assert vg.main(base + ["--subredes", str(plan), "--retirar"]) == 2                         # retirar en la 60
    assert vg.main(base + ["--subredes", str(plan), "--tabla", "50"]) == 2                     # fuera de rango
    assert vg.main(base + ["--tabla", "62"]) == 2                                              # sin plan
    assert vg.main(base + ["--subredes", str(plan), "--tabla", "62"]) == 0                     # válida (ONOS no responde: no hace nada)
    assert "AISLADO en la tabla 62" in capsys.readouterr().out
