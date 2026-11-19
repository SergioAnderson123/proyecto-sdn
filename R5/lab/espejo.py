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

MODO GRUPO (slice de 14 VM, ESPEJO_TABLA=60): en vez de `NORMAL` (que inunda las tramas de destino desconocido, duplica el tráfico y se salta el
  reenvío por la tabla 60 de R2 y de la capa 3) cada regla hace `GRUPO + goto_table 60`. El grupo es de tipo ALL, uno por switch, con un cubo
  por sensor cableado a ese switch (eth_dst = MAC de la NIC del sensor + OUTPUT). Los cubos trabajan sobre CLONES: el paquete original sigue
  intacto por la tabla 60, exactamente como lo haría la regla de confianza de R2 (`TABLE 60`) si el espejo no existiera.

Uso:
  python3 espejo.py                instala el espejo (idempotente: ONOS reemplaza reglas idénticas)
  python3 espejo.py --canario IP   MODO GRUPO: instala el espejo SOLO para el origen IP/32 (prioridad +1), para probar sin tocar el resto
  python3 espejo.py --quitar-canario  retira las reglas del canario (prioridad +1)
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
TABLA_ESPEJO = os.getenv("ESPEJO_TABLA", "").strip()      # vacío = modo NORMAL (laboratorio); "60" = modo grupo + goto_table 60
COOKIE_GRUPO = os.getenv("ESPEJO_COOKIE", "0x52354d49")      # "R5MI": identifica el grupo de espejo en cada switch
GRUPO_ID_BASE = int(os.getenv("ESPEJO_GRUPO_ID", "21000"))  # ONOS exige el id del grupo en el cuerpo del POST: 21001, 21002... uno por switch


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


def reglas_topologia(topo, puertos_por_device, prioridad=None, grupos=None, prefijos=None):
    """Lista de reglas de ONOS (diccionarios para POST /flows) del espejo en todos los cores."""
    prioridad = prioridad or PRIORIDAD
    prefijos = prefijos or prefijos_ipv4(topo)
    reglas = []
    for c in topo["cores"]:
        disp = puertos_por_device[c["device"]]
        if grupos:                                                # modo grupo: copias en un grupo ALL y el original sigue por la tabla de reenvío
            copias = [{"type": "GROUP", "groupId": int(grupos[c["device"]])}, {"type": "TABLE", "tableId": int(TABLA_ESPEJO)}]
        else:
            copias = [{"type": "OUTPUT", "port": "NORMAL"}]        # el reenvío normal va primero y con la MAC original
            for s, e in c["espejo"].items():                      # y luego una copia por sensor cableado a este core, con la MAC de su NIC
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


# ---------------- Grupos (modo grupo) ----------------
def cuerpo_grupo(core, puertos, gid):
    """Grupo ALL del switch `core`: un cubo por sensor cableado a él (eth_dst = MAC de su NIC + OUTPUT al puerto de espejo)."""
    cubos = []
    for s, e in core["espejo"].items():
        ins = []
        if e["mac"]:
            ins.append({"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": e["mac"]})
        ins.append({"type": "OUTPUT", "port": str(numero_de_puerto(puertos, e["puerto"]))})
        cubos.append({"treatment": {"instructions": ins}})
    return {"id": gid, "groupId": gid, "type": "ALL", "appCookie": COOKIE_GRUPO, "deviceId": core["device"], "buckets": cubos}


def grupo_actual(device):
    """El grupo de espejo del switch según ONOS (diccionario) o None."""
    r = onos("GET", f"/groups/{device}")
    if r.status_code != 200:
        return None
    for g in r.json().get("groups", []):
        if str(g.get("appCookie", "")).lower() == COOKIE_GRUPO.lower():
            return g
    return None


def salidas_del_grupo(g):
    """Conjunto de (mac, puerto) de los cubos de un grupo (el devuelto por ONOS o el que se va a crear), para compararlos."""
    res = set()
    for b in g.get("buckets", []):
        mac, puerto = None, None
        for i in (b.get("treatment") or {}).get("instructions", []):
            if i.get("type") == "OUTPUT":
                puerto = str(i.get("port"))
            elif i.get("subtype") == "ETH_DST":
                mac = str(i.get("mac", "")).lower()
        res.add((mac, puerto))
    return res


def asegurar_grupo(core, puertos, gid, espera=60, pausa=2.0):
    """Crea (o recrea si cambió) el grupo ALL de espejo del switch y devuelve su groupId cuando ONOS lo da por ADDED."""
    import time
    device = core["device"]
    cuerpo = cuerpo_grupo(core, puertos, gid)
    g = grupo_actual(device)
    if g is not None and salidas_del_grupo(g) != salidas_del_grupo(cuerpo):
        onos("DELETE", f"/groups/{device}/{COOKIE_GRUPO}")
        time.sleep(2)
        g = None
    if g is None:
        r = onos("POST", f"/groups/{device}", json=cuerpo)
        if r.status_code not in (200, 201):
            raise SystemExit(f"ONOS no aceptó el grupo de espejo en {device}: HTTP {r.status_code} {r.text[:300]}")
    fin = time.time() + espera
    while time.time() < fin:
        g = grupo_actual(device)
        if g is not None and str(g.get("state", "")).upper() == "ADDED" and g.get("id") is not None:
            return int(str(g["id"]), 0)
        time.sleep(pausa)
    if g is not None and g.get("id") is not None and os.getenv("ESPEJO_GRUPO_ESTRICTO", "1") == "0":
        # ONOS puede dejar el grupo en PENDING_ADD aunque el switch ya lo tenga (visto en sw-core-1 el 18/11: el estado no avanza ni al crear ni al borrar).
        # Con ESPEJO_GRUPO_ESTRICTO=0 se sigue; hay que comprobarlo en el switch (ovs-ofctl dump-groups) y con tráfico.
        print(f"AVISO: el grupo de {device} sigue en {g.get('state')} en ONOS tras {espera} s; se continúa (ESPEJO_GRUPO_ESTRICTO=0). Comprobar con dump-groups")
        return int(str(g["id"]), 0)
    raise SystemExit(f"el grupo de espejo de {device} no llegó a ADDED en {espera} s (estado: {g and g.get('state')})")


def quitar_grupos(topo):
    for c in topo["cores"]:
        r = onos("DELETE", f"/groups/{c['device']}/{COOKIE_GRUPO}")
        print(f"grupo de espejo de {c['nombre']}: HTTP {r.status_code}")


def quitar_canario():
    """Borra solo las reglas del canario (prioridad +1) y deja el espejo normal."""
    r = onos("GET", f"/flows/application/{APP_ESPEJO}")
    n = 0
    for f in r.json().get("flows", []) if r.status_code == 200 else []:
        if f.get("priority") == PRIORIDAD + 1:
            onos("DELETE", f"/flows/{f['deviceId']}/{f['id']}")
            n += 1
    print(f"canario retirado: {n} regla(s)")


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
    if os.getenv("TOPOLOGIA"):
        # Con topologia.yaml, los nodos ONOS salen de ahi (si ONOS_URL no esta definido), como en el integrador y r5ctl. Sin esto el
        # contenedor `espejo`, que no recibe ONOS_URL, busca ONOS en localhost.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codigo"))
        import topologia
        try:
            topologia.aplicar_entorno()
        except (topologia.TopologiaInvalida, OSError) as e:
            raise SystemExit(f"topología inválida: {e}")
    if "--descubrir" in argv:
        return descubrir()
    if "--quitar-canario" in argv:
        return quitar_canario()
    if "--quitar" in argv:
        r = onos("DELETE", f"/flows/application/{APP_ESPEJO}")
        print(r.status_code, "reglas de espejo retiradas" if r.status_code in (200, 204) else r.text[:200])
        if TABLA_ESPEJO and os.getenv("TOPOLOGIA"):                 # primero las reglas (que referencian el grupo) y luego los grupos
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codigo"))
            import topologia
            quitar_grupos(topologia.cargar(os.environ["TOPOLOGIA"]))
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
        grupos, prefijos, prio = None, None, None
        if TABLA_ESPEJO:
            grupos = {c["device"]: asegurar_grupo(c, puertos[c["device"]], GRUPO_ID_BASE + i) for i, c in enumerate(topo["cores"], 1)}
            print("grupos de espejo (groupId por switch):", grupos)
            if "--canario" in argv:                                  # solo un origen /32 y prioridad +1: prueba aislada
                ip = argv[argv.index("--canario") + 1]
                prefijos, prio = [f"{ipaddress.ip_address(ip)}/32"], PRIORIDAD + 1
            else:                                                    # reglas nuevas con otra accion: se retiran las de NORMAL para que no queden mezcladas
                r = onos("DELETE", f"/flows/application/{APP_ESPEJO}")
                print(f"reglas anteriores del espejo retiradas (HTTP {r.status_code})")
        flujos = reglas_topologia(topo, puertos, prioridad=prio, grupos=grupos, prefijos=prefijos)
        print(f"Espejo en {len(topo['cores'])} core(s): {len(flujos)} reglas, copia a {', '.join(topo['sensores'])}"
              + (f" (grupo + goto_table {TABLA_ESPEJO})" if grupos else " (NORMAL)"))
    else:
        flujos = reglas_clasicas()
    instalar(flujos)


if __name__ == "__main__":
    main(sys.argv[1:])
