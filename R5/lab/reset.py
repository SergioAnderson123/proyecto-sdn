"""Deja el laboratorio listo para repetir una medición de R5.

Borra los DROP del integrador (appId APP_ID) en todos los switches, el estado
del integrador (STATE_PATH) y vacía el eve.json indicado en EVE_PATH. Después
de ejecutarlo hay que reiniciar el integrador.
"""
import os
import requests

AUTH = (os.getenv("ONOS_USER", "onos"), os.environ["ONOS_PASS"])
BASE = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1")
APP_ID = os.getenv("APP_ID", "org.campus.r5")

for f in requests.get(f"{BASE}/flows/application/{APP_ID}", auth=AUTH).json().get("flows", []):
    requests.delete(f"{BASE}/flows/{f['deviceId']}/{f['id']}", auth=AUTH)
estado = os.getenv("STATE_PATH", "state.json")
if os.path.exists(estado):
    os.remove(estado)
p = os.environ.get("EVE_PATH")
if p:
    open(p, "w").close()
print("reset ok")
