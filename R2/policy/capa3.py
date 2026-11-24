"""capa3.py: reenvío proactivo por SUBREDES (capa 3) para que las reglas dejen de crecer con el número de hosts. Solo calcula la lista de flujos (funciones puras, sin ONOS).

Diseño y razones: docs/DISENO_CAPA3.md. Resumen:
  - Una subred por switch de acceso (o por grupo de puertos de un switch). Cada subred tiene un gateway VIRTUAL (IP y una MAC compartida `gw_mac`): no existe en ningún equipo; lo
    contesta el propio switch de acceso.
  - Dentro de la subred (mismo switch): ARP por inundación LOCAL (solo a los puertos de acceso de esa subred; jamás sale por una troncal) y reenvío por MAC.
  - Entre subredes: el host manda a la MAC del gateway; los switches enrutan por PREFIJO (una regla por subred, no por host) siguiendo el árbol hacia el switch de la subred destino;
    el switch del destino reescribe las MAC (origen = gateway, destino = host) y entrega por el puerto del host (una regla por host, solo en su propio switch).
  - Nada inunda entre switches: no hay árbol de difusión, solo flujos unicast por prefijo. Un lazo físico no puede multiplicar nada.
Todo va en la tabla de reenvío (60), igual que el modo de capa 2: las tablas de rol de R2 (política por IP+puerto) no cambian.

Estado: diseño + cálculo + pruebas sin red. Los flujos de ARP del gateway usan acciones de extensión de Open vSwitch y criterios (ARP_OP, ARP_TPA, DEC_TTL) que el REST de ONOS 2.7 podría no
aceptar: está PENDIENTE verificarlo en vivo (ver "Prueba de viabilidad" en el diseño).
"""
import ipaddress
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

import caminos
import flujos_proactivos as fp

TABLA_REENVIO = fp.TABLA_REENVIO                # 60
MAC_BROADCAST = fp.MAC_BROADCAST

PRIO_ARP_GATEWAY = 300      # contesta el ARP del gateway (sobre la inundación local)
PRIO_ARP_LOCAL = fp.PRIO_DIFUSION   # 200: ARP en difusión, solo hacia puertos de acceso del mismo switch
PRIO_ENTREGA = 160          # IP de un host de este switch (/32): reescribe MAC y entrega
PRIO_RUTA = 150             # prefijo de otra subred: hacia el switch de esa subred
PRIO_SUBIDA = 120           # modo con ruta por defecto: lo que sale de un puerto de acceso va "hacia arriba"
PRIO_BLOQUEO = 130          # con salida al exterior: las direcciones privadas sin ruta se descartan (nunca salen por la ruta por defecto)
PRIO_DEFECTO = 110          # con salida al exterior: todo lo demás va hacia el switch de salida y sale por el siguiente salto
BLOQUEO_PRIVADO = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
PRIO_L2_LOCAL = fp.PRIO_UNICAST     # 100: MAC de un host de este switch
PRIO_DESCARTE = fp.PRIO_DESCARTE    # 1

_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)


class PlanInvalido(ValueError):
    """El plan de subredes no es coherente: no se genera ningún flujo."""


class Subred:
    def __init__(self, nombre: str, dispositivo: str, prefijo: str, gateway: str, puertos: Iterable[int]):
        self.nombre, self.dispositivo = nombre, dispositivo
        self.prefijo = ipaddress.ip_network(prefijo, strict=True)
        self.gateway = ipaddress.ip_address(gateway)
        self.puertos = tuple(sorted({int(p) for p in puertos}))
        if self.prefijo.version != 4 or self.gateway.version != 4:
            raise PlanInvalido(f"{nombre}: solo IPv4")
        if self.gateway not in self.prefijo or self.gateway in (self.prefijo.network_address, self.prefijo.broadcast_address):
            raise PlanInvalido(f"{nombre}: el gateway {gateway} debe ser una dirección utilizable de {prefijo}")
        if not self.puertos:
            raise PlanInvalido(f"{nombre}: sin puertos de acceso")

    def __repr__(self):
        return f"Subred({self.nombre} {self.prefijo} gw {self.gateway} en {self.dispositivo}{list(self.puertos)})"


class Salida:
    """Ruta por defecto hacia el exterior: el switch `dispositivo` entrega por `puerto` a un único SIGUIENTE SALTO (la MAC del equipo del otro lado del enlace, como la del router del
    proveedor). Así el exterior no se da de alta host por host: cualquier IP que no sea de la red interna sale por ahí. `bloquear`: prefijos que jamás salen (por defecto, los privados)."""

    def __init__(self, dispositivo: str, puerto, mac: str, bloquear: Optional[Iterable[str]] = None):
        if not _MAC.match(str(mac)):
            raise PlanInvalido(f"salida: MAC del siguiente salto inválida: {mac}")
        try:
            self.puerto = int(puerto)
        except (TypeError, ValueError):
            raise PlanInvalido(f"salida: puerto inválido: {puerto}")
        if self.puerto < 1:
            raise PlanInvalido(f"salida: puerto inválido: {puerto}")
        self.dispositivo, self.mac = dispositivo, str(mac).upper()
        try:
            self.bloquear = tuple(ipaddress.ip_network(p, strict=True) for p in (BLOQUEO_PRIVADO if bloquear is None else bloquear))
        except ValueError as e:
            raise PlanInvalido(f"salida: prefijo a bloquear inválido ({e})")
        if any(p.version != 4 for p in self.bloquear):
            raise PlanInvalido("salida: solo IPv4")

    def __repr__(self):
        return f"Salida({self.dispositivo}/{self.puerto} -> {self.mac})"


class Plan:
    def __init__(self, gw_mac: str, subredes: Iterable[Subred], salida: Optional[Salida] = None):
        if not _MAC.match(gw_mac):
            raise PlanInvalido(f"MAC del gateway inválida: {gw_mac}")
        self.gw_mac = gw_mac.upper()
        self.subredes: List[Subred] = list(subredes)
        self.salida = salida
        self._validar()

    def _validar(self):
        nombres = [s.nombre for s in self.subredes]
        if len(set(nombres)) != len(nombres):
            raise PlanInvalido("nombres de subred repetidos")
        for i, a in enumerate(self.subredes):
            for b in self.subredes[i + 1:]:
                if a.prefijo.overlaps(b.prefijo):
                    raise PlanInvalido(f"los prefijos de {a.nombre} y {b.nombre} se solapan")
                if a.dispositivo == b.dispositivo and set(a.puertos) & set(b.puertos):
                    raise PlanInvalido(f"{a.nombre} y {b.nombre} comparten puertos en {a.dispositivo}")

    def subred_de(self, ip: str) -> Optional[Subred]:
        try:
            direccion = ipaddress.ip_address(ip)
        except ValueError:
            return None
        return next((s for s in self.subredes if direccion in s.prefijo), None)


def cargar_plan(datos: dict, nombres: Optional[Dict[str, str]] = None) -> Plan:
    """Desde un diccionario (JSON): {"gw_mac": "...", "subredes": [{"nombre","dispositivo","prefijo","gateway","puertos":[...]}]}.
    `dispositivo` puede ser el nombre (core-1, acc-1...) o el `of:...` si se pasa `nombres` (dpid -> nombre)."""
    por_nombre = {v: k for k, v in (nombres or {}).items()}
    subredes = []
    for s in datos.get("subredes", []):
        try:
            d = s["dispositivo"]
            subredes.append(Subred(s["nombre"], por_nombre.get(d, d), s["prefijo"], s["gateway"], s["puertos"]))
        except KeyError as e:
            raise PlanInvalido(f"a una subred le falta el campo {e}")
        except ValueError as e:
            raise PlanInvalido(str(e))
    if not subredes:
        raise PlanInvalido("el plan no tiene subredes")
    salida = None
    if datos.get("salida") is not None:
        s = datos["salida"]
        try:
            d = s["dispositivo"]
            salida = Salida(por_nombre.get(d, d), s["puerto"], s["mac"], s.get("bloquear"))
        except KeyError as e:
            raise PlanInvalido(f"a la salida le falta el campo {e}")
    return Plan(datos.get("gw_mac", ""), subredes, salida)


def hosts_validos(plan: Plan, hosts: Iterable[dict]) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """Separa los hosts que encajan en el plan (IP dentro de una subred, y en el switch y puerto de esa subred) de los que no, con el motivo.
    Host = {"mac", "ip", "device", "port"}. Un host sin IP todavía no recibe reglas: su tráfico se descarta (falla cerrado)."""
    validos, rechazados = [], []
    for h in hosts:
        ip = h.get("ip")
        if not ip:
            rechazados.append((h, "sin IP conocida"))
            continue
        s = plan.subred_de(ip)
        if s is None:
            rechazados.append((h, "su IP no está en ninguna subred del plan"))
        elif h["device"] != s.dispositivo or int(h["port"]) not in s.puertos:
            rechazados.append((h, f"IP de {s.nombre} pero conectado en {h['device']}/{h['port']}"))
        elif ipaddress.ip_address(ip) == s.gateway:
            rechazados.append((h, "usa la IP del gateway"))
        else:
            validos.append(dict(h, _subred=s.nombre))
    return validos, rechazados


# ---------- instrucciones ----------
def _dec_ttl():
    return {"type": "L3MODIFICATION", "subtype": "DEC_TTL"}


def _salida(puerto) -> dict:
    return {"type": "OUTPUT", "port": str(puerto)}


def _ext(nombre: str) -> dict:
    return {"type": "EXTENSION", "extensionType": nombre}


def _respuesta_arp_gateway(gw_mac: str, gw_ip: str, copiar: bool) -> List[dict]:
    """Convierte la petición ARP en la respuesta del gateway, sin controlador (idioma conocido de Open vSwitch con extensiones de Nicira)."""
    acciones = []
    if copiar:
        acciones.append({"type": "OUTPUT", "port": "CONTROLLER"})        # copia la PETICIÓN original (así ONOS aprende MAC, IP y puerto del host)
    acciones += [
        _ext("NICIRA_MOVE_ETH_SRC_TO_DST"),
        {"type": "L2MODIFICATION", "subtype": "ETH_SRC", "mac": gw_mac},
        {"type": "L3MODIFICATION", "subtype": "ARP_OP", "op": 2},
        _ext("NICIRA_MOVE_ARP_SHA_TO_THA"),
        _ext("NICIRA_MOVE_ARP_SPA_TO_TPA"),
        {"type": "L3MODIFICATION", "subtype": "ARP_SHA", "mac": gw_mac},
        {"type": "L3MODIFICATION", "subtype": "ARP_SPA", "ip": gw_ip},
        {"type": "OUTPUT", "port": "IN_PORT"},
    ]
    return acciones


# ---------- flujos por subred ----------
def _flujos_de_subred(plan: Plan, s: Subred, hosts: List[dict], copiar_arp: bool, tabla: int = TABLA_REENVIO, arp_gateway: bool = True) -> List[dict]:
    d, gw = s.dispositivo, plan.gw_mac
    flujos = []
    for p in s.puertos:
        if arp_gateway:                                                    # estático: no cabe en el REST de ONOS (ver DISENO_CAPA3.md); lo instala otro mecanismo
            flujos.append(fp._base(d, tabla, PRIO_ARP_GATEWAY, [
                {"type": "IN_PORT", "port": p}, {"type": "ETH_TYPE", "ethType": "0x0806"}, {"type": "ARP_OP", "op": 1},
                {"type": "ARP_TPA", "ip": f"{s.gateway}/32"}], _respuesta_arp_gateway(gw, str(s.gateway), copiar_arp)))
        otros = [q for q in s.puertos if q != p]
        if otros:                                                          # inundación LOCAL: solo puertos de acceso de esta subred, nunca troncales
            flujos.append(fp._base(d, tabla, PRIO_ARP_LOCAL, [
                {"type": "IN_PORT", "port": p}, {"type": "ETH_TYPE", "ethType": "0x0806"}, {"type": "ETH_DST", "mac": MAC_BROADCAST}],
                [_salida(q) for q in otros]))
    for h in hosts:
        mac = h["mac"].upper()
        flujos.append(fp._base(d, tabla, PRIO_L2_LOCAL, [{"type": "ETH_DST", "mac": mac}], [_salida(h["port"])]))
        flujos.append(fp._base(d, tabla, PRIO_ENTREGA, [
            {"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": gw}, {"type": "IPV4_DST", "ip": f"{h['ip']}/32"}], [
            {"type": "L2MODIFICATION", "subtype": "ETH_SRC", "mac": gw}, {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": mac},
            _dec_ttl(), _salida(h["port"])]))
    return flujos


def flujos_capa3(plan: Plan, grafo: caminos.Grafo, hosts: Iterable[dict], preferidos: Iterable[str] = (), nucleo: Optional[Iterable[str]] = None,
                 copiar_arp: bool = False, tabla: int = TABLA_REENVIO, arp_gateway: bool = True) -> List[dict]:
    """Todos los flujos de la tabla de reenvío en modo capa 3.

    `nucleo`: switches que llevan UNA regla por cada prefijo (rutas completas). Con `None`, todos. Si se indica, los demás switches con subredes (los de acceso) llevan solo una
    regla por defecto "hacia arriba" por puerto de acceso: sus reglas dejan de crecer con el número de subredes (ver el diseño). `copiar_arp`: copia al controlador el ARP del gateway.
    `arp_gateway=False`: sin los flujos del ARP del gateway (son estáticos y se instalan aparte; el vigilante solo gestiona lo dinámico, que sí cabe en el REST de ONOS).
    `tabla`: tabla OpenFlow donde van los flujos (60 en producción; otra, p. ej. 62, para la prueba de viabilidad aislada de producción)."""
    preferidos = list(preferidos)
    validos, _ = hosts_validos(plan, hosts)
    por_subred = defaultdict(list)
    for h in validos:
        por_subred[h["_subred"]].append(h)
    conocidos = set(grafo)
    for s in plan.subredes:
        if s.dispositivo not in conocidos:
            raise PlanInvalido(f"{s.nombre}: el switch {s.dispositivo} no está en la topología")
    flujos: List[dict] = [fp._base(d, tabla, PRIO_DESCARTE, [], []) for d in sorted(grafo)]
    for s in plan.subredes:
        flujos += _flujos_de_subred(plan, s, sorted(por_subred[s.nombre], key=lambda h: h["mac"].upper()), copiar_arp, tabla, arp_gateway)
    arboles: Dict[str, caminos.Arbol] = {}
    nucleo_set = None if nucleo is None else set(nucleo)
    for s in plan.subredes:
        arbol = arboles.setdefault(s.dispositivo, caminos.arbol_hacia(grafo, s.dispositivo, preferidos))
        for d in sorted(grafo):
            if d == s.dispositivo or d not in arbol or (nucleo_set is not None and d not in nucleo_set):
                continue
            flujos.append(fp._base(d, tabla, PRIO_RUTA, [
                {"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": plan.gw_mac},
                {"type": "IPV4_DST", "ip": str(s.prefijo)}], [_dec_ttl(), _salida(arbol[d][1])]))
    if nucleo_set is not None:
        raiz = next((p for p in preferidos if p in grafo), None)
        arriba = caminos.arbol_hacia(grafo, raiz, preferidos) if raiz else {}
        acceso: Dict[str, set] = defaultdict(set)
        for s in plan.subredes:
            if s.dispositivo not in nucleo_set:
                acceso[s.dispositivo] |= set(s.puertos)
        for d in sorted(acceso):
            if d not in arriba:
                continue                                                  # sin camino a la raíz: sin ruta (descarta)
            for p in sorted(acceso[d]):
                flujos.append(fp._base(d, tabla, PRIO_SUBIDA, [
                    {"type": "IN_PORT", "port": p}, {"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": plan.gw_mac}],
                    [_dec_ttl(), _salida(arriba[d][1])]))
    if plan.salida is not None:
        flujos += _flujos_de_salida(plan, grafo, preferidos, nucleo_set, tabla)
    return flujos


def _flujos_de_salida(plan: Plan, grafo: caminos.Grafo, preferidos: List[str], nucleo_set: Optional[set], tabla: int) -> List[dict]:
    """Ruta por defecto hacia el exterior. Todo en la tabla de reenvío, solo para IPv4 con la MAC del gateway (igual que el resto de la capa 3):
      - switch de salida: una regla (PRIO_DEFECTO) que reescribe origen = gateway y destino = siguiente salto, decrementa el TTL y saca por el puerto de salida;
      - los demás switches con rutas completas: una regla (PRIO_DEFECTO) hacia el switch de salida por el árbol;
      - en todos ellos, el descarte de los prefijos privados (PRIO_BLOQUEO): sobre la ruta por defecto, bajo las rutas y entregas reales, así que una IP interna que no existe
        se descarta en vez de salir al exterior.
    Una IP externa nueva no necesita ninguna regla nueva."""
    sal = plan.salida
    if sal.dispositivo not in grafo:
        raise PlanInvalido(f"la salida usa el switch {sal.dispositivo}, que no está en la topología")
    if nucleo_set is not None and sal.dispositivo not in nucleo_set:
        raise PlanInvalido(f"el switch de salida {sal.dispositivo} debe ser del núcleo (switches con rutas completas)")
    gw = plan.gw_mac
    ipv4_al_gateway = [{"type": "ETH_TYPE", "ethType": "0x0800"}, {"type": "ETH_DST", "mac": gw}]
    arbol = caminos.arbol_hacia(grafo, sal.dispositivo, preferidos)
    flujos = [fp._base(sal.dispositivo, tabla, PRIO_DEFECTO, ipv4_al_gateway, [
        {"type": "L2MODIFICATION", "subtype": "ETH_SRC", "mac": gw}, {"type": "L2MODIFICATION", "subtype": "ETH_DST", "mac": sal.mac},
        _dec_ttl(), _salida(sal.puerto)])]
    con_rutas = [sal.dispositivo] + [d for d in sorted(grafo) if d != sal.dispositivo and d in arbol and (nucleo_set is None or d in nucleo_set)]
    for d in con_rutas:
        if d != sal.dispositivo:
            flujos.append(fp._base(d, tabla, PRIO_DEFECTO, ipv4_al_gateway, [_dec_ttl(), _salida(arbol[d][1])]))
        for prefijo in sal.bloquear:
            flujos.append(fp._base(d, tabla, PRIO_BLOQUEO, ipv4_al_gateway + [{"type": "IPV4_DST", "ip": str(prefijo)}], []))
    return flujos


# ---------- escala ----------
def contar_por_switch(flujos: Iterable[dict]) -> Dict[str, int]:
    c: Dict[str, int] = defaultdict(int)
    for f in flujos:
        c[f["deviceId"]] += 1
    return dict(c)


def reglas_capa2(hosts: int, switches: int, puertos_arbol_por_switch: int = 3) -> int:
    """Reglas de la tabla 60 del modo de capa 2 (flujos_proactivos): un unicast por (switch, host), una difusión ARP por puerto del árbol/acceso y un descarte por switch."""
    return switches * hosts + switches * (puertos_arbol_por_switch + 1) + switches
