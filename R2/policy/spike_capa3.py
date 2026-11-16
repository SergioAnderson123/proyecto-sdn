"""spike_capa3.py: PRUEBA DE VIABILIDAD de la capa 3 contra ONOS: ¿el REST de ONOS 2.7 acepta lo que necesitan los flujos del gateway virtual?

    python3 spike_capa3.py --dispositivo acc-1                  # SOLO MUESTRA los flujos de prueba (JSON); no toca nada
    python3 spike_capa3.py --dispositivo acc-1 --aplicar        # los envía de a uno y dice cuáles acepta ONOS y cuáles rechaza (con el mensaje)
    python3 spike_capa3.py --quitar --aplicar                   # borra todo lo de la prueba (appId r2.capa3spike)

Los flujos de prueba son INERTES: van a la tabla 61 (a la que nada salta), con prioridad 1 y sin salida real, así que ningún paquete puede llegar a ellos. Sirven solo para saber qué
criterios e instrucciones acepta el REST: ARP_OP, ARP_TPA, DEC_TTL, modificación de campos ARP, salida por IN_PORT y las extensiones de Nicira (move). Lo que se rechace decide cómo se instalan los
flujos de ARP del gateway (ver docs/DISENO_CAPA3.md, sección 'Prueba de viabilidad').
"""
import argparse
import json
import sys
import urllib.error

from instalar_proactivo import Onos
from plan_arboles import NOMBRES

APP_ID = "r2.capa3spike"
TABLA_INERTE = 61


def _flujo(dpid, nombre, criterios, instrucciones):
    return {"priority": 1, "timeout": 0, "isPermanent": True, "deviceId": dpid, "tableId": TABLA_INERTE,
            "treatment": {"instructions": instrucciones}, "selector": {"criteria": criterios}, "_prueba": nombre}


def pruebas(dpid):
    base = [{"type": "ETH_TYPE", "ethType": "0x0806"}]
    return [
        _flujo(dpid, "criterio ARP_OP", base + [{"type": "ARP_OP", "op": 1}], []),
        _flujo(dpid, "criterio ARP_TPA", base + [{"type": "ARP_TPA", "ip": "10.99.0.254/32"}], []),
        _flujo(dpid, "instruccion DEC_TTL", [{"type": "ETH_TYPE", "ethType": "0x0800"}], [{"type": "L3MODIFICATION", "subtype": "DEC_TTL"}]),
        _flujo(dpid, "instruccion ARP_OP", base, [{"type": "L3MODIFICATION", "subtype": "ARP_OP", "op": 2}]),
        _flujo(dpid, "instruccion ARP_SHA", base, [{"type": "L3MODIFICATION", "subtype": "ARP_SHA", "mac": "02:00:5E:00:00:FE"}]),
        _flujo(dpid, "instruccion ARP_SPA", base, [{"type": "L3MODIFICATION", "subtype": "ARP_SPA", "ip": "10.99.0.254"}]),
        _flujo(dpid, "salida IN_PORT", base, [{"type": "OUTPUT", "port": "IN_PORT"}]),
        _flujo(dpid, "extension Nicira (nombre)", base, [{"type": "EXTENSION", "extensionType": "NICIRA_MOVE_ETH_SRC_TO_DST"}]),
        _flujo(dpid, "extension Nicira (objeto)", base, [{"type": "EXTENSION", "extension": {"extensionTypeNumber": "NICIRA_MOVE_ETH_SRC_TO_DST"}}]),
    ]


def enviar(onos, f):
    """(aceptado, detalle). Quita la clave de ayuda `_prueba` antes de enviar."""
    cuerpo = {k: v for k, v in f.items() if k != "_prueba"}
    try:
        onos._pedir("POST", f"/flows?appId={APP_ID}", {"flows": [cuerpo]})
        return True, "aceptado"
    except urllib.error.HTTPError as e:
        try:
            detalle = e.read().decode()[:300]
        except Exception:  # noqa: BLE001
            detalle = ""
        return False, f"HTTP {e.code} {detalle}"


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--dispositivo", default="acc-1", help="nombre (acc-1, core-1...) o deviceId")
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--quitar", action="store_true")
    a.add_argument("--aplicar", action="store_true", help="sin esta bandera solo se muestra")
    arg = a.parse_args(argv)
    por_nombre = {v: k for k, v in NOMBRES.items()}
    dpid = por_nombre.get(arg.dispositivo, arg.dispositivo)
    onos = Onos(arg.onos, arg.usuario)
    if arg.quitar:
        print("Se borrarían todos los flujos del appId", APP_ID)
        if arg.aplicar:
            onos._pedir("DELETE", f"/flows/application/{APP_ID}")
            print("Borrados.")
        return 0
    todas = pruebas(dpid)
    if not arg.aplicar:
        for f in todas:
            print(f["_prueba"], "->", json.dumps({k: v for k, v in f.items() if k != "_prueba"})[:230])
        print("\nSimulación: nada se envió. Añade --aplicar para probarlas contra ONOS (son inertes: tabla 61).")
        return 0
    aceptadas = 0
    for f in todas:
        ok, detalle = enviar(onos, f)
        aceptadas += ok
        print(f"{'ACEPTA ' if ok else 'RECHAZA'} {f['_prueba']:<28} {'' if ok else detalle}")
    print(f"\n{aceptadas} de {len(todas)} aceptadas. Quita las de prueba con: python3 spike_capa3.py --quitar --aplicar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
