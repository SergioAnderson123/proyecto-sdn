"""Prueba de TCAM: instala N reglas DROP de prueba en el Core y las retira.

Uso:
  py tcam.py 50      instala 50 DROP (10.200.x.y/32, prioridad P_R5) y mide el tiempo
  py tcam.py clean   borra las que instaló
Después de instalar: ovs-ofctl -O OpenFlow13 dump-flows s1 | wc -l
"""
import sys
import time
import json
import os
import requests

AUTH = (os.getenv("ONOS_USER", "onos"), os.environ["ONOS_PASS"])
BASE = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1")
DEV = os.getenv("CORE_DEVICE", "of:0000000000000001")
PRIORIDAD = int(os.getenv("P_R5", "42000"))
APP_ID = "org.campus.r5.tcam"   # aparte del integrador: reset.py y la reconciliación no las confunden

if sys.argv[1] == "clean":
    for f in json.load(open("tcam_flows.json")):
        requests.delete(f"{BASE}/flows/{f['deviceId']}/{f['flowId']}", auth=AUTH)
    print("limpio")
else:
    n = int(sys.argv[1])
    flows = [{
        "priority": PRIORIDAD, "timeout": 0, "isPermanent": True, "deviceId": DEV,
        "treatment": {"instructions": []},
        "selector": {"criteria": [
            {"type": "ETH_TYPE", "ethType": "0x0800"},
            {"type": "IPV4_SRC", "ip": f"10.200.{i // 250}.{i % 250 + 1}/32"}]}
    } for i in range(n)]
    t = time.time()
    r = requests.post(f"{BASE}/flows?appId={APP_ID}",
                      json={"flows": flows}, auth=AUTH)
    print(r.status_code, n, "flujos en", round(time.time() - t, 2), "s")
    json.dump(r.json().get("flows", []), open("tcam_flows.json", "w"))
