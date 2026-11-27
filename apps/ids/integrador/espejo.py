"""Instala el espejo selectivo permanente en el Core (Bloque 1 de R5).

MODO TOPOLOGÍA (slice de 14 VM, dos cores y dos sensores):  TOPOLOGIA=/ruta/topologia.yaml
  Por cada core, por cada puerto de ENTRADA hacia un extremo (gateway, servidores, accesos) y por cada prefijo de la red del campus y de
  la red externa, una regla `in_port + ipv4_src=prefijo` que reenvía con NORMAL y copia a TODOS los sensores. Se espeja al INGRESAR el
  tráfico a la red (no a la salida), de modo que un paquete se copia una sola vez aunque ECMP lo reparta entre los dos cores; los puertos
  de la troncal entre cores no figuran en `entradas`. Cada sensor recibe la copia con la MAC destino reescrita a la de su NIC (la red
  virtual de VNRT solo entrega a una VM las tramas dirigidas a su MAC).

MODO CLÁSICO (un solo Core, laboratorio Docker y VNRT del 31/10): sin TOPOLOGIA, tres reglas permanentes en CORE_DEVICE
  1. entrante: in_port=uplink, ipv4_dst=HOME_NET      -> NORMAL + puerto del IDS
  2. saliente: ipv4_src=HOME_NET, ipv4_dst=EXTERNA    -> NORMAL + puerto del IDS
  3. interna:  ipv4_src=HOME_NET, ipv4_dst=HOME_NET   -> NORMAL + puerto del IDS (R3/R4)
  Variables: ONOS_URL, ONOS_USER, ONOS_PASS; opcionales CORE_DEVICE, PUERTO_UPLINK, PUERTO_IDS, ESPEJO_HOME_NET, ESPEJO_RED_EXTERNA e
  IDS_MAC (VNRT 31/10: of:0000a223f2c04547, 3, 1, 10.0.0.0/30, 10.0.0.64/26).

Uso:
  python3 espejo.py                instala el espejo (idempotente: ONOS reemplaza reglas idénticas)
  python3 espejo.py --descubrir    lista los switches de ONOS con sus puertos (nombre -> número) para llenar topologia.yaml
  python3 espejo.py --quitar       borra solo las reglas de espejo (appId org.campus.espejo)
"""
import ipaddress
import os
import sys

import requests

# 41000: dentro de la banda de R5 (41000-44999), por encima del flujo de sesión y de los puertos de confianza de R2 (40001) y de la
# base de R1 (40000); si estuviera por debajo, un Core marcado como "de confianza" por R2 taparía el espejo. Por debajo del DROP de R5
# (42000), para que una IP bloqueada deje de copiarse al IDS, y del DROP temporal de R1 (45000).
# El espejo NO debe instalarse en un switch con sesiones de usuario de R2: reenvía con NORMAL y saltaría las tablas de rol.
PRIORIDAD = int(os.getenv("P_ESPEJO", "41000"))
APP_ESPEJO = "org.campus.espejo"


# ---------------- ONOS (con varios nodos) ----------------
def urls_onos():
    return [u.strip().rstrip("/") for u in os.getenv("ONOS_URL", "http://localhost:8181/onos/v1").split(",") if u.strip()]


def onos(metodo, ruta, **kw):
    """Prueba cada nodo del clúster en orden y devuelve la primera respuesta que no sea un fallo de red o un 5xx."""
    auth = (os.getenv("ONOS_USER", "onos"), os.environ["ONOS_PASS"])
    ultimo = None
    for base in urls_onos():
        try:
            r = requests.request(metodo, f"{base}{ruta}", auth=auth, timeout=5, **kw)
        except requests.RequestException as e:
            ultimo = f"{base}: {e}"
            continue
        if r.status_code >= 500:
            ultimo = f"{base}: HTTP {r.status_code}"
            continue
        return r
    raise SystemExit(f"ningún nodo de ONOS respondió ({ultimo})")


# ---------------- Modo topología ----------------
def puertos_de(device):
    """{nombre de interfaz: número de puerto OpenFlow} según ONOS."""
    r = onos("GET", f"/devices/{device}/ports")
    if r.status_code != 200:
        raise SystemExit(f"ONOS no conoce el switch {device} (HTTP {r.status_code}): ¿está conectado?")
    res = {}
    for p in r.json().get("ports", []):
        a = p.get("annotations") or {}
        nombre = a.get("portName") or a.get("name")
        if nombre and str(p.get("port")).isdigit():
            res[nombre] = int(p["port"])
    return res


def numero_de_puerto(disp, nombre):
    """Número de puerto OpenFlow de la interfaz `nombre`. Si ONOS no entrega nombres, topologia.yaml puede usar el número directamente."""
    if nombre in disp:
        return disp[nombre]
    if str(nombre).isdigit():
        return int(nombre)
    raise KeyError(nombre)


def resolver_puertos(topo, puertos_por_device):
    """Comprueba que cada interfaz nombrada en topologia.yaml exista en ONOS. Falla con un mensaje útil si alguna no existe."""
    faltan = []
    for c in topo["cores"]:
        disp = puertos_por_device[c["device"]]
        pedidos = list(c["entradas"]) + [e["puerto"] for e in c["espejo"].values()]
        for nombre in pedidos:
            if nombre not in disp and not str(nombre).isdigit():
                faltan.append(f"{c['nombre']} ({c['device']}): no existe la interfaz '{nombre}'; las que ve ONOS son {sorted(disp)}")
    if faltan:
        raise SystemExit("topologia.yaml no coincide con ONOS:\n  " + "\n  ".join(faltan))


def prefijos_ipv4(topo):
    """Redes del campus y externas (solo IPv4): el espejo se limita a ellas, así que el resto del tráfico no se copia."""
    return [n for n in topo["home"] + topo["externa"] if ipaddress.ip_network(n).version == 4]


def reglas_topologia(topo, puertos_por_device, prioridad=None):
    """Lista de reglas de ONOS (diccionarios para POST /flows) del espejo en todos los cores."""
    prioridad = prioridad or PRIORIDAD
    prefijos = prefijos_ipv4(topo)
    reglas = []
    for c in topo["cores"]:
        disp = puertos_por_device[c["device"]]
        copias = [{"type": "OUTPUT", "port": "NORMAL"}]            # el reenvío normal va primero y con la MAC original
        for s in topo["sensores"]:                                # y luego una copia por sensor, con la MAC de su NIC
            e = c["espejo"][s]
            if e["mac"]:
                copias.append({"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": e["mac"]})
            copias.append({"type": "OUTPUT", "port": str(numero_de_puerto(disp, e["puerto"]))})
        for entrada in c["entradas"]:
            for prefijo in prefijos:
                reglas.append({
                    "priority": prioridad, "timeout": 0, "isPermanent": True, "deviceId": c["device"],
                    "treatment": {"instructions": copias},
                    "selector": {"criteria": [
                        {"type": "IN_PORT", "port": str(numero_de_puerto(disp, entrada))},
                        {"type": "ETH_TYPE", "ethType": "0x0800"},
                        {"type": "IPV4_SRC", "ip": prefijo},
                    ]},
                })
    return reglas


# ---------------- Modo clásico ----------------
def reglas_clasicas():
    core = os.getenv("CORE_DEVICE", "of:0000000000000001")
    uplink = int(os.getenv("PUERTO_UPLINK", "3"))
    ids = int(os.getenv("PUERTO_IDS", "4"))
    home = os.getenv("ESPEJO_HOME_NET", "10.0.0.0/24")
    externa = os.getenv("ESPEJO_RED_EXTERNA", "10.0.1.0/24")
    ids_mac = os.getenv("IDS_MAC")   # VNRT: la red virtual solo entrega al IDS las tramas dirigidas a su MAC

    def regla(criterios):
        return {
            "priority": PRIORIDAD, "timeout": 0, "isPermanent": True, "deviceId": core,
            "treatment": {"instructions": [{"type": "OUTPUT", "port": "NORMAL"}]
                          + ([{"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": ids_mac}] if ids_mac else [])
                          + [{"type": "OUTPUT", "port": str(ids)}]},
            "selector": {"criteria": criterios},
        }
    return [
        regla([{"type": "IN_PORT", "port": str(uplink)}, {"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "IPV4_DST", "ip": home}]),
        regla([{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "IPV4_SRC", "ip": home}, {"type": "IPV4_DST", "ip": externa}]),
        # interno -> interno que cruza el Core (R3/R4): ataques entre switches de borde distintos
        regla([{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "IPV4_SRC", "ip": home}, {"type": "IPV4_DST", "ip": home}]),
    ]


# ---------------- Acciones ----------------
def instalar(flujos):
    for app_id in (APP_ESPEJO, "org.onosproject.rest"):
        r = onos("POST", "/flows", params={"appId": app_id}, json={"flows": flujos})
        if r.status_code in (200, 201):
            break
    print(r.status_code, r.text[:300])
    return r.status_code in (200, 201)


def descubrir():
    for d in onos("GET", "/devices").json().get("devices", []):
        a = d.get("annotations") or {}
        print(f"{d['id']}  disponible={d.get('available')}  gestión={a.get('managementAddress', '?')}  nombre={a.get('name', '-')}")
        for nombre, num in sorted(puertos_de(d["id"]).items(), key=lambda kv: kv[1]):
            print(f"    puerto {num:>3}  {nombre}")


def main(argv):
    if "--descubrir" in argv:
        return descubrir()
    if "--quitar" in argv:
        r = onos("DELETE", f"/flows/application/{APP_ESPEJO}")
        print(r.status_code, "reglas de espejo retiradas" if r.status_code in (200, 204) else r.text[:200])
        return
    if os.getenv("TOPOLOGIA"):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codigo"))
        import topologia
        try:
            topo = topologia.cargar(os.environ["TOPOLOGIA"])
        except topologia.TopologiaInvalida as e:
            raise SystemExit(f"topología inválida: {e}")
        puertos = {c["device"]: puertos_de(c["device"]) for c in topo["cores"]}
        resolver_puertos(topo, puertos)
        flujos = reglas_topologia(topo, puertos)
        print(f"Espejo en {len(topo['cores'])} core(s): {len(flujos)} reglas, copia a {', '.join(topo['sensores'])}")
    else:
        flujos = reglas_clasicas()
    instalar(flujos)


if __name__ == "__main__":
    main(sys.argv[1:])
