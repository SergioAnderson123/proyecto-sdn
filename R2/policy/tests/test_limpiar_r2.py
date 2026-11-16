"""Limpieza de los flujos antiguos de R2 en switches concretos."""
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import limpiar_r2 as lr  # noqa: E402
from plan_arboles import NOMBRES  # noqa: E402

POR = {v: k for k, v in NOMBRES.items()}
C1, C2, B, A1 = POR["core-1"], POR["core-2"], POR["borde"], POR["acc-1"]


def flujo(i, app, dev, tabla=0, salida="NORMAL"):
    return {"id": str(i), "appId": app, "deviceId": dev, "tableId": tabla, "priority": 100,
            "treatment": {"instructions": [{"type": "OUTPUT", "port": salida}]}}


class OnosFalso:
    def __init__(self):
        self.flujos = {C1: [flujo(1, "org.campus.r2", C1), flujo(2, "org.campus.r2", C1, 20), flujo(3, "r2.proactivo", C1, 60, "2"),
                            flujo(4, "org.onosproject.core", C1, 0, "CONTROLLER"), flujo(5, "org.onosproject.rest", C1)],
                       A1: [flujo(6, "org.campus.r2", A1)]}
        self.borrados = []

    def get(self, ruta):
        dev = ruta.rsplit("/", 1)[1]
        return {"flows": list(self.flujos.get(dev, []))}

    def borrar(self, dpid, flow_id):
        if flow_id == "404":
            raise urllib.error.HTTPError("u", 404, "no", {}, None)
        self.borrados.append((dpid, flow_id))
        self.flujos[dpid] = [f for f in self.flujos[dpid] if f["id"] != flow_id]


def test_sin_aplicar_solo_cuenta_y_no_borra():
    onos, logs = OnosFalso(), []
    assert lr.limpiar(onos, [C1], aplicar=False, log=logs.append) == {C1: 2} and onos.borrados == []
    assert "2 flujos de R2 por tabla {0: 1, 20: 1}; 2 con NORMAL" in logs[0]


def test_aplicando_borra_solo_los_de_r2_de_los_switches_indicados():
    onos = OnosFalso()
    lr.limpiar(onos, [C1], aplicar=True, log=lambda *_: None)
    assert sorted(i for _, i in onos.borrados) == ["1", "2"]                      # los de la tabla 60, ONOS y la sesion (rest) no se tocan
    assert {f["id"] for f in onos.flujos[C1]} == {"3", "4", "5"} and len(onos.flujos[A1]) == 1     # otro switch intacto


def test_un_404_al_borrar_no_detiene_la_limpieza():
    onos = OnosFalso()
    onos.flujos[C2] = [flujo(404, "org.campus.r2", C2), flujo(7, "org.campus.r2", C2)]
    assert lr.limpiar(onos, [C2], aplicar=True, log=lambda *_: None) == {C2: 2} and onos.borrados == [(C2, "7")]


def test_nombres_de_switch_y_cli(capsys):
    assert lr._dpid("core-1") == C1 and lr._dpid(C1) == C1
    with pytest.raises(ValueError):
        lr._dpid("nube")
    assert lr.main(["--dispositivos", "nube"]) == 2
    assert "DETENIDO" in capsys.readouterr().out
