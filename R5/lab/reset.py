"""Deja el laboratorio listo para repetir una medición de R5.

Borra los DROP de prioridad 50000 del Core, el estado del integrador y vacía
el eve.json indicado en EVE_PATH. Después de ejecutarlo hay que reiniciar el
integrador.
"""
import os
import requests

AUTH = (os.getenv("ONOS_USER", "onos"), os.environ["ONOS_PASS"])
BASE = os.getenv("ONOS_URL", "http://localhost:8181/onos/v1")
DEV = "of:0000000000000001"

for f in requests.get(f"{BASE}/flows/{DEV}", auth=AUTH).json()["flows"]:
    if f["priority"] == 50000:
        requests.delete(f"{BASE}/flows/{DEV}/{f['id']}", auth=AUTH)
if os.path.exists("state.json"):
    os.remove("state.json")
p = os.environ.get("EVE_PATH")
if p:
    open(p, "w").close()
print("reset ok")
