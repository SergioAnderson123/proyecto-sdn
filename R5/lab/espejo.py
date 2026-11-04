"""Instala el espejo selectivo permanente en el Core (Bloque 1 de R5).

Tres reglas permanentes (prioridad P_ESPEJO, por defecto 41000) en el switch Core:
  1. entrante: in_port=uplink, ipv4_dst=HOME_NET      -> NORMAL + puerto del IDS
  2. saliente: ipv4_src=HOME_NET, ipv4_dst=EXTERNA    -> NORMAL + puerto del IDS
  3. interna:  ipv4_src=HOME_NET, ipv4_dst=HOME_NET   -> NORMAL + puerto del IDS (R3/R4)

Variables de entorno: ONOS_URL, ONOS_USER, ONOS_PASS; opcionales CORE_DEVICE, PUERTO_UPLINK, PUERTO_IDS,
ESPEJO_HOME_NET, ESPEJO_RED_EXTERNA e IDS_MAC (reescribe la MAC destino solo de la copia al IDS) (VNRT: of:0000a223f2c04547, 3, 1, 10.0.0.0/30, 10.0.0.64/26).
"""
import os
import requests

ONOS_URL = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1")
AUTH = (os.getenv("ONOS_USER", "onos"), os.environ["ONOS_PASS"])
CORE = os.getenv("CORE_DEVICE", "of:0000000000000001")
PUERTO_UPLINK = int(os.getenv("PUERTO_UPLINK", "3"))
PUERTO_IDS = int(os.getenv("PUERTO_IDS", "4"))
HOME_NET = os.getenv("ESPEJO_HOME_NET", "10.0.0.0/24")
RED_EXTERNA = os.getenv("ESPEJO_RED_EXTERNA", "10.0.1.0/24")
IDS_MAC = os.getenv("IDS_MAC")   # VNRT: la red virtual solo entrega al IDS las tramas dirigidas a su MAC
# 41000: dentro de la banda de R5 (41000-44999), por encima del flujo de sesión y de los puertos de confianza de R2 (40001) y de la
# base de R1 (40000); si estuviera por debajo, un Core marcado como "de confianza" por R2 taparía el espejo. Por debajo del DROP de R5
# (42000), para que una IP bloqueada deje de copiarse al IDS, y del DROP temporal de R1 (45000).
# El espejo NO debe instalarse en un switch con sesiones de usuario de R2: reenvía con NORMAL y saltaría las tablas de rol.
PRIORIDAD = int(os.getenv("P_ESPEJO", "41000"))


def regla(criterios):
    return {
        "priority": PRIORIDAD,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": CORE,
        "treatment": {"instructions": [
            {"type": "OUTPUT", "port": "NORMAL"},
        ] + ([{"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": IDS_MAC}] if IDS_MAC else []) + [
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
    # interno -> interno que cruza el Core (R3/R4): ataques entre switches de borde distintos
    regla([
        {"type": "ETH_TYPE", "ethType": "0x0800"},
        {"type": "IPV4_SRC", "ip": HOME_NET},
        {"type": "IPV4_DST", "ip": HOME_NET},
    ]),
]

for app_id in ("org.campus.espejo", "org.onosproject.rest"):
    r = requests.post(f"{ONOS_URL}/flows?appId={app_id}",
                      json={"flows": flujos}, auth=AUTH)
    if r.status_code in (200, 201):
        break
print(r.status_code, r.text)
