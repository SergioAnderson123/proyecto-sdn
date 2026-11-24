"""flujos.py: los flujos de un switch para el panel (solo lectura): normalizarlos, explicarlos, filtrarlos y armar los comandos de `ovs-ofctl` equivalentes para mirarlos en el switch real.

Todo es PURO salvo `LectorFlujos` (que pide a ONOS, con una caché corta y de uno en uno). Reglas de carga, por lo que se vio el 23-nov con los timeouts de Atomix:
  - un switch se lee SOLO de su nodo maestro (nunca de otro: esas lecturas son las que se atascan) y bajo demanda, no en un bucle;
  - respuesta cacheada unos segundos (varias personas o pestañas no multiplican lecturas) y una sola lectura a la vez en todo el panel;
  - si la lectura falla y hay una anterior, se enseña ésta marcada como antigua, con el error.
"""
import ipaddress
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

DPID = re.compile(r"^of:[0-9a-f]{16}$")

# tipo de criterio de ONOS -> (nombre corto, clave del valor)
CAMPOS = {
    "IN_PORT": ("in_port", "port"), "ETH_SRC": ("eth_src", "mac"), "ETH_DST": ("eth_dst", "mac"), "ETH_TYPE": ("eth_type", "ethType"), "VLAN_VID": ("vlan", "vlanId"),
    "IP_PROTO": ("ip_proto", "protocol"), "IPV4_SRC": ("ip_src", "ip"), "IPV4_DST": ("ip_dst", "ip"), "TCP_SRC": ("tcp_src", "tcpPort"), "TCP_DST": ("tcp_dst", "tcpPort"),
    "UDP_SRC": ("udp_src", "udpPort"), "UDP_DST": ("udp_dst", "udpPort"), "ARP_OP": ("arp_op", "op"), "ARP_TPA": ("arp_tpa", "ip"), "ARP_SPA": ("arp_spa", "ip"),
    "ICMPV4_TYPE": ("icmp_type", "icmpType"), "METADATA": ("metadata", "metadata"),
}
TIPOS_ACCION = ("salida", "goto", "meter", "descarte", "controlador", "modifica", "extension")

# (tabla o None = cualquiera, prioridad, explicación). Fuente: R5/deploy/prioridades.yaml, policy_tables.py, capa3.py y flujos_proactivos.py (una prueba comprueba que no falte ninguna prioridad del yaml).
CATALOGO: List[Tuple[Optional[int], int, str]] = [
    (0, 50010, "EAPOL por puerto de acceso, con medidor (20 pps), al controlador (campus-meters)"),
    (0, 50000, "EAPOL de un puerto con sesión al controlador (Policy Mapper, R1)"),
    (0, 45000, "Lista negra por suplantación: DROP (Policy Mapper)"),
    (0, 42000, "DROP por IP de origen (integrador de R5)"),
    (0, 41000, "Copia del tráfico al IDS: espejo (R5)"),
    (0, 40600, "DROP del DHCP del cliente en un puerto externo (R2)"),
    (0, 40510, "DHCP por puerto de acceso, con medidor (10 pps), al controlador (campus-meters)"),
    (0, 40500, "DHCP del cliente (UDP 68 a 67) al controlador (R2)"),
    (0, 40100, "LLDP/BDDP al controlador: descubrimiento de enlaces (R2, reenvío proactivo)"),
    (0, 40060, "SOLO PRUEBA aislada de capa 3: IPv4 con la MAC del gateway de prueba salta a la tabla 62"),
    (0, 40004, "ARP del puerto de usuario al controlador y a la tabla 60 (fallback MAB)"),
    (0, 40003, "ARP salta a la tabla 60: difusión por árbol (reenvío proactivo)"),
    (0, 40002, "Portal cautivo: ARP de quien no tiene sesión, o reescritura de la respuesta del portal"),
    (0, 40001, "Sesión 802.1X (puerto + MAC de origen: pone la VLAN del rol y salta a su tabla) o puerto de confianza (servidor, troncal)"),
    (0, 40000, "Base de ONOS y R1: LLDP, BDDP, ARP y DHCP de ONOS al controlador"),
    (0, 5, "Núcleo de ONOS: ARP al controlador, regla por defecto de ONOS (org.onosproject.core)"),
    (0, 35000, "Portal cautivo: tráfico al portal y desvío del HTTP (bajo la sesión: con sesión nunca se desvía)"),
    (0, 1000, "Sin sesión ni otra regla: pone la VLAN de cuarentena y salta a su tabla (solo ARP y portal)"),
    (None, 30000, "Tabla de rol: un servicio permitido (VLAN del rol + IP y puerto TCP del servicio)"),
    (None, 310, "Tabla 60: la respuesta ARP a la MAC del gateway sube al controlador (sondeo de presencia, campus-dhcp)"),
    (None, 300, "Tabla 60: ARP dirigido al gateway virtual; lo contesta el propio switch (campus-gatewayarp)"),
    (None, 200, "Tabla 60: ARP en difusión; solo hacia los puertos de acceso del mismo switch"),
    (None, 160, "Capa 3: entrega a un host de este switch (IP /32): reescribe MAC y TTL"),
    (None, 150, "Capa 3: ruta por prefijo hacia el switch de esa subred"),
    (None, 130, "Capa 3: IP privada sin ruta, descartada para que nunca salga por la ruta por defecto"),
    (None, 120, "Capa 3: lo que entra por un puerto de acceso sube hacia el núcleo"),
    (None, 110, "Capa 3: ruta por defecto hacia el exterior (siguiente salto del borde)"),
    (None, 100, "Tabla 60: reenvío por MAC dentro del switch (unicast local)"),
    (None, 1, "Tabla 60: descarte por defecto (lo que ninguna regla reconoció)"),
    (None, 0, "Tabla de rol o de cuarentena: DROP explícito de todo lo demás"),
]
_TABLAS_ROL = {10: "docente", 20: "alumno", 40: "soporte", 50: "invitado", 99: "cuarentena"}


def explicar(tabla: int, prioridad: int, app: str = "") -> str:
    """Qué significa un flujo según su tabla y su prioridad (el catálogo es de nuestras apps; si no está, lo dice)."""
    for t, p, texto in CATALOGO:
        if p == prioridad and (t == tabla or (t is None and tabla != 0)):
            if t is None and tabla in _TABLAS_ROL and p in (30000, 0):
                return f"{texto} (rol {_TABLAS_ROL[tabla]})"
            return texto
    return "Sin descripción en el catálogo (¿regla de otra app o de una versión nueva?)"


# ---------- normalización ----------
def _eth_type(v) -> str:
    try:
        return f"0x{int(str(v), 16):04x}"
    except ValueError:
        return str(v).lower()


def _valor(tipo: str, c: dict):
    nombre, clave = CAMPOS[tipo]
    v = c.get(clave)
    if v is None:
        return None
    if nombre == "eth_type":
        return _eth_type(v)
    if nombre in ("eth_src", "eth_dst"):
        return str(v).lower()
    if nombre in ("ip_src", "ip_dst", "arp_tpa", "arp_spa"):
        return str(v) if "/" in str(v) else f"{v}/32"
    return str(v)


def _accion(i: dict) -> Tuple[str, str]:
    """(tipo, texto) de una instrucción de ONOS."""
    t = i.get("type")
    if t == "NOACTION":                          # así devuelve ONOS un flujo sin acciones (un DROP explícito): es lo mismo que una lista vacía
        return "descarte", "descartar"
    if t == "OUTPUT":
        p = str(i.get("port"))
        return ("controlador", "controlador") if p.upper() == "CONTROLLER" else ("salida", f"salida {p}")
    if t == "TABLE":
        return "goto", f"ir a la tabla {i.get('tableId')}"
    if t == "METER":
        return "meter", f"medidor {i.get('meterId')}"
    if t in ("L2MODIFICATION", "L3MODIFICATION"):
        sub = i.get("subtype", "")
        if sub == "VLAN_PUSH":
            return "modifica", "poner etiqueta VLAN"
        if sub == "VLAN_POP":
            return "modifica", "quitar etiqueta VLAN"
        if sub == "VLAN_ID":
            return "modifica", f"VLAN = {i.get('vlanId')}"
        if sub == "DEC_TTL":
            return "modifica", "decrementar TTL"
        valor = i.get("mac") or i.get("ip") or i.get("port") or ""
        return "modifica", f"{sub.lower()} = {valor}".strip(" =")
    if t == "EXTENSION":
        return "extension", f"extensión {i.get('extensionType', '?')}"
    return "extension", str(t).lower()


ORDEN_CAMPOS = ["in_port", "eth_src", "eth_dst", "eth_type", "vlan", "ip_proto", "ip_src", "ip_dst", "tcp_src", "tcp_dst", "udp_src", "udp_dst", "arp_op", "arp_spa", "arp_tpa", "icmp_type", "metadata"]


def _canonico(coincide: dict) -> dict:
    """Los campos en un orden fijo: ONOS devuelve los criterios en el orden que quiere (y no el que se instaló), y sin esto dos flujos iguales parecerían distintos al compararlos."""
    return dict(sorted(coincide.items(), key=lambda kv: (ORDEN_CAMPOS.index(kv[0]) if kv[0] in ORDEN_CAMPOS else len(ORDEN_CAMPOS), kv[0])))


def normalizar(f: dict) -> dict:
    """Un flujo de `GET /flows/{switch}` de ONOS, aplanado para filtrarlo y mostrarlo."""
    coincide = {}
    for c in (f.get("selector") or {}).get("criteria", []):
        if c.get("type") in CAMPOS:
            v = _valor(c["type"], c)
            if v is not None:
                coincide[CAMPOS[c["type"]][0]] = v
        else:
            coincide[str(c.get("type", "?")).lower()] = "?"
    coincide = _canonico(coincide)
    tr = f.get("treatment") or {}
    acciones = [_accion(i) for i in tr.get("instructions", [])] + [("modifica", "(diferida) " + _accion(i)[1]) for i in tr.get("deferred", [])]
    if not acciones or all(t == "descarte" for t, _ in acciones):
        acciones = [("descarte", "descartar")]
    tabla, prio = int(f.get("tableId", 0)), int(f.get("priority", 0))
    app = str(f.get("appId", ""))
    return {"id": str(f.get("id", "")), "tabla": tabla, "prioridad": prio, "app": app, "estado": str(f.get("state", "")), "paquetes": int(f.get("packets", 0) or 0),
            "bytes": int(f.get("bytes", 0) or 0), "vida_s": int(f.get("life", 0) or 0), "permanente": bool(f.get("isPermanent", False)), "coincide": coincide,
            "coincide_texto": ", ".join(f"{k}={v}" for k, v in coincide.items()) or "cualquiera", "acciones": [t for t, _ in acciones], "acciones_texto": "; ".join(x for _, x in acciones),
            "explicacion": explicar(tabla, prio, app)}


# ---------- filtros ----------
class FiltroInvalido(ValueError):
    pass


def _rango(texto: str) -> Tuple[Optional[int], Optional[int]]:
    t = texto.strip()
    try:
        if "-" in t:
            a, b = t.split("-", 1)
            return (int(a) if a.strip() else None), (int(b) if b.strip() else None)
        return int(t), int(t)
    except ValueError:
        raise FiltroInvalido(f"prioridad inválida: «{texto}» (use un número o un rango como 100-200)")


def _red(texto: str, campo: str):
    try:
        return ipaddress.ip_network(texto.strip(), strict=False)
    except ValueError:
        raise FiltroInvalido(f"{campo} inválido: «{texto}»")


def _igual(valor: Optional[str], buscado: str, campo: str) -> bool:
    if valor is None:
        return False
    b = buscado.strip().lower()
    if campo == "eth_type":
        return valor == _eth_type(b)
    if campo in ("eth_src", "eth_dst"):
        return valor == b.replace("-", ":")
    return valor.lower() == b


FILTROS_EXACTOS = ("in_port", "eth_src", "eth_dst", "eth_type", "vlan", "ip_proto", "tcp_src", "tcp_dst", "udp_src", "udp_dst", "arp_op")


def filtrar(flujos: Iterable[dict], filtros: Dict[str, str]) -> List[dict]:
    """Aplica los filtros (todos a la vez) a flujos YA normalizados. Un filtro vacío se ignora; uno mal escrito lanza FiltroInvalido (no se ignora en silencio).

    `ip_src` e `ip_dst` se comportan como `ovs-ofctl dump-flows`: salen los flujos cuyo prefijo está CONTENIDO en el que se pide. `ip_src_cubre` e `ip_dst_cubre` piden otra cosa:
    los flujos cuyo prefijo CONTIENE esa IP (qué reglas podrían aplicarse a ella)."""
    f = {k: str(v).strip() for k, v in filtros.items() if v is not None and str(v).strip() != ""}
    tablas = None
    if "tabla" in f:
        try:
            tablas = {int(x) for x in f["tabla"].split(",") if x.strip()}
        except ValueError:
            raise FiltroInvalido(f"tabla inválida: «{f['tabla']}» (números separados por comas)")
    pmin = pmax = None
    if "prioridad" in f:
        pmin, pmax = _rango(f["prioridad"])
    redes = {c: _red(f[c], c) for c in ("ip_src", "ip_dst") if c in f}
    cubre = {}
    for c in ("ip_src", "ip_dst"):
        if c + "_cubre" in f:
            try:
                cubre[c] = ipaddress.ip_address(f[c + "_cubre"])
            except ValueError:
                raise FiltroInvalido(f"{c}_cubre inválido: «{f[c + '_cubre']}»")
    if "accion" in f and f["accion"] not in TIPOS_ACCION:
        raise FiltroInvalido(f"acción inválida: «{f['accion']}» (una de {', '.join(TIPOS_ACCION)})")
    solo_con_paquetes = f.get("con_paquetes", "").lower() in ("1", "true", "si", "sí", "on")
    texto = f.get("texto", "").lower()
    salida = []
    for x in flujos:
        if tablas is not None and x["tabla"] not in tablas:
            continue
        if pmin is not None and x["prioridad"] < pmin:
            continue
        if pmax is not None and x["prioridad"] > pmax:
            continue
        if "app" in f and f["app"].lower() not in x["app"].lower():
            continue
        if "estado" in f and x["estado"].upper() != f["estado"].upper():
            continue
        if solo_con_paquetes and x["paquetes"] <= 0:
            continue
        if "accion" in f and f["accion"] not in x["acciones"]:
            continue
        if any(c in f and not _igual(x["coincide"].get(c), f[c], c) for c in FILTROS_EXACTOS):
            continue
        ok = True
        for c, red in redes.items():
            v = x["coincide"].get(c)
            try:
                if v is None or not ipaddress.ip_network(v, strict=False).subnet_of(red):
                    ok = False
            except (ValueError, TypeError):
                ok = False
        for c, ip in cubre.items():
            v = x["coincide"].get(c)
            try:
                if v is None or ip not in ipaddress.ip_network(v, strict=False):
                    ok = False
            except (ValueError, TypeError):
                ok = False
        if not ok:
            continue
        if texto and texto not in f"{x['coincide_texto']} {x['acciones_texto']} {x['explicacion']} {x['app']}".lower():
            continue
        salida.append(x)
    return salida


def ordenar(flujos: List[dict], orden: str = "tabla") -> List[dict]:
    clave = {"tabla": lambda x: (x["tabla"], -x["prioridad"], x["id"]), "prioridad": lambda x: (-x["prioridad"], x["tabla"], x["id"]),
             "paquetes": lambda x: (-x["paquetes"], x["tabla"], -x["prioridad"]), "app": lambda x: (x["app"], x["tabla"], -x["prioridad"])}.get(orden)
    return sorted(flujos, key=clave or (lambda x: (x["tabla"], -x["prioridad"], x["id"])))


def cuentas(flujos: Iterable[dict]) -> dict:
    por_tabla: Dict[int, int] = {}
    por_app: Dict[str, int] = {}
    por_estado: Dict[str, int] = {}
    for x in flujos:
        por_tabla[x["tabla"]] = por_tabla.get(x["tabla"], 0) + 1
        por_app[x["app"]] = por_app.get(x["app"], 0) + 1
        por_estado[x["estado"]] = por_estado.get(x["estado"], 0) + 1
    return {"tablas": dict(sorted(por_tabla.items())), "apps": dict(sorted(por_app.items())), "estados": dict(sorted(por_estado.items()))}


def cookies_por_app(crudos: Iterable[dict]) -> Dict[str, str]:
    """app -> prefijo de cookie de OVS. ONOS arma el cookie como (id corto de la app << 48) | id del flujo, y el `id` del REST es ese número en decimal. Solo se devuelve si todos los flujos
    de la app coinciden (si no, no se arriesga un filtro por cookie)."""
    vistos: Dict[str, set] = {}
    for f in crudos:
        try:
            vistos.setdefault(str(f.get("appId", "")), set()).add(int(str(f.get("id"))) >> 48)
        except (TypeError, ValueError):
            continue
    return {app: f"0x{next(iter(v)):x}000000000000" for app, v in vistos.items() if len(v) == 1 and next(iter(v)) > 0}


# ---------- comandos para el switch real ----------
def _ovs_match(f: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """(campos de ovs-ofctl, notas) a partir de los filtros que ovs-ofctl sabe expresar. Ojo: `dump-flows` con un filtro muestra los flujos cuyo match está CONTENIDO en él."""
    notas, t = [], []
    tipo = f.get("eth_type", "").lower()
    proto = f.get("ip_proto", "")
    tcp = "tcp_src" in f or "tcp_dst" in f
    udp = "udp_src" in f or "udp_dst" in f
    if tcp or proto == "6":
        t.append("tcp")
    elif udp or proto == "17":
        t.append("udp")
    elif proto == "1":
        t.append("icmp")
    elif tipo in ("0x0806", "0x806", "arp") or "arp_op" in f:
        t.append("arp")
    elif tipo in ("0x0800", "0x800", "ip") or "ip_src" in f or "ip_dst" in f or proto:
        t.append("ip")
    elif tipo:
        t.append(f"dl_type={tipo}")
    if proto and proto not in ("6", "17", "1"):
        t.append(f"nw_proto={proto}")
    if f.get("in_port", "").isdigit():
        t.append(f"in_port={f['in_port']}")
    elif f.get("in_port"):
        notas.append("in_port no es un número: ovs-ofctl solo filtra por número de puerto")
    for campo, clave in (("eth_src", "dl_src"), ("eth_dst", "dl_dst"), ("ip_src", "nw_src"), ("ip_dst", "nw_dst"), ("tcp_src", "tcp_src"), ("tcp_dst", "tcp_dst"), ("udp_src", "udp_src"),
                         ("udp_dst", "udp_dst"), ("vlan", "dl_vlan"), ("arp_op", "arp_op")):
        if campo in f:
            t.append(f"{clave}={f[campo].replace('-', ':') if clave.startswith('dl_s') or clave.startswith('dl_d') else f[campo]}")
    return t, notas


def comandos_ovs(filtros: Dict[str, str], cookie_app: Optional[str] = None, puente: str = "br0", version: str = "OpenFlow13") -> dict:
    """Los comandos que, EN LA VM DEL SWITCH, enseñan lo mismo que el panel con esos filtros. Devuelve {principal, tablas, traza, notas}.

    `ovs-ofctl dump-flows` solo filtra por campos de coincidencia, tabla y cookie; la prioridad, las acciones, los paquetes o el texto se aplican con `grep`/`awk` sobre su salida. El estado
    (ADDED...) es de ONOS y el switch no lo tiene. `ofproto/trace` dice, para un paquete concreto, qué regla lo atrapa."""
    f = {k: str(v).strip() for k, v in filtros.items() if v is not None and str(v).strip() != ""}
    notas: List[str] = []
    for k in list(f):                                   # lo que se escriba aquí acaba pegado en un terminal: solo caracteres inofensivos, nunca texto que el shell interprete
        permitido = re.fullmatch(r"[\w .:/=,\-]+", f[k]) if k == "texto" else re.fullmatch(r"[0-9,]+", f[k]) if k == "tabla" else re.fullmatch(r"[0-9A-Za-z:._/\-]+", f[k])
        if not permitido:
            notas.append(f"El valor de «{k}» tiene caracteres que no se admiten en un comando: ese filtro se ignora")
            del f[k]
    base = f"sudo ovs-ofctl -O {version} dump-flows {puente}"
    campos, notas_match = _ovs_match(f)
    notas += notas_match
    if cookie_app is not None and not re.fullmatch(r"0x[0-9a-f]+", cookie_app):
        cookie_app = None
    tablas = [x.strip() for x in f.get("tabla", "").split(",") if x.strip().isdigit()]
    if f.get("tabla") and not tablas:
        notas.append("tabla no es un número: no se pudo filtrar por tabla")
    if cookie_app:
        campos_cookie = f"cookie={cookie_app}/0xffff000000000000"
    elif f.get("app"):
        campos_cookie = None
        notas.append(f"No conozco el cookie de la app «{f['app']}» (el panel lo deduce de los flujos de esa app que ve en este switch): el comando no filtra por app")
    else:
        campos_cookie = None

    def armar(tabla: Optional[str]) -> str:
        partes = ([f"table={tabla}"] if tabla else []) + campos + ([campos_cookie] if campos_cookie else [])
        cmd = base + (f' "{",".join(partes)}"' if partes else "")
        if "prioridad" in f:
            try:
                a, b = _rango(f["prioridad"])
            except FiltroInvalido as e:
                notas.append(str(e))
                a = b = None
            if a is not None and a == b:
                cmd += f' | grep -E "priority={a}[, ]"'
            elif a is not None or b is not None:
                cmd += (" | awk -F'priority=' '{split($2,p,/[, ]/); if (" + " && ".join(c for c in ((f"p[1]>={a}" if a is not None else ""), (f"p[1]<={b}" if b is not None else "")) if c) + ") print}'")
        if f.get("con_paquetes", "").lower() in ("1", "true", "si", "sí", "on"):
            cmd += ' | grep -v "n_packets=0,"'
        acc = {"salida": "output:", "goto": "goto_table", "meter": "meter:", "descarte": "actions=drop", "controlador": "CONTROLLER", "modifica": "set_field|mod_|push_vlan|pop_vlan|dec_ttl", "extension": "move:|load:"}.get(f.get("accion", ""))
        if acc:
            cmd += f' | grep -E "{acc}"'
        if f.get("texto"):
            cmd += " | grep -i " + "'" + f["texto"].replace("'", "") + "'"
        return cmd

    if len(tablas) > 1:
        principal = "for t in " + " ".join(tablas) + "; do echo \"=== tabla $t ===\"; " + armar("$t") + "; done"
    else:
        principal = armar(tablas[0] if tablas else None)
    if "estado" in f:
        notas.append("El estado (ADDED, PENDING_ADD...) lo lleva ONOS; el switch no lo tiene")
    if "ip_dst_cubre" in f or "ip_src_cubre" in f:
        notas.append("«Cubre» (qué reglas contienen esa IP) no es un filtro de ovs-ofctl: use el comando de traza de abajo para ver qué regla atrapa un paquete a esa IP")
    if "ip_dst" in f or "ip_src" in f:
        notas.append("ovs-ofctl muestra los flujos cuyo prefijo está CONTENIDO en el que se pide: con una IP exacta no salen las rutas por prefijo que la contienen")
    traza = None
    if f.get("in_port", "").isdigit() and ("ip_dst" in f or "ip_dst_cubre" in f or "eth_dst" in f):
        spec = [f"in_port={f['in_port']}"]
        if "eth_src" in f:
            spec.append(f"dl_src={f['eth_src']}")
        if "eth_dst" in f:
            spec.append(f"dl_dst={f['eth_dst']}")
        spec.append("tcp" if ("tcp_dst" in f or "tcp_src" in f) else "udp" if ("udp_dst" in f or "udp_src" in f) else "ip")
        for campo, clave in (("ip_src", "nw_src"), ("ip_src_cubre", "nw_src"), ("ip_dst", "nw_dst"), ("ip_dst_cubre", "nw_dst"), ("tcp_src", "tcp_src"), ("tcp_dst", "tcp_dst"), ("udp_src", "udp_src"), ("udp_dst", "udp_dst")):
            if campo in f:
                spec.append(f"{clave}={f[campo].split('/')[0]}")
        if "vlan" in f:
            spec.append(f"dl_vlan={f['vlan']}")
        traza = f"sudo ovs-appctl ofproto/trace {puente} '{','.join(spec)}'"
    elif "ip_dst_cubre" in f:
        notas.append("Para la traza falta el puerto de entrada (in_port): indique por qué puerto entraría el paquete")
    return {"principal": principal, "tablas": tablas, "traza": traza, "notas": notas}


# ---------- lectura de ONOS ----------
class LectorFlujos:
    """Lee los flujos de un switch del nodo MAESTRO, con caché y de una en una. `leer(url) -> (datos, error)` e `ahora()` son inyectables."""

    def __init__(self, onos: Dict[str, str], leer: Callable, ahora: Callable[[], float] = time.time, ttl_s: float = 10.0, minimo_s: float = 3.0, timeout_s: float = 16.0,
                 usuario: str = "", clave: str = ""):
        self.onos, self.leer, self.ahora, self.ttl_s, self.minimo_s, self.timeout_s = dict(onos), leer, ahora, ttl_s, minimo_s, timeout_s
        self.usuario, self.clave = usuario, clave
        self._cache: Dict[str, dict] = {}
        self._cerrojo = threading.Lock()                # protege la caché
        self._una_a_la_vez = threading.Lock()           # una sola lectura a ONOS a la vez en todo el panel

    def obtener(self, dpid: str, maestro: Optional[str], forzar: bool = False) -> dict:
        """{flujos (crudos), nodo, t, antiguo, error}. Nunca lanza."""
        if not DPID.match(dpid or ""):
            return {"flujos": [], "nodo": None, "t": None, "antiguo": False, "error": "identificador de switch inválido"}
        with self._cerrojo:
            previo = self._cache.get(dpid)
        t = self.ahora()
        if previo and previo.get("flujos") is not None:
            edad = t - previo["t"]
            if edad < self.minimo_s or (edad < self.ttl_s and not forzar):
                return dict(previo, antiguo=False, error=None, desde_cache=True)
        if maestro not in self.onos:
            return self._fallo(previo, f"no se sabe qué nodo manda en {dpid}: no se lee de otro para no cargar a ONOS")
        with self._una_a_la_vez:
            with self._cerrojo:                          # otra petición pudo leerlo mientras se esperaba el turno
                previo2 = self._cache.get(dpid)
            if previo2 and previo2 is not previo and self.ahora() - previo2["t"] < self.minimo_s:
                return dict(previo2, antiguo=False, error=None, desde_cache=True)
            datos, err = self.leer(f"{self.onos[maestro]}/onos/v1/flows/{dpid}", usuario=self.usuario, clave=self.clave, timeout=self.timeout_s)
        if err or not isinstance(datos, dict):
            return self._fallo(previo, f"ONOS {maestro}: {err or 'respuesta inesperada'}")
        res = {"flujos": datos.get("flows", []), "nodo": maestro, "t": self.ahora(), "antiguo": False, "error": None}
        with self._cerrojo:
            self._cache[dpid] = res
        return res

    def _fallo(self, previo: Optional[dict], motivo: str) -> dict:
        if previo and previo.get("flujos") is not None:
            return dict(previo, antiguo=True, error=motivo)
        return {"flujos": [], "nodo": None, "t": None, "antiguo": False, "error": motivo}


# ---------- política esperada frente a lo instalado ----------
def comparar(esperados: List[dict], instalados: List[dict]) -> dict:
    """Compara los flujos que R2 ESPERA en un switch (calculados con policy_tables) con los que ONOS tiene bajo el appId de R2. Ambos ya normalizados. Dos flujos son el mismo si coinciden
    tabla, prioridad, campos y acciones (un multiconjunto: si la política espera dos iguales y hay uno, falta uno). Devuelve {coinciden, faltan, sobran}: `faltan` = esperados que no están
    instalados (R2 no los instaló o ONOS los perdió); `sobran` = instalados que la política ya no pide (una política vieja, o a medio aplicar)."""
    clave = lambda x: (x["tabla"], x["prioridad"], x["coincide_texto"], tuple(sorted(x["acciones_texto"].split("; "))))      # noqa: E731  (el orden de las acciones no cuenta: ONOS puede devolverlas reordenadas)
    exp: Dict[tuple, List[dict]] = {}
    ins: Dict[tuple, List[dict]] = {}
    for x in esperados:
        exp.setdefault(clave(x), []).append(x)
    for x in instalados:
        ins.setdefault(clave(x), []).append(x)
    faltan, sobran, coinciden = [], [], 0
    for k in set(exp) | set(ins):
        e, i = exp.get(k, []), ins.get(k, [])
        coinciden += min(len(e), len(i))
        faltan += e[len(i):]
        sobran += i[len(e):]
    orden = lambda x: (x["tabla"], -x["prioridad"], x["coincide_texto"])                         # noqa: E731
    return {"esperados": len(esperados), "instalados": len(instalados), "coinciden": coinciden, "faltan": sorted(faltan, key=orden), "sobran": sorted(sobran, key=orden)}
