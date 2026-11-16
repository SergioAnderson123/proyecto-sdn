#!/usr/bin/env python3
"""Prueba si ONOS 2.7 puede CREAR un meter y un flujo que lo use por su REST (lo que necesitaria el Policy Mapper para poner los meters de R1: EAPOL 20 pps, MAB y DHCP 10 pps).

    python3 prueba_meters_onos.py crear   --onos http://192.168.201.1:8181 --usuario onos:rocks --device of:0000766a0c84c741
    python3 prueba_meters_onos.py limpiar --onos ... --usuario ... --device ... [--meter 1]

`crear` hace un POST de un meter (10 pps, rafaga 20, banda DROP) y toma su identificador del encabezado Location de la respuesta (ONOS lo registra de forma asincrona: la lista GET puede tardar), y un POST de un
flujo INERTE en la tabla 61 (EtherType experimental 0x88b5, al que ningun paquete real llega) con la instruccion METER. Imprime TODAS las respuestas tal cual. El estado se guarda en cuanto se conoce el meter,
asi que `limpiar` siempre puede borrar lo creado; con `--meter ID` borra ese meter aunque no haya estado (por ejemplo, uno que dejo una ejecucion anterior).
"""
import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request

ESTADO = "/tmp/prueba_meters_onos.json"


def pedir(arg, metodo, ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    r = urllib.request.Request(arg.onos + "/onos/v1" + ruta, data=datos, method=metodo, headers={
        "Authorization": "Basic " + base64.b64encode(arg.usuario.encode()).decode(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=15) as x:
            return x.status, dict(x.headers), x.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


def mostrar(titulo, resp):
    estado, cabeceras, cuerpo = resp
    print(f"== {titulo}: HTTP {estado}")
    if cabeceras.get("Location"):
        print("   Location:", cabeceras["Location"])
    if cuerpo.strip():
        print("   cuerpo:", cuerpo.strip()[:600])


def listar(arg):
    estado, _, cuerpo = pedir(arg, "GET", "/meters/" + arg.device)
    try:
        return json.loads(cuerpo).get("meters", [])
    except ValueError:
        return []


def guardar(estado):
    with open(ESTADO, "w") as f:
        json.dump(estado, f)


def crear(arg):
    meter = {"deviceId": arg.device, "unit": "PKTS_PER_SEC", "burst": True, "bands": [{"type": "DROP", "rate": 10, "burstSize": 20}]}
    resp = pedir(arg, "POST", "/meters/" + arg.device, meter)
    mostrar("POST /meters", resp)
    loc = resp[1].get("Location", "")
    if resp[0] not in (200, 201) or not loc:
        print("RESULTADO: ONOS no acepto el meter (ver la respuesta de arriba).")
        return 1
    meter_id = loc.rstrip("/").split("/")[-1]
    estado = {"meter_id": meter_id, "flow_id": None}
    guardar(estado)                                       # desde aqui `limpiar` siempre puede borrarlo
    visto = []
    for _ in range(10):                                   # ONOS lo registra de forma asincrona: se mira hasta 5 s, solo para informar
        visto = [m for m in listar(arg) if str(m.get("id")) == meter_id]
        if visto:
            break
        time.sleep(0.5)
    print("meter", meter_id, "segun GET /meters:", json.dumps(visto)[:400] if visto else "(aun no aparece en la lista de ONOS; se continua con el identificador del Location)")
    try:
        mid = int(meter_id, 0)
    except ValueError:
        print("RESULTADO: no se pudo interpretar el id del meter:", meter_id)
        return 1
    flujo = {"priority": 1, "timeout": 0, "isPermanent": True, "deviceId": arg.device, "tableId": 61,
             "treatment": {"instructions": [{"type": "METER", "meterId": mid}]},
             "selector": {"criteria": [{"type": "ETH_TYPE", "ethType": "0x88b5"}]}}
    resp = pedir(arg, "POST", "/flows/" + arg.device + "?appId=org.campus.meterprueba", flujo)
    mostrar("POST /flows con METER", resp)
    loc = resp[1].get("Location", "")
    estado["flow_id"] = loc.rstrip("/").split("/")[-1] if loc else None
    guardar(estado)
    print(f"RESULTADO: meter {meter_id} creado; flujo HTTP {resp[0]}, id {estado['flow_id']}. Comprobar en el switch con ovs-ofctl dump-meters y dump-flows table=61.")
    return 0 if resp[0] in (200, 201) else 1


def limpiar(arg):
    estado = {}
    if os.path.exists(ESTADO):
        with open(ESTADO) as f:
            estado = json.load(f)
    if arg.meter and not estado.get("meter_id"):
        estado["meter_id"] = arg.meter
    if not estado:
        print("nada que limpiar (no hay estado; usar --meter ID para borrar un meter concreto)")
        return 0
    if estado.get("flow_id"):
        mostrar("DELETE flujo", pedir(arg, "DELETE", f"/flows/{arg.device}/{estado['flow_id']}"))
    if estado.get("meter_id"):
        mostrar("DELETE meter", pedir(arg, "DELETE", f"/meters/{arg.device}/{estado['meter_id']}"))
    if os.path.exists(ESTADO):
        os.remove(ESTADO)
    print("meters que ONOS lista ahora en ese switch:", json.dumps(listar(arg))[:300])
    return 0


def main():
    a = argparse.ArgumentParser()
    a.add_argument("accion", choices=["crear", "limpiar"])
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--device", default="of:0000766a0c84c741")
    a.add_argument("--meter", help="limpiar: borrar este meter aunque no haya estado")
    arg = a.parse_args()
    if arg.accion == "crear":
        try:
            codigo = crear(arg)
        except Exception as e:  # noqa: BLE001 - se limpia lo que se haya creado
            print("ERROR:", repr(e))
            codigo = 1
        if codigo:
            limpiar(arg)
        return codigo
    return limpiar(arg)


if __name__ == "__main__":
    raise SystemExit(main())
