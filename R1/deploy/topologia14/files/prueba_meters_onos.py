#!/usr/bin/env python3
"""Prueba si ONOS 2.7 puede CREAR un meter y un flujo que lo use por su REST (lo que necesitaria el Policy Mapper para poner los meters de R1: EAPOL 20 pps, MAB y DHCP 10 pps).

    python3 prueba_meters_onos.py crear   --onos http://192.168.201.1:8181 --usuario onos:rocks --device of:0000766a0c84c741
    python3 prueba_meters_onos.py limpiar --onos ... --usuario ... --device ...

`crear` hace un POST de un meter (10 pps, rafaga 20, banda DROP) y un POST de un flujo INERTE en la tabla 61 (EtherType experimental 0x88b5, al que ningun paquete real llega) con la instruccion METER,
e imprime TODAS las respuestas tal cual (para ajustar los campos si ONOS los rechaza). `limpiar` borra el flujo y el meter creados. Si algo falla en `crear`, intenta limpiar lo que alcanzo a crear.
"""
import argparse
import base64
import json
import os
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


def crear(arg):
    antes = {str(m.get("id")) for m in listar(arg)}
    meter = {"deviceId": arg.device, "unit": "PKTS_PER_SEC", "burst": True, "bands": [{"type": "DROP", "rate": 10, "burstSize": 20}]}
    resp = pedir(arg, "POST", "/meters/" + arg.device, meter)
    mostrar("POST /meters", resp)
    nuevos = [m for m in listar(arg) if str(m.get("id")) not in antes]
    print("meters nuevos segun GET /meters:", json.dumps(nuevos)[:500])
    if not nuevos:
        print("RESULTADO: ONOS no creo el meter (ver la respuesta de arriba).")
        return 1
    meter_id = nuevos[0]["id"]
    estado = {"meter_id": meter_id, "flow_id": None}
    with open(ESTADO, "w") as f:
        json.dump(estado, f)
    try:
        mid = int(str(meter_id), 0)
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
    with open(ESTADO, "w") as f:
        json.dump(estado, f)
    print(f"RESULTADO: meter {meter_id} creado; flujo HTTP {resp[0]}, id {estado['flow_id']}. Comprobar en el switch con ovs-ofctl dump-meters y dump-flows table=61.")
    return 0 if resp[0] in (200, 201) else 1


def limpiar(arg):
    if not os.path.exists(ESTADO):
        print("nada que limpiar (no hay estado)")
        return 0
    with open(ESTADO) as f:
        e = json.load(f)
    if e.get("flow_id"):
        mostrar("DELETE flujo", pedir(arg, "DELETE", f"/flows/{arg.device}/{e['flow_id']}"))
    mostrar("DELETE meter", pedir(arg, "DELETE", f"/meters/{arg.device}/{e['meter_id']}"))
    os.remove(ESTADO)
    print("meters que quedan en ONOS en ese switch:", json.dumps(listar(arg))[:300])
    return 0


def main():
    a = argparse.ArgumentParser()
    a.add_argument("accion", choices=["crear", "limpiar"])
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--device", default="of:0000766a0c84c741")
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
