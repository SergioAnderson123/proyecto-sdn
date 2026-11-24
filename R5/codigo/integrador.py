#!/usr/bin/env python3
"""M09 - Integrador IDS-SDN: traduce alertas de Suricata en bloqueos sobre ONOS.

Flujo general:
    eve-alerts.json (I09)  ->  decisión (¿a quién y dónde bloquear?)  ->  ONOS REST (I10)
                                                                       ->  Policy Mapper (I11, opcional)

El servicio sigue el archivo EVE que escribe Suricata, filtra las alertas por severidad, decide qué IP bloquear y en qué
switch(es), e instala una regla DROP temporal mediante la API REST de ONOS. Para hosts internos pide además al Policy Mapper
de R1/R2 que revoque la sesión del usuario.

Garantías de diseño:
    * Entrega al menos una vez: el offset de lectura solo avanza cuando la alerta se procesó (o se descartó de forma definitiva),
      y el estado se persiste de forma atómica para sobrevivir a reinicios.
    * Idempotencia: una IP ya bloqueada no se vuelve a enviar a ONOS.
    * Tolerancia a fallos: reintento con espera exponencial ante caídas de ONOS y conmutación entre nodos del clúster.
    * Autocorrección: el estado local se reconcilia periódicamente con los flujos reales de ONOS.

Configuración: variables de entorno (ver sección "Configuración"), opcionalmente completadas desde TOPOLOGIA=<yaml>.
"""
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
import topologia  # noqa: E402  (debe importarse tras ajustar sys.path)

# Si TOPOLOGIA=/ruta/topologia.yaml (slice de 14 VM), el archivo completa las variables que el entorno no defina:
# ONOS_URL (clúster), MAPPER_URL (primario y réplica), HOME_NET, LISTA_BLANCA y CORE_DEVICES.
# Sin TOPOLOGIA, todo proviene únicamente del entorno.
topologia.aplicar_entorno()

# ---------------- Configuración ----------------
# Entrada y persistencia
EVE_PATH   = os.getenv("EVE_PATH", "/var/log/suricata/eve-alerts.json")   # salida EVE de Suricata (una alerta JSON por línea)
STATE_PATH = os.getenv("STATE_PATH", "/var/lib/integrador/state.json")    # estado persistente (offset de lectura e IPs bloqueadas)

# Acceso a ONOS
ONOS_URL   = os.getenv("ONOS_URL")    # https://<host-onos>:8443/onos/v1; admite varios nodos separados por coma (clúster).
                                      # El puerto 8181 es HTTP plano: HTTPS debe habilitarse en ONOS.
ONOS_USER  = os.getenv("ONOS_USER")
ONOS_PASS  = os.getenv("ONOS_PASS")
ONOS_CA    = os.getenv("ONOS_CA")     # ruta al certificado de la CA; si no se define, se usa el almacén del sistema
APP_ID     = os.getenv("APP_ID", "org.campus.r5")   # identifica nuestras reglas en ONOS: permite listarlas y borrarlas sin afectar a otras apps
DRY_RUN    = os.getenv("DRY_RUN") == "1"            # modo simulación: registra las acciones pero no contacta con ONOS

# Policy Mapper de R1/R2 (p. ej. http://192.168.100.11:5000); admite réplica separada por coma. Vacío = I11 desactivada.
MAPPER_URL = os.getenv("MAPPER_URL", "")
# Clave compartida con el Policy Mapper (su R5_TOKEN): se envía en X-R5-Token. Sin ella el mapper responde 403 y no se revocan sesiones.
R5_TOKEN   = os.getenv("R5_TOKEN", "")
# Nombre del sensor (nodo-sec-1 o nodo-sec-2). Ambos reciben el mismo tráfico espejado y aplican los mismos bloqueos.
SENSOR     = os.getenv("SENSOR", socket.gethostname())


def lista_urls(valor):
    """Normaliza una lista de URL separadas por coma: descarta vacíos, espacios y la barra final.

    Ejemplo: 'http://a:1/, http://b:1' -> ['http://a:1', 'http://b:1']."""
    return [u.strip().rstrip("/") for u in (valor or "").split(",") if u.strip()]


def redes(var, defecto):
    """Lee de la variable de entorno `var` una lista de prefijos CIDR separados por coma y los devuelve como objetos de red.

    Ejemplo: HOME_NET=10.0.0.0/24,10.0.1.0/24. Lanza ValueError si algún prefijo es inválido."""
    return [ipaddress.ip_network(x.strip()) for x in os.getenv(var, defecto).split(",") if x.strip()]


# Alcance de la protección
HOME_NET     = redes("HOME_NET", "10.0.0.0/24")                    # red interna; debe coincidir con HOME_NET de suricata.yaml
LISTA_BLANCA = redes("LISTA_BLANCA", "10.0.0.40/32,10.0.0.50/32,10.0.0.250/32")  # nunca se bloquean: servidores de recursos y DHCP de ONOS
# Switches Core donde se bloquea a un atacante externo. Con ECMP su tráfico puede entrar por cualquiera de los dos,
# por lo que el DROP se instala en todos a la vez.
CORE_DEVICES = [d.strip() for d in os.getenv("CORE_DEVICES", "").split(",") if d.strip()] \
    or [os.getenv("CORE_DEVICE", "of:0000000000000001")]
CORE_DEVICE  = CORE_DEVICES[0]                                    # compatibilidad con versiones anteriores: primer Core (o el único)

# Política de bloqueo
PRIORITY     = int(os.getenv("P_R5", "42000"))     # banda reservada a R5: 41000-44999 (por encima de la sesión R2, 40001; por debajo de R1, 45000)
MAX_SEVERITY = int(os.getenv("MAX_SEVERITY", "2"))  # solo se actúa sobre alertas con severidad <= este valor (1 = más grave)
INTRANET     = os.getenv("INTRANET_BLOCK", "1") == "1"   # también bloquea alertas interno -> interno (requiere espejo del tráfico interno)
BLOCK_SEC    = int(os.getenv("BLOCK_SEC", "300"))  # timeout de inactividad de la regla en ONOS, en segundos

# Anti-suplantación: antes de bloquear a un host INTERNO se contrasta la MAC origen de la alerta con la MAC que ONOS conoce para esa IP.
# Un atacante externo que inventa una IP interna llega con su propia MAC (o la del router), distinta de la del dueño real de la IP.
VERIFICAR_ORIGEN = os.getenv("VERIFICAR_ORIGEN", "1") == "1"      # 0 = comportamiento anterior (se confía en la IP origen)
# MACs de gateways/routers internos: el tráfico enrutado entre subredes llega al sensor con esa MAC origen, no con la del host, así que
# no sirve para verificar y se acepta (no se puede distinguir de un origen legítimo).
MACS_GATEWAY = {m.strip().lower() for m in os.getenv("MACS_GATEWAY", "").split(",") if m.strip()}
SUPLANTACION_TTL = 30    # segundos que se recuerda una suplantación detectada para no consultar a ONOS en cada alerta de la misma inundación

# Temporización del bucle
POLL_SEC      = float(os.getenv("POLL_SEC", "0.05"))       # periodo de sondeo del EVE; con 0.5 s la espera media alerta->POST era ~250 ms
SAVE_EVERY    = 5                                          # segundos entre guardados del estado
RECONCILE_SEC = float(os.getenv("RECONCILE_SEC", "10"))    # periodo de comparación entre el estado local y los flujos reales de ONOS
GRACIA_SEC    = 3                                          # un flujo recién creado puede tardar unos segundos en aparecer en ONOS
BACKOFF_MIN, BACKOFF_MAX = 1, 30                           # espera inicial y máxima (s) al reintentar tras un fallo de ONOS

AUTH   = (ONOS_USER, ONOS_PASS)
VERIFY = ONOS_CA if ONOS_CA else True   # parámetro `verify` de requests: ruta a la CA o validación con el almacén del sistema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("integrador")


class Reintentar(Exception):
    """Fallo transitorio (caída de red, 5xx, 401/403/408/429): el llamador debe reintentar con espera creciente."""


class Descartar(Exception):
    """Error 4xx permanente: reintentar no ayudaría. Se registra y se omite la alerta."""


# ---------------- ONOS ----------------
_nodo = 0        # índice del último nodo de ONOS que respondió; se reutiliza mientras siga funcionando


def onos(metodo, ruta, **kw):
    """Invoca la API REST de ONOS y devuelve la respuesta (`requests.Response`).

    Con varios nodos configurados los prueba en orden, empezando por el último que funcionó, y pasa al siguiente si uno no
    responde o devuelve 5xx: un nodo caído no debe retrasar un bloqueo.

    Lanza:
        Reintentar: ningún nodo respondió, o la respuesta fue 401/403/408/429 (fallo transitorio).
        Descartar:  respuesta 4xx permanente (la petición es inválida).
    El tratamiento de los códigos 4xx es el mismo sea cual sea el nodo que respondió."""
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
    else:                                     # ningún nodo dio una respuesta utilizable
        raise Reintentar(ultimo or "ONOS_URL sin nodos")
    if r.status_code in (401, 403, 408, 429):
        raise Reintentar(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise Descartar(f"HTTP {r.status_code}: {r.text[:200]}")
    return r


def ubicar_host(ip):
    """I10a: localiza el switch de borde de un host consultando GET /hosts de ONOS.

    Devuelve el deviceId del switch, o None si ONOS no conoce al host o aún no conoce su ubicación."""
    if DRY_RUN:
        return "of:0000000000000002"
    for h in onos("GET", "/hosts").json().get("hosts", []):
        if ip in h.get("ipAddresses", []) and h.get("locations"):
            return h["locations"][0]["elementId"]
    return None


def dispositivos_para(ip, donde):
    """Devuelve los switches donde instalar el DROP.

    `donde == "core"`: todos los Core (atacante externo). En otro caso, el switch de borde del host interno; si ONOS no lo
    conoce, se recurre a los Core."""
    if donde == "core":
        return list(CORE_DEVICES)
    dev = ubicar_host(ip)
    if dev:
        return [dev]
    log.warning("host %s no encontrado en ONOS; se bloquea en el Core", ip)
    return list(CORE_DEVICES)                        # plan B: al menos se corta el tráfico Norte-Sur


def mac_de(ip):
    """MAC del host `ip` según ONOS (GET /hosts), en minúsculas, o None si ONOS no la conoce."""
    if DRY_RUN:
        return None
    for h in onos("GET", "/hosts").json().get("hosts", []):
        if ip in h.get("ipAddresses", []) and h.get("mac"):
            return h["mac"].lower()
    return None


def mac_del_evento(ev, ip):
    """MAC del host `ip` según la alerta de Suricata (eve-log con `ethernet: yes`), en minúsculas, o None.

    Es la fuente preferida para I11: no depende de que ONOS haya aprendido al host. En el espejo solo se reescribe la MAC
    destino (IDS_MAC), de modo que la MAC origen de la copia es la real."""
    ether = ev.get("ether") or {}
    if ev.get("src_ip") == ip:
        mac = ether.get("src_mac")
    elif ev.get("dest_ip") == ip:
        mac = ether.get("dest_mac")
    else:
        mac = None
    return mac.lower() if mac else None


_suplantadas = {}    # (ip, mac de la alerta) -> instante hasta el que se recuerda la suplantación


def origen_suplantado(ev, ip):
    """True si la MAC origen de la alerta contradice la MAC que ONOS tiene para `ip`: el origen está suplantado.

    Evita que un atacante externo provoque el bloqueo (y la revocación de sesión) de un usuario legítimo inventando su IP.
    Solo devuelve True con evidencia en contra; si no se puede verificar, devuelve False y el bloqueo sigue como antes. No se
    puede verificar cuando: la verificación está desactivada, la alerta no trae MAC (eve-log sin `ethernet: yes`), la MAC es la de un
    gateway (tráfico enrutado) o ONOS no conoce al host.

    No cubre a un atacante que comparte el dominio L2 con la víctima y falsifica también la MAC: eso lo evitan la autenticación
    802.1X y la lista negra por suplantación de R1. Propaga `Reintentar` si ONOS no responde, para reprocesar la alerta."""
    if not VERIFICAR_ORIGEN or DRY_RUN:
        return False
    mac_alerta = mac_del_evento(ev, ip)
    if not mac_alerta or mac_alerta in MACS_GATEWAY:
        return False
    clave, ahora = (ip, mac_alerta), time.time()
    if _suplantadas.get(clave, 0) > ahora:
        return True
    try:
        mac_onos = mac_de(ip)
    except Descartar:                       # ONOS rechazó la consulta: sin dato no se descarta la alerta
        return False
    if not mac_onos or mac_onos == mac_alerta:
        return False
    _suplantadas[clave] = ahora + SUPLANTACION_TTL
    return True


def registrar_incidente(alerta, ip, donde, device, flow_id, t_lectura, t_post, t_201):
    """Arma el registro del incidente de un bloqueo y escribe su línea BLOQUEO en el log. Devuelve el registro (se guarda en el estado).

    Contiene: quién (IP bloqueada) contra quién (destino), qué ataque (sid y firma), qué se hizo (accion DROP, en qué switch y con qué
    flow_id), cuándo (hora del paquete en el sensor y los instantes de lectura, POST y 201) y qué sensor lo detectó.
    El formato de la línea BLOQUEO lo lee deploy/repeticiones.py: no cambiarlo sin actualizar su expresión regular."""
    destino = alerta["dest_ip"] if alerta["src_ip"] == ip else alerta["src_ip"]      # con to_client el atacante es el destino de la alerta
    incidente = {
        "vence": t_201 + BLOCK_SEC,   # estimación: el timeout real de ONOS es de inactividad
        "device": device, "flow_id": flow_id, "donde": donde, "sensor": SENSOR,
        "sid": alerta["sid"], "firma": alerta["firma"],
        "destino": destino, "proto": alerta["proto"], "accion": "DROP",
        "t_alerta": alerta["timestamp"],   # hora del paquete en el sensor (reloj de Suricata)
        "t_lectura": t_lectura, "t_post": t_post, "t_201": t_201,
    }
    log.info("BLOQUEO %s en %s (%s) flow=%s lectura->201=%.1f ms", ip, device, alerta["firma"], flow_id, (t_201 - t_lectura) * 1000)
    return incidente


def revocar_sesion(ip, mac=None):
    """I11: tras bloquear a un host interno, pide al Policy Mapper que revoque su sesión de R1/R2.

    El DROP por IP de R5 solo aísla el tráfico IPv4. Sin esta revocación, la sesión (flujo 40001) seguiría registrada y el
    usuario podría volver a pasar en cuanto expire el DROP.

    Es una operación opcional y de mejor esfuerzo: nunca impide el bloqueo, y cualquier fallo solo se registra.
    Se prueban en orden el Policy Mapper primario y su réplica.

    Devuelve el estado informado por el mapper ("revocada" o "sin_sesion"), o None si no se pudo revocar."""
    mappers = lista_urls(MAPPER_URL)            # primario y réplica: se pasa a la siguiente si una no responde
    if not mappers:
        return None
    try:
        mac = mac or mac_de(ip)                 # la MAC de la alerta tiene prioridad; ONOS es el plan B
    except (Reintentar, Descartar, ValueError) as e:
        log.warning("I11: no se pudo revocar la sesión de %s: %s", ip, e)
        return None
    if not mac:
        log.warning("I11: no se conoce la MAC de %s (ni la alerta ni ONOS); no se revoca su sesión", ip)
        return None
    for base in mappers:
        try:
            r = requests.post(f"{base}/sessions/revoke", json={"mac": mac, "motivo": "R5", "segundos": BLOCK_SEC},
                              headers={"X-R5-Token": R5_TOKEN}, timeout=3)
            estado = r.json().get("status") if r.status_code == 200 else None
        except (requests.RequestException, ValueError) as e:
            log.warning("I11: el Policy Mapper %s no respondió para %s: %s", base, ip, e)
            continue
        if r.status_code == 403:
            log.warning("I11: el Policy Mapper %s rechazó la clave (403): revisar R5_TOKEN en el sensor y en el mapper", base)
        log.info("I11: sesión de %s (%s) -> %s", ip, mac, estado or f"HTTP {r.status_code}")
        if estado:
            return estado
    return None


def build_perimeter_rule(ip, device):
    """Regla de ONOS (un flujo) que descarta el tráfico IPv4 cuyo origen es `ip` en el switch `device`.

    Prioridad PRIORITY (banda de R5, 41000-44999) y expiración por inactividad a los BLOCK_SEC segundos, así el bloqueo se libera solo
    cuando el atacante calla. Sin instrucciones = DROP. El selector es exacto (ETH_TYPE IPv4 e IPV4_SRC /32): solo esa IP, no el resto.
    Solo cubre el tráfico que SALE de `ip`; el que llega hacia ella no se descarta.
    Lanza ValueError si `ip` no es una IPv4 válida (IPv6 incluido): un dato inválido no debe llegar al switch como selector basura."""
    origen = ipaddress.IPv4Address(ip)
    return {
        "priority": PRIORITY,
        "timeout": BLOCK_SEC,                       # expira por inactividad
        "isPermanent": False,
        "deviceId": device,
        "treatment": {"instructions": []},          # sin instrucciones = DROP
        "selector": {"criteria": [
            {"type": "ETH_TYPE", "ethType": "0x0800"},     # solo IPv4
            {"type": "IPV4_SRC", "ip": f"{origen}/32"},
        ]},
    }


def regla_drop(device, ip):
    """Nombre anterior de build_perimeter_rule: la misma regla dentro del cuerpo {"flows": [...]} que espera POST /flows."""
    return {"flows": [build_perimeter_rule(ip, device)]}


def aplicar(ip, donde):
    """Instala el DROP para `ip` en los switches que correspondan (según `donde`).

    Devuelve la tupla (device, flow_id, t_post, t_201):
        device:  deviceId del switch, o varios unidos por coma.
        flow_id: identificador(es) del flujo en ONOS, unidos por coma (None si ONOS no los devolvió o en simulación).
        t_post:  instante (epoch) en que se envía el POST.
        t_201:   instante (epoch) en que ONOS confirma la creación."""
    devices = dispositivos_para(ip, donde)                          # I10a / I10b
    payload = {"flows": [build_perimeter_rule(ip, d) for d in devices]}
    device = ",".join(devices)
    t_post = time.time()
    if DRY_RUN:
        log.info("[SIMULACIÓN] POST /flows (device %s)\n%s", device, json.dumps(payload, indent=2))
        return device, None, t_post, time.time()
    # POST /flows recibe la lista {"flows": [...]} y el deviceId va dentro de cada regla. Una sola petición instala el DROP en
    # todos los switches, de modo que un atacante externo queda cortado a la vez en ambos Core.
    r = onos("POST", "/flows", params={"appId": APP_ID}, json=payload)
    t_201 = time.time()
    try:
        ids = [str(f["flowId"]) for f in r.json()["flows"] if f.get("flowId")]   # necesarios para el DELETE y la reconciliación
    except (ValueError, KeyError, TypeError):
        ids = []
    if not ids:
        log.warning("ONOS no devolvió flowId para %s", ip)
    return device, (",".join(ids) or None), t_post, t_201


# ---------------- Lectura de alertas (I09) ----------------
def parse_ids_alert(linea):
    """Interpreta una línea del eve-alerts.json de Suricata. Devuelve un diccionario normalizado, o None si no es una alerta válida.

    Campos devueltos: src_ip, dest_ip, src_port, dest_port, proto, sid, firma, severidad, direction, timestamp, mac_src, mac_dst
    y `evento` (el objeto JSON original, que usa el resto del integrador). Los puertos, el protocolo, la MAC y la firma son opcionales
    (None si faltan: ICMP no tiene puertos y `ether` solo aparece con `ethernet: yes`).

    Una alerta es válida si es un objeto JSON con event_type "alert", un bloque `alert` con `severity` entera y direcciones IP de
    origen y destino que sean IPs válidas (v4 o v6). Cualquier otra cosa (JSON truncado, texto, línea vacía, evento de otro tipo,
    campos ausentes o de tipo incorrecto) devuelve None sin lanzar excepciones: una línea corrupta no debe detener al integrador."""
    try:
        ev = json.loads(linea)
    except (TypeError, ValueError):                  # TypeError: la línea no es texto (p. ej. None); ValueError incluye JSONDecodeError
        return None
    return normalizar_alerta(ev)


def normalizar_alerta(ev):
    """Valida un evento EVE ya decodificado (diccionario) y devuelve la alerta normalizada de parse_ids_alert, o None si no es válida.

    Es la mitad de parse_ids_alert que no depende del texto de la línea: procesar() la usa directamente sobre el evento que recibe."""
    if not isinstance(ev, dict) or ev.get("event_type") != "alert":
        return None
    detalle = ev.get("alert")
    if not isinstance(detalle, dict):
        return None
    severidad = detalle.get("severity")
    if not isinstance(severidad, int) or isinstance(severidad, bool):
        return None
    for campo in ("src_ip", "dest_ip"):
        valor = ev.get(campo)
        if not isinstance(valor, str):
            return None
        try:
            ipaddress.ip_address(valor)
        except ValueError:
            return None
    ether = ev.get("ether") if isinstance(ev.get("ether"), dict) else {}

    def mac(v):
        return v.lower() if isinstance(v, str) and v else None

    return {
        "src_ip": ev["src_ip"], "dest_ip": ev["dest_ip"], "src_port": ev.get("src_port"), "dest_port": ev.get("dest_port"),
        "proto": ev.get("proto"), "sid": detalle.get("signature_id"), "firma": detalle.get("signature"), "severidad": severidad,
        "direction": ev.get("direction"), "timestamp": ev.get("timestamp"),
        "mac_src": mac(ether.get("src_mac")), "mac_dst": mac(ether.get("dest_mac")),
        "evento": ev,
    }


def consumir_eve(st, espera=BACKOFF_MIN):
    """Procesa las líneas completas nuevas del EVE a partir de st["offset"] y avanza el offset línea a línea.

    - Una línea a medio escribir (sin salto de línea final) no se procesa ni avanza el offset: se retoma cuando Suricata la termine.
    - Una línea corrupta o que no es una alerta se salta y el offset avanza.
    - Si ONOS no está disponible (Reintentar), se detiene SIN avanzar el offset de esa alerta, para reprocesarla después.
    Devuelve True si hubo que detenerse por un fallo transitorio de ONOS (el llamador espera y reintenta)."""
    fallo = False
    with open(EVE_PATH) as f:
        f.seek(st["offset"])
        for linea in iter(f.readline, ""):
            if not linea.endswith("\n"):
                break                                 # línea a medio escribir: se completa en la próxima vuelta
            t_lectura = time.time()
            alerta = parse_ids_alert(linea)
            if alerta is not None:
                try:
                    procesar(alerta["evento"], st, t_lectura)
                except Reintentar as e:
                    log.warning("ONOS no disponible (%s); reintento en %ss", e, espera)
                    fallo = True
                    break                             # no avanza el offset: la alerta se reprocesará
            st["offset"] = f.tell()                   # alerta procesada o línea descartada
    return fallo


# ---------------- Decisión ----------------
ACCION_BLOQUEAR, ACCION_REGISTRAR, ACCION_IGNORAR = "bloquear", "registrar", "ignorar"


def classify_alert(alerta):
    """Decide qué hacer con una alerta según su severidad (la de normalizar_alerta / parse_ids_alert). Devuelve:

        "bloquear"  : severidad entre 1 y MAX_SEVERITY (1 = la más grave). Se busca a quién bloquear y dónde (decidir).
        "registrar" : severidad mayor que MAX_SEVERITY (media-baja o ruido, p. ej. LLDP severidad 3): no se bloquea nada.
        "ignorar"   : severidad menor que 1 (0 o negativa). Suricata no usa esos valores, así que se trata como dato inválido y no
                      como "la más grave": un valor corrupto no debe provocar un bloqueo.
    El límite MAX_SEVERITY es configurable (variable de entorno del mismo nombre, 2 por defecto)."""
    severidad = alerta["severidad"]
    if severidad < 1:
        return ACCION_IGNORAR
    return ACCION_BLOQUEAR if severidad <= MAX_SEVERITY else ACCION_REGISTRAR


def ip4(s):
    """Convierte `s` en una dirección IPv4, o devuelve None si no es una IP válida o es IPv6 (que se ignora)."""
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        return None
    return a if a.version == 4 else None          # IPv6 se ignora


def en(a, lista):
    """True si la dirección `a` pertenece a alguna de las redes de `lista`."""
    return any(a in n for n in lista)


def en_lista_blanca(ip):
    """True si `ip` (texto o ipaddress) pertenece a la lista blanca (LISTA_BLANCA): una IP que nunca se debe bloquear.

    La lista admite prefijos de cualquier tamaño y también IPs externas conocidas (p. ej. el NAT de salida de la PUCP). Un dato que no
    es una IP válida devuelve False en lugar de lanzar una excepción: sin evidencia no se protege, pero tampoco se interrumpe al integrador."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return en(a, LISTA_BLANCA)


def decidir(ev):
    """Decide a quién bloquear y dónde, a partir de una alerta EVE.

    Devuelve (ip, "core" | "borde"), o None si no hay que actuar. Según el sentido del tráfico:
        externo -> interno : se bloquea al origen en los Core ("core").
        interno -> externo : se bloquea al host interno (posible C2) en su borde ("borde").
        interno -> interno : se bloquea al origen en su borde, si INTRANET está activo ("borde").
    Las IPs de la lista blanca nunca se bloquean y el tráfico IPv6 se ignora."""
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
    if en_lista_blanca(res[0]):      # solo se protege a la IP que se iba a bloquear
        log.warning("IP en lista blanca, no se bloquea: %s", res[0])
        return None
    return str(res[0]), res[1]


def procesar(ev, st, t_lectura=None):
    """Procesa una alerta EVE: si procede, instala el bloqueo y lo anota en el estado `st`.

    Se ignora lo que no sea una alerta, supere MAX_SEVERITY, no requiera acción o corresponda a una IP ya bloqueada.
    `t_lectura` es el instante en que se leyó la línea; se usa para medir la latencia lectura -> 201.
    Propaga `Reintentar` si ONOS no está disponible, para que el bucle principal no avance el offset."""
    alerta = normalizar_alerta(ev)
    if alerta is None:                                # no es una alerta válida (otro tipo de evento, campos ausentes o mal tipados)
        return
    accion = classify_alert(alerta)
    if accion != ACCION_BLOQUEAR:
        log.debug("alerta %s (severidad %s) no se bloquea: acción %s", alerta["sid"], alerta["severidad"], accion)
        return
    d = decidir(ev)
    if not d:
        return
    ip, donde = d
    if ip in st["bloqueadas"]:                # idempotencia: no se reenvía una IP ya bloqueada
        return
    if donde == "borde" and origen_suplantado(ev, ip):     # no se castiga al dueño legítimo de una IP inventada
        log.warning("SUPLANTACIÓN? %s: la alerta trae MAC %s y ONOS conoce otra; no se bloquea ni se revoca su sesión (%s)",
                    ip, mac_del_evento(ev, ip), ev["alert"].get("signature"))
        return
    try:
        device, flow_id, t_post, t_201 = aplicar(ip, donde)
    except Descartar as e:
        log.error("Regla descartada para %s: %s", ip, e)
        return
    t_lectura = t_lectura or t_post
    st["bloqueadas"][ip] = registrar_incidente(alerta, ip, donde, device, flow_id, t_lectura, t_post, t_201)
    if donde == "borde":                    # host interno: además del DROP, se revoca su sesión de R1/R2 (I11)
        st["bloqueadas"][ip]["sesion"] = revocar_sesion(ip, mac_del_evento(ev, ip))


def reconciliar(st):
    """Olvida las IPs cuyo flujo ya no existe en ONOS (expiró por inactividad o se borró a mano con r5ctl).

    Sin esto, una IP liberada seguiría en la lista del integrador y sus alertas nuevas se ignorarían. Se respeta un periodo de
    gracia (GRACIA_SEC) para no descartar flujos recién creados que ONOS aún no lista."""
    if DRY_RUN or not st["bloqueadas"]:
        return
    flujos = onos("GET", f"/flows/application/{APP_ID}").json().get("flows", [])
    activas = {c["ip"].split("/")[0] for f in flujos for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"}
    ahora = time.time()
    for ip, v in list(st["bloqueadas"].items()):
        if ip not in activas and ahora - v.get("t_201", 0) > GRACIA_SEC:
            del st["bloqueadas"][ip]
            log.info("LIBERADA %s: su flujo ya no existe en ONOS", ip)


def purgar_vencidas(st, ahora=None):
    """Olvida los bloqueos cuyo vencimiento estimado (`vence`) ya llegó y devuelve la lista de IPs olvidadas.

    Es la otra forma de liberar una IP, junto a reconciliar(): si el atacante calla BLOCK_SEC segundos, ONOS retira el flujo por
    inactividad y el integrador la olvida aquí; si vuelve, se le bloquea de nuevo. Un bloqueo que vence justo en `ahora` se olvida.
    `ahora` (epoch) solo se pasa en las pruebas; por defecto es la hora actual."""
    ahora = time.time() if ahora is None else ahora
    vencidas = [ip for ip, v in st["bloqueadas"].items() if v["vence"] <= ahora]
    for ip in vencidas:
        del st["bloqueadas"][ip]
        log.info("VENCIDA %s: el bloqueo cumplió su tiempo", ip)
    return vencidas


# ---------------- Estado ----------------
def cargar_estado():
    """Carga el estado persistido, o uno nuevo si no existe o está corrupto.

    Estructura: {"bloqueadas": {ip: datos}, "inode": int|None, "offset": int}. `inode` y `offset` indican hasta dónde se leyó
    el EVE. Migra los formatos antiguos de "bloqueadas"."""
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
    """Guarda el estado de forma atómica (archivo temporal + rename), para que una interrupción no lo deje a medias."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


# ---------------- Bucle principal ----------------
def main():
    """Bucle principal: sigue el EVE de Suricata, procesa cada alerta nueva y mantiene el estado.

    En cada vuelta: (1) reconcilia con ONOS cada RECONCILE_SEC, (2) elimina bloqueos vencidos, (3) lee las líneas nuevas del EVE
    y (4) guarda el estado cada SAVE_EVERY segundos. Si ONOS no está disponible, no avanza el offset y reintenta con espera
    exponencial (BACKOFF_MIN..BACKOFF_MAX), de modo que no se pierde ninguna alerta."""
    if not DRY_RUN and not all((ONOS_URL, ONOS_USER, ONOS_PASS)):
        raise SystemExit("Faltan ONOS_URL, ONOS_USER u ONOS_PASS")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit))   # SIGTERM -> salida limpia (se guarda el estado)

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
            purgar_vencidas(st, ahora)                                # purga bloqueos vencidos
            try:
                s = os.stat(EVE_PATH)
            except FileNotFoundError:                             # Suricata aún no creó el archivo
                time.sleep(POLL_SEC)
                continue
            if st["inode"] != s.st_ino or s.st_size < st["offset"]:   # rotación o truncamiento: se relee desde el inicio
                st["inode"], st["offset"] = s.st_ino, 0

            fallo = consumir_eve(st, espera)

            if fallo:
                time.sleep(espera)
                espera = min(espera * 2, BACKOFF_MAX)         # espera exponencial acotada
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
