"""Instala el espejo selectivo permanente en el Core (Bloque 1 de R5).

Dos reglas permanentes (prioridad 30000) en el switch Core:
  1. entrante: in_port=uplink, ipv4_dst=HOME_NET      -> NORMAL + puerto del IDS
  2. saliente: ipv4_src=HOME_NET, ipv4_dst=EXTERNA    -> NORMAL + puerto del IDS

Variables de entorno: ONOS_URL, ONOS_USER, ONOS_PASS.
"""
import os
import requests

ONOS_URL = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1")
AUTH = (os.getenv("ONOS_USER", "onos"), os.environ["ONOS_PASS"])
CORE = "of:0000000000000001"
PUERTO_UPLINK = 3
PUERTO_IDS = 4
HOME_NET = "10.0.0.0/24"
RED_EXTERNA = "10.0.1.0/24"
PRIORIDAD = 30000


def regla(criterios):
    return {
        "priority": PRIORIDAD,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": CORE,
        "treatment": {"instructions": [
            {"type": "OUTPUT", "port": "NORMAL"},
            {"type": "OUTPUT", "port": str(PUERTO_IDS)},
        ]},
        "selector": {"criteria": criterios},
    }


flujos = [
    regla([
        {"type": "IN_PORT", "port": str(PUERTO_UPLINK)},
        {"type": "ETH_TYPE", "ethType": "0x0800"},
        {"type": "IPV4_DST", "ip": HOME_NET},
    ]),
    regla([
        {"type": "ETH_TYPE", "ethType": "0x0800"},
        {"type": "IPV4_SRC", "ip": HOME_NET},
        {"type": "IPV4_DST", "ip": RED_EXTERNA},
    ]),
]

for app_id in ("org.campus.espejo", "org.onosproject.rest"):
    r = requests.post(f"{ONOS_URL}/flows?appId={app_id}",
                      json={"flows": flujos}, auth=AUTH)
    if r.status_code in (200, 201):
        break
print(r.status_code, r.text)
