#!/usr/bin/env python3
"""M09 - Integrador IDS-SDN: eve-alerts.json (I09) -> ONOS REST (I10)."""
import ipaddress
import json
import logging
import os
import signal
import socket
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topologia  # noqa: E402

# Con TOPOLOGIA=/ruta/topologia.yaml (slice de 14 VM) el archivo rellena lo que el entorno no defina: ONOS_URL (clúster), MAPPER_URL
# (primario y réplica), HOME_NET, LISTA_BLANCA y CORE_DEVICES. Sin TOPOLOGIA todo sigue viniendo del entorno, como antes.
topologia.aplicar_entorno()

# ---------------- Configuración ----------------
EVE_PATH   = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")
STATE_PATH = os.getenv("STATE_PATH", "/var/lib/integrador/state.json")
# ONOS_URL admite varios nodos separados por coma (clúster): se usa el primero que responda y se recuerda cuál fue.
ONOS_URL   = os.getenv("ONOS_URL")    # https://<host-onos>:8443/onos/v1 (8181 es HTTP; HTTPS se activa en ONOS)
ONOS_USER  = os.getenv("ONOS_USER")
ONOS_PASS  = os.getenv("ONOS_PASS")
ONOS_CA    = os.getenv("ONOS_CA")     # ruta al CA; si no se define, usa el del sistema
APP_ID     = os.getenv("APP_ID", "org.campus.r5")   # nombre propio: permite listar/borrar solo nuestras reglas
DRY_RUN    = os.getenv("DRY_RUN") == "1"
# Policy Mapper de R1/R2 (p. ej. http://192.168.100.11:5000), con réplica separada por coma; vacío = I11 desactivada
MAPPER_URL = os.getenv("MAPPER_URL", "")
SENSOR     = os.getenv("SENSOR", socket.gethostname())   # nodo-sec-1 o nodo-sec-2: los dos reciben el mismo espejo y bloquean igual


def lista_urls(valor):
    """'http://a:1/, http://b:1' -> ['http://a:1', 'http://b:1']."""
    return [u.strip().rstrip("/") for u in (valor or "").split(",") if u.strip()]


def redes(var, defecto):
    """Lista de prefijos separados por comas, p. ej. HOME_NET=10.0.0.0/24,10.0.1.0/24."""
    return [ipaddress.ip_network(x.strip()) for x in os.getenv(var, defecto).split(",") if x.strip()]


HOME_NET     = redes("HOME_NET", "10.0.0.0/24")                    # = suricata.yaml
LISTA_BLANCA = redes("LISTA_BLANCA", "10.0.0.40/32,10.0.0.50/32,10.0.0.250/32")  # servidores de recursos y servidor DHCP de ONOS
# Cores donde se bloquea a un atacante externo. Con ECMP su tráfico puede entrar por cualquiera de los dos, así que el DROP va en todos.
CORE_DEVICES = [d.strip() for d in os.getenv("CORE_DEVICES", "").split(",") if d.strip()] \
    or [os.getenv("CORE_DEVICE", "of:0000000000000001")]
CORE_DEVICE  = CORE_DEVICES[0]                                    # compatibilidad: el primero (o el único)
PRIORITY     = int(os.getenv("P_R5", "42000"))     # banda R5 41000-44999: sobre la sesión R2 (40001), bajo R1 (45000)
MAX_SEVERITY = int(os.getenv("MAX_SEVERITY", "2"))
INTRANET     = os.getenv("INTRANET_BLOCK", "1") == "1"   # bloquear también alertas interno -> interno (requiere espejo interno)
BLOCK_SEC    = int(os.getenv("BLOCK_SEC", "300"))  # timeout de inactividad en ONOS
POLL_SEC     = float(os.getenv("POLL_SEC", "0.05"))  # con 0.5 s la espera media alerta->POST era ~250 ms
SAVE_EVERY   = 5
RECONCILE_SEC = float(os.getenv("RECONCILE_SEC", "10"))   # cada cuánto compara su lista con los flujos reales de ONOS
GRACIA_SEC   = 3            # un flujo recién creado puede tardar unos segundos en aparecer en ONOS
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
_nodo = 0        # índice del último nodo de ONOS que respondió: se sigue usando mientras funcione


def onos(metodo, ruta, **kw):
    """Llama a la REST de ONOS. Con varios nodos prueba en orden, empezando por el último que funcionó, y pasa al siguiente si uno
    no responde o devuelve 5xx (un nodo caído no debe frenar un bloqueo). Los 401/403/408/429 y los 4xx se tratan igual en todos."""
    global _nodo
    bases = lista_urls(ONOS_URL)
    ultimo, r = None, None
    for i in range(len(bases)):
        idx = (_nodo + i) % len(bases)
        try:
            r = requests.request(metodo, f"{bases[idx]}{ruta}", auth=AUTH, verify=VERIFY, timeout=3, **kw)
        except requests.RequestException as e:
            ultimo = str(e)
            continue
        if r.status_code >= 500:
            ultimo = f"HTTP {r.status_code}"
            continue
        if idx != _nodo:
            log.warning("ONOS: se pasa al nodo %s", bases[idx])
        _nodo = idx
        break
    else:
        raise Reintentar(ultimo or "ONOS_URL sin nodos")
    if r.status_code in (401, 403, 408, 429):
        raise Reintentar(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise Descartar(f"HTTP {r.status_code}: {r.text[:200]}")
    return r


def ubicar_host(ip):
    """I10a: GET /hosts y buscar la IP en la respuesta. Devuelve el deviceId del borde, o None si ONOS no conoce al host."""
    if DRY_RUN:
        return "of:0000000000000002"
    for h in onos("GET", "/hosts").json().get("hosts", []):
        if ip in h.get("ipAddresses", []) and h.get("locations"):
            return h["locations"][0]["elementId"]
    return None


def dispositivos_para(ip, donde):
    """Switches donde se instala el DROP: todos los cores para un atacante externo; el borde del host para uno interno."""
    if donde == "core":
        return list(CORE_DEVICES)
    dev = ubicar_host(ip)
    if dev:
        return [dev]
    log.warning("host %s no encontrado en ONOS; se bloquea en el Core", ip)
    return list(CORE_DEVICES)                        # plan B: al menos se corta el tráfico Norte-Sur


def mac_de(ip):
    """MAC de un host según ONOS (GET /hosts), o None."""
    if DRY_RUN:
        return None
    for h in onos("GET", "/hosts").json().get("hosts", []):
        if ip in h.get("ipAddresses", []) and h.get("mac"):
            return h["mac"].lower()
    return None


def mac_del_evento(ev, ip):
    """MAC del host `ip` según la alerta de Suricata (eve-log con `ethernet: yes`), o None.

    Es la fuente preferida para I11: no depende de que ONOS haya aprendido al host, y en el espejo solo se reescribe la MAC
    destino (IDS_MAC), por lo que la MAC origen de la copia es la real."""
    ether = ev.get("ether") or {}
    if ev.get("src_ip") == ip:
        mac = ether.get("src_mac")
    elif ev.get("dest_ip") == ip:
        mac = ether.get("dest_mac")
    else:
        mac = None
    return mac.lower() if mac else None


def revocar_sesion(ip, mac=None):
    """I11: tras bloquear a un host interno, pide al Policy Mapper que revoque su sesión de R1/R2.

    El DROP por IP de R5 solo aísla el tráfico IPv4; sin esto la sesión (flujo 40001) seguiría registrada y el usuario podría
    volver a pasar en cuanto expire el DROP. Es opcional y no debe impedir el bloqueo: cualquier fallo solo se registra.
    Devuelve el estado que responde el mapper ("revocada" o "sin_sesion") o None."""
    mappers = lista_urls(MAPPER_URL)            # primario y réplica: se prueba el siguiente si uno no responde
    if not mappers:
        return None
    try:
        mac = mac or mac_de(ip)
    except (Reintentar, Descartar, ValueError) as e:
        log.warning("I11: no se pudo revocar la sesión de %s: %s", ip, e)
        return None
    if not mac:
        log.warning("I11: no se conoce la MAC de %s (ni la alerta ni ONOS); no se revoca su sesión", ip)
        return None
    for base in mappers:
        try:
            r = requests.post(f"{base}/sessions/revoke", json={"mac": mac, "motivo": "R5", "segundos": BLOCK_SEC}, timeout=3)
            estado = r.json().get("status") if r.status_code == 200 else None
        except (requests.RequestException, ValueError) as e:
            log.warning("I11: el Policy Mapper %s no respondió para %s: %s", base, ip, e)
            continue
        log.info("I11: sesión de %s (%s) -> %s", ip, mac, estado or f"HTTP {r.status_code}")
        if estado:
            return estado
    return None


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
    """Instala el DROP. Devuelve (device, flow_id, t_post, t_201); con varios switches, ambos van unidos por comas."""
    devices = dispositivos_para(ip, donde)                          # I10a / I10b
    payload = {"flows": [regla_drop(d, ip)["flows"][0] for d in devices]}
    device = ",".join(devices)
    t_post = time.time()
    if DRY_RUN:
        log.info("[SIMULACIÓN] POST /flows (device %s)\n%s", device, json.dumps(payload, indent=2))
        return device, None, t_post, time.time()
    # POST /flows recibe la lista {"flows": [...]}; el deviceId va dentro de cada regla. Una sola petición instala el DROP en todos
    # los switches, así que un atacante externo queda cortado a la vez en los dos cores.
    r = onos("POST", "/flows", params={"appId": APP_ID}, json=payload)
    t_201 = time.time()
    try:
        ids = [str(f["flowId"]) for f in r.json()["flows"] if f.get("flowId")]   # necesarios para el DELETE y la reconciliación
    except (ValueError, KeyError, TypeError):
        ids = []
    if not ids:
        log.warning("ONOS no devolvió flowId para %s", ip)
    return device, (",".join(ids) or None), t_post, t_201


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
    if ev.get("direction") == "to_client":   # la firma disparó sobre la respuesta: el atacante es el destino
        src, dst = dst, src
    if not src or not dst:
        return None
    s_int, d_int = en(src, HOME_NET), en(dst, HOME_NET)
    if not s_int and d_int:
        res = (src, "core")          # atacante externo -> DROP en el Core
    elif s_int and not d_int:
        res = (src, "borde")         # host interno (posible C2) -> DROP en su borde
    elif s_int and d_int and INTRANET:
        res = (src, "borde")         # ataque dentro de la intranet (R3/R4) -> DROP en el borde del origen
    else:
        return None
    if en(res[0], LISTA_BLANCA):     # solo se protege a la IP que se iba a bloquear
        log.warning("IP en lista blanca, no se bloquea: %s", res[0])
        return None
    return str(res[0]), res[1]


def procesar(ev, st, t_lectura=None):
    if ev.get("event_type") != "alert" or ev.get("alert", {}).get("severity", 99) > MAX_SEVERITY:
        return
    d = decidir(ev)
    if not d:
        return
    ip, donde = d
    if ip in st["bloqueadas"]:
        return
    try:
        device, flow_id, t_post, t_201 = aplicar(ip, donde)
    except Descartar as e:
        log.error("Regla descartada para %s: %s", ip, e)
        return
    t_lectura = t_lectura or t_post
    st["bloqueadas"][ip] = {
        "vence": t_201 + BLOCK_SEC,   # estimado: el timeout real es de inactividad
        "device": device, "flow_id": flow_id, "donde": donde, "sensor": SENSOR,
        "sid": ev["alert"].get("signature_id"), "firma": ev["alert"].get("signature"),
        "t_alerta": ev.get("timestamp"),   # hora del paquete en el sensor (reloj de Suricata)
        "t_lectura": t_lectura, "t_post": t_post, "t_201": t_201,
    }
    log.info("BLOQUEO %s en %s (%s) flow=%s lectura->201=%.1f ms", ip, device,
             ev["alert"].get("signature"), flow_id, (t_201 - t_lectura) * 1000)
    if donde == "borde":                    # host interno: además del DROP, se revoca su sesión de R1/R2 (I11)
        st["bloqueadas"][ip]["sesion"] = revocar_sesion(ip, mac_del_evento(ev, ip))


def reconciliar(st):
    """Olvida las IPs cuyo flujo ya no existe en ONOS (expiró por inactividad o se borró a mano con r5ctl).
    Sin esto, una IP liberada seguiría en la lista del integrador y sus alertas nuevas se ignorarían."""
    if DRY_RUN or not st["bloqueadas"]:
        return
    flujos = onos("GET", f"/flows/application/{APP_ID}").json().get("flows", [])
    activas = {c["ip"].split("/")[0] for f in flujos for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"}
    ahora = time.time()
    for ip, v in list(st["bloqueadas"].items()):
        if ip not in activas and ahora - v.get("t_201", 0) > GRACIA_SEC:
            del st["bloqueadas"][ip]
            log.info("LIBERADA %s: su flujo ya no existe en ONOS", ip)


# ---------------- Estado ----------------
def cargar_estado():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    if not isinstance(st.get("bloqueadas"), dict):    # migra la versión anterior (lista)
        st["bloqueadas"] = {}
    for ip, v in st["bloqueadas"].items():            # migra la versión anterior (ip -> vencimiento)
        if not isinstance(v, dict):
            st["bloqueadas"][ip] = {"vence": v}
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
    log.info("Integrador %s: %d nodo(s) ONOS, cores %s, Policy Mapper %s", SENSOR, len(lista_urls(ONOS_URL)), ",".join(CORE_DEVICES),
             ",".join(lista_urls(MAPPER_URL)) or "desactivado")
    ultimo_guardado, espera = time.time(), BACKOFF_MIN
    ultima_reconc = 0.0
    try:
        while True:
            ahora = time.time()
            if ahora - ultima_reconc >= RECONCILE_SEC:
                ultima_reconc = ahora
                try:
                    reconciliar(st)
                except (Reintentar, Descartar) as e:
                    log.warning("No se pudo reconciliar con ONOS: %s", e)
            st["bloqueadas"] = {k: v for k, v in st["bloqueadas"].items() if v["vence"] > ahora}
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
                    t_lectura = time.time()
                    try:
                        ev = json.loads(linea)
                    except json.JSONDecodeError:
                        ev = None
                    if ev is not None:
                        try:
                            procesar(ev, st, t_lectura)
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