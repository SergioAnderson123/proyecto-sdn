#!/usr/bin/env python3
"""M09 - Integrador IDS-SDN: eve-alerts.json (I09) -> ONOS REST (I10)."""
import ipaddress
import json
import logging
import os
import signal
import time

import requests

# ---------------- Configuración ----------------
EVE_PATH   = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")
STATE_PATH = os.getenv("STATE_PATH", "/var/lib/integrador/state.json")
ONOS_URL   = os.getenv("ONOS_URL")    # https://<host-onos>:8443/onos/v1 (8181 es HTTP; HTTPS se activa en ONOS)
ONOS_USER  = os.getenv("ONOS_USER")
ONOS_PASS  = os.getenv("ONOS_PASS")
ONOS_CA    = os.getenv("ONOS_CA")     # ruta al CA; si no se define, usa el del sistema
APP_ID     = os.getenv("APP_ID", "org.campus.integrador")   # nombre propio: permite listar/borrar solo nuestras reglas
DRY_RUN    = os.getenv("DRY_RUN") == "1"


def redes(lista):
    return [ipaddress.ip_network(x) for x in lista]


HOME_NET     = redes(["10.0.0.0/24"])  # = suricata.yaml
LISTA_BLANCA = redes(["10.0.0.99/32", "10.0.0.10/32"])   # TBD: gateway, DNS, ONOS, IDS, gestión
CORE_DEVICE  = "of:0000000000000001"                   # TBD: dpid real del Core
PRIORITY     = 50000        # TBD: mayor que las reglas del Policy Mapper
MAX_SEVERITY = 2
BLOCK_SEC    = 300
POLL_SEC     = 0.5
SAVE_EVERY   = 5
BACKOFF_MIN, BACKOFF_MAX = 1, 30

AUTH   = (ONOS_USER, ONOS_PASS)
VERIFY = ONOS_CA if ONOS_CA else True

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("integrador")


class Reintentar(Exception):
    """Caída de red, 5xx, 401/403/408/429: se reintenta con espera creciente."""


class Descartar(Exception):
    """Error 4xx permanente: se registra y se salta la alerta."""


# ---------------- ONOS ----------------
def onos(metodo, ruta, **kw):
    try:
        r = requests.request(metodo, f"{ONOS_URL}{ruta}", auth=AUTH,
                             verify=VERIFY, timeout=3, **kw)
    except requests.RequestException as e:
        raise Reintentar(str(e))
    if r.status_code >= 500 or r.status_code in (401, 403, 408, 429):
        raise Reintentar(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise Descartar(f"HTTP {r.status_code}: {r.text[:200]}")
    return r


def ubicar_host(ip):
    """I10a: GET /hosts y buscar la IP en la respuesta. Devuelve el deviceId del borde."""
    if DRY_RUN:
        return "of:0000000000000002"
    for h in onos("GET", "/hosts").json().get("hosts", []):
        if ip in h.get("ipAddresses", []) and h.get("locations"):
            return h["locations"][0]["elementId"]
    log.warning("host %s no encontrado en ONOS; se bloquea en el Core", ip)
    return CORE_DEVICE                               # plan B: al menos se corta el tráfico Norte-Sur


def regla_drop(device, ip):
    return {"flows": [{
        "priority": PRIORITY,
        "timeout": BLOCK_SEC,
        "isPermanent": False,
        "deviceId": device,
        "treatment": {"instructions": []},          # sin instrucciones = DROP
        "selector": {"criteria": [
            {"type": "ETH_TYPE", "ethType": "0x0800"},
            {"type": "IPV4_SRC", "ip": f"{ip}/32"},
        ]},
    }]}


def aplicar(ip, donde):
    device = CORE_DEVICE if donde == "core" else ubicar_host(ip)   # I10a / I10b
    payload = regla_drop(device, ip)
    if DRY_RUN:
        log.info("[SIMULACIÓN] POST /flows (device %s)\n%s", device, json.dumps(payload, indent=2))
    else:
        # POST /flows recibe la lista {"flows": [...]}; el deviceId va dentro de cada regla
        onos("POST", "/flows", params={"appId": APP_ID}, json=payload)


# ---------------- Decisión ----------------
def ip4(s):
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        return None
    return a if a.version == 4 else None          # IPv6 se ignora


def en(a, lista):
    return any(a in n for n in lista)


def decidir(ev):
    """(ip, 'core'|'borde') o None."""
    src, dst = ip4(ev.get("src_ip", "")), ip4(ev.get("dest_ip", ""))
    if not src or not dst:
        return None
    s_int, d_int = en(src, HOME_NET), en(dst, HOME_NET)
    if not s_int and d_int:
        res = (src, "core")          # atacante externo -> DROP en el Core
    elif s_int and not d_int:
        res = (src, "borde")         # host interno (posible C2) -> DROP en su borde
    else:
        return None
    if en(res[0], LISTA_BLANCA):     # solo se protege a la IP que se iba a bloquear
        log.warning("IP en lista blanca, no se bloquea: %s", res[0])
        return None
    return str(res[0]), res[1]


def procesar(ev, st):
    if ev.get("event_type") != "alert" or ev.get("alert", {}).get("severity", 99) > MAX_SEVERITY:
        return
    d = decidir(ev)
    if not d:
        return
    ip, donde = d
    if ip in st["bloqueadas"]:
        return
    try:
        aplicar(ip, donde)
    except Descartar as e:
        log.error("Regla descartada para %s: %s", ip, e)
        return
    st["bloqueadas"][ip] = time.time() + BLOCK_SEC
    log.info("BLOQUEO %s en %s (%s)", ip, donde, ev["alert"].get("signature"))


# ---------------- Estado ----------------
def cargar_estado():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    if not isinstance(st.get("bloqueadas"), dict):    # migra la versión anterior (lista)
        st["bloqueadas"] = {}
    st.setdefault("inode", None)
    st.setdefault("offset", 0)
    return st


def guardar_estado(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


# ---------------- Bucle principal ----------------
def main():
    if not DRY_RUN and not all((ONOS_URL, ONOS_USER, ONOS_PASS)):
        raise SystemExit("Faltan ONOS_URL, ONOS_USER u ONOS_PASS")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit))

    st = cargar_estado()
    ultimo_guardado, espera = time.time(), BACKOFF_MIN
    try:
        while True:
            ahora = time.time()
            st["bloqueadas"] = {k: v for k, v in st["bloqueadas"].items() if v > ahora}
            try:
                s = os.stat(EVE_PATH)
            except FileNotFoundError:
                time.sleep(POLL_SEC)
                continue
            if st["inode"] != s.st_ino or s.st_size < st["offset"]:   # rotación o truncamiento
                st["inode"], st["offset"] = s.st_ino, 0

            fallo = False
            with open(EVE_PATH) as f:
                f.seek(st["offset"])
                for linea in iter(f.readline, ""):
                    if not linea.endswith("\n"):
                        break                                 # línea a medio escribir
                    try:
                        ev = json.loads(linea)
                    except json.JSONDecodeError:
                        ev = None
                    if ev is not None:
                        try:
                            procesar(ev, st)
                        except Reintentar as e:
                            log.warning("ONOS no disponible (%s); reintento en %ss", e, espera)
                            fallo = True
                            break                             # no avanza el offset
                    st["offset"] = f.tell()

            if fallo:
                time.sleep(espera)
                espera = min(espera * 2, BACKOFF_MAX)
            else:
                espera = BACKOFF_MIN
                time.sleep(POLL_SEC)

            if time.time() - ultimo_guardado >= SAVE_EVERY:
                guardar_estado(st)
                ultimo_guardado = time.time()
    finally:
        guardar_estado(st)


if __name__ == "__main__":
    main()