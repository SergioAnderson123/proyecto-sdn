"""policy_tables.py

R2: instala en los switches las reglas que deciden QUÉ recursos puede alcanzar cada rol.

El flujo de sesión (prioridad 40001, tabla 0) ya manda el tráfico de un usuario autenticado a la tabla de su rol
(10 docente, 20 alumno). Este módulo llena esas tablas a partir de `policy.yaml`:

  * tabla de rol : ARP -> NORMAL; por cada servicio permitido, ip_dst + tcp_dst -> NORMAL; el resto, DROP (prioridad 0).
  * puertos de confianza (servidores, troncales): tabla 0, prioridad 40001 -> NORMAL, para que el tráfico de regreso
    no pase por las tablas de rol ni por el ARP->controlador de R1.

Al salir hacia la red se quita la etiqueta VLAN (VLAN_POP): los hosts no hablan 802.1Q.
Las funciones `flujos_*` son puras (sin red) para poder probarlas; `sincronizar` las envía a ONOS por REST.
"""
import os
from collections import Counter
from typing import Dict, List, Optional

import httpx
import yaml

POLICY_PATH = os.getenv(
    "POLICY_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy.yaml")
)
APP_ID = "org.campus.r2"
PRIO_ROL = 30000        # reglas permitidas dentro de una tabla de rol
PRIO_CONFIANZA = 40001  # sobre el ARP/EAPOL->controlador de R1 (40000)


def cargar_politica(ruta: Optional[str] = None) -> dict:
    with open(ruta or POLICY_PATH, encoding="utf8") as f:
        p = yaml.safe_load(f) or {}
    for clave in ("roles", "services", "trusted", "rules"):
        p.setdefault(clave, {} if clave != "rules" else [])
    return p


PRIO_SESION = 40001     # flujo de sesión del Policy Mapper: in_port + eth_src -> push_vlan + goto_table


def es_flujo_de_sesion(flujo: dict) -> bool:
    """Flujo de sesión de un usuario (tabla 0, prioridad 40001, con ETH_SRC); no los de confianza (sin ETH_SRC)."""
    return (flujo.get("priority") == PRIO_SESION and flujo.get("tableId") == 0
            and any(c.get("type") == "ETH_SRC" for c in flujo.get("selector", {}).get("criteria", [])))


def servicios_permitidos(politica: dict, rol_matriz: str) -> Dict[str, bool]:
    """{servicio: permitido} para un rol de la matriz; lo que no figura queda denegado."""
    res: Dict[str, bool] = {}
    for r in politica["rules"]:
        if r["src"] == rol_matriz:
            res[r["dst"]] = bool(r["allow"])
    return res


def _base(dpid: str, tabla: int, prioridad: int, criterios: list, instrucciones: list) -> dict:
    return {
        "priority": prioridad,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": dpid,
        "tableId": tabla,
        "treatment": {"instructions": instrucciones},
        "selector": {"criteria": criterios},
    }


_SALIDA = [
    {"type": "L2MODIFICATION", "subtype": "VLAN_POP"},
    {"type": "OUTPUT", "port": "NORMAL"},
]


def flujos_tabla_rol(politica: dict, dpid: str, rol: str, tabla: int) -> List[dict]:
    """Flujos de la tabla de un rol de LDAP/RADIUS (por ejemplo 'docente')."""
    rol_matriz = politica["roles"][rol]["matriz"]
    permitidos = servicios_permitidos(politica, rol_matriz)
    # VLAN_VID es obligatorio: OVS rechaza pop_vlan (MATCH_INCONSISTENT) si el match no garantiza que el paquete lleva etiqueta.
    # La tabla de un rol coincide con su VLAN (el flujo de sesión hizo push_vlan con ese id).
    vlan = {"type": "VLAN_VID", "vlanId": tabla}
    flujos = [_base(dpid, tabla, PRIO_ROL, [vlan, {"type": "ETH_TYPE", "ethType": "0x0806"}], _SALIDA)]  # ARP
    for nombre, ok in sorted(permitidos.items()):
        srv = politica["services"].get(nombre)
        if not ok or not srv:
            continue
        flujos.append(_base(dpid, tabla, PRIO_ROL, [
            vlan,
            {"type": "ETH_TYPE", "ethType": "0x0800"},
            {"type": "IP_PROTO", "protocol": 6},
            {"type": "IPV4_DST", "ip": f"{srv['ip']}/32"},
            {"type": "TCP_DST", "tcpPort": int(srv["tcp"])},
        ], _SALIDA))
    flujos.append(_base(dpid, tabla, 0, [], []))   # sin instrucciones = DROP explícito
    return flujos


def flujos_confianza(politica: dict, dpid: str) -> List[dict]:
    t = politica["trusted"].get(dpid)
    if not t:
        return []
    salida = [{"type": "OUTPUT", "port": "NORMAL"}]
    if t == "all":
        return [_base(dpid, 0, PRIO_CONFIANZA, [], salida)]
    return [_base(dpid, 0, PRIO_CONFIANZA, [{"type": "IN_PORT", "port": int(p)}], salida) for p in t]


def flujos_dispositivo(politica: dict, dpid: str) -> List[dict]:
    """Todo lo que R2 instala en un switch. Los de confianza total no llevan tablas de rol (no tienen usuarios)."""
    flujos = flujos_confianza(politica, dpid)
    if politica["trusted"].get(dpid) != "all":
        for rol, datos in politica["roles"].items():
            flujos += flujos_tabla_rol(politica, dpid, rol, int(datos["tabla"]))
    return flujos


async def dispositivos(onos_url: str, auth: tuple) -> List[str]:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{onos_url}/devices", auth=auth, timeout=3.0)
            return [d["id"] for d in r.json().get("devices", []) if d.get("available")]
        except (httpx.RequestError, ValueError):
            return []


async def sincronizar(politica: dict, dpids: List[str], onos_url: str, auth: tuple) -> Dict[str, int]:
    """Envía a ONOS los flujos de R2 de cada switch (idempotente: mismo selector y prioridad = mismo flujo)."""
    resultado: Dict[str, int] = {}
    async with httpx.AsyncClient() as client:
        for dpid in dpids:
            flujos = flujos_dispositivo(politica, dpid)
            if not flujos:
                continue
            try:
                r = await client.post(f"{onos_url}/flows", params={"appId": APP_ID},
                                      json={"flows": flujos}, auth=auth, timeout=5.0)
                resultado[dpid] = len(flujos) if r.status_code in (200, 201) else -r.status_code
                if r.status_code not in (200, 201):
                    print(f"  [R2 TABLAS ERROR] {dpid}: ONOS respondió {r.status_code}: {r.text[:200]}")
            except httpx.RequestError as e:
                print(f"  [R2 TABLAS ERROR] {dpid}: {e}")
    return resultado


async def purgar_sesiones_huerfanas(onos_url: str, auth: tuple) -> int:
    """Borra de ONOS los flujos de sesión que sobreviven a un reinicio del Policy Mapper o de la topología.

    Las sesiones viven en memoria del mapper pero sus flujos persisten en ONOS (y se reinstalan al reconectar un switch).
    Un flujo huérfano tiene prioridad sobre el EAPOL->controlador de R1: el supplicant del usuario nunca llega a
    autenticar y, peor, el tráfico seguiría pasando sin sesión. Tras un reinicio todos deben volver a autenticarse."""
    borrados = 0
    async with httpx.AsyncClient() as client:
        try:
            flujos = (await client.get(f"{onos_url}/flows", auth=auth, timeout=5.0)).json().get("flows", [])
        except (httpx.RequestError, ValueError):
            return 0
        for f in flujos:
            if es_flujo_de_sesion(f):
                try:
                    r = await client.delete(f"{onos_url}/flows/{f['deviceId']}/{f['id']}", auth=auth, timeout=3.0)
                    borrados += 1 if r.status_code in (200, 204) else 0
                except httpx.RequestError:
                    pass
    return borrados


def dispositivos_incompletos(politica: dict, dpids: List[str], flujos_onos: List[dict]) -> List[str]:
    """Switches a los que les faltan flujos de R2 (pura: recibe los flujos que ONOS tiene bajo el appId de R2).

    Reenviar siempre todo ponía los flujos en PENDING_ADD y reiniciaba sus contadores en cada admisión y cada 15 s."""
    presentes = Counter(f["deviceId"] for f in flujos_onos if f.get("state") == "ADDED")
    return [d for d in dpids if presentes[d] < len(flujos_dispositivo(politica, d))]


async def faltantes(politica: dict, dpids: List[str], onos_url: str, auth: tuple) -> List[str]:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{onos_url}/flows/application/{APP_ID}", auth=auth, timeout=3.0)
            flujos = r.json().get("flows", []) if r.status_code == 200 else []   # 404: el appId aún no existe
        except (httpx.RequestError, ValueError):
            return list(dpids)                                                  # sin información: se reenvía por seguridad
    return dispositivos_incompletos(politica, dpids, flujos)
