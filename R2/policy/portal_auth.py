"""Validación del portal cautivo: usuario y contraseña contra LDAP, rol por grupo y ubicación del host en ONOS.

Funciones puras en lo posible (`rol_de_grupos`) para poder probarlas sin LDAP ni ONOS.
"""
import ipaddress
import os
import time
from typing import Iterable, Optional, Tuple

import httpx
# R2 tools can run without R1 on their import path.
HA_ENABLED = bool(os.getenv("SESSION_ETCD_ENDPOINTS"))
if HA_ENABLED:
    from ha_onos import AsyncClient as HaAsyncClient

LDAP_URL = os.getenv(
    "LDAP_URL",
    f"ldap://{os.getenv('LDAP_HOST', 'ldap')}:{os.getenv('LDAP_PORT', '389')}",
)
LDAP_BASE = os.getenv("LDAP_BASE", "dc=example,dc=org")
LDAP_ADMIN_DN = os.getenv("LDAP_ADMIN_DN", f"cn=admin,{LDAP_BASE}")
LDAP_ADMIN_PASSWORD = os.getenv("LDAP_ADMIN_PASSWORD", "")


def rol_de_grupos(politica: dict, grupos: Iterable[str]) -> Optional[str]:
    """Rol del primer grupo LDAP que figure en `portal.grupos` (None si ninguno: sin acceso)."""
    tabla = politica.get("portal", {}).get("grupos", {})
    for g in grupos:
        if g in tabla:
            return tabla[g]
    return None


def autenticar_ldap(usuario: str, clave: str) -> Optional[list]:
    """Bind simple como el usuario; devuelve sus grupos (cn) si la clave es correcta, None si no."""
    if not usuario or not clave:                      # un bind sin clave es anónimo y "tendría éxito"
        return None
    from ldap3 import Server, Connection
    from ldap3.utils.conv import escape_filter_chars
    from ldap3.utils.dn import escape_rdn
    dn = f"uid={escape_rdn(usuario)},ou=users,{LDAP_BASE}"
    try:
        con = Connection(Server(LDAP_URL, connect_timeout=3), user=dn, password=clave, receive_timeout=3)
        if not con.bind():
            return None
        con.unbind()
        # un usuario normal no puede leer los grupos: se consultan con la cuenta de servicio (solo lectura de membresías)
        adm = Connection(Server(LDAP_URL, connect_timeout=3), user=LDAP_ADMIN_DN, password=LDAP_ADMIN_PASSWORD,
                         receive_timeout=3, auto_bind=True)
        adm.search(f"ou=groups,{LDAP_BASE}", f"(member={escape_filter_chars(dn)})", attributes=["cn"])
        grupos = [str(e.cn) for e in adm.entries]
        adm.unbind()
        return grupos
    except Exception:                                  # LDAP caído: se rechaza (cierra por defecto)
        return None


def elegir_mac(hosts, ip: str) -> Optional[str]:
    """MAC (en minúsculas) del ÚNICO host de ONOS que tiene esa IP; None si ninguno o si hay más de uno distinto (ambiguo: no se adivina).

    Función pura sobre la lista `hosts` de `GET /onos/v1/hosts`, para poder probarla sin ONOS."""
    macs = set()
    for h in hosts or []:
        try:
            if ip in (h.get("ipAddresses") or []):
                macs.add(h["mac"].lower())
        except (AttributeError, KeyError, TypeError):
            continue
    return macs.pop() if len(macs) == 1 else None


_HOSTS_TTL_S = float(os.getenv("PORTAL_HOSTS_TTL_S", "5"))
_hosts_cache = {"t": None, "hosts": []}      # una visita al portal pregunta la MAC varias veces: la lista de hosts se guarda unos segundos (también un fallo, para no martillear a ONOS)


async def mac_de_ip(ip: str, onos_url: str, auth: tuple, reloj=time.monotonic) -> Optional[str]:
    """MAC del equipo al que ONOS asocia esa IP, o None.

    Es lo que permite identificar a un cliente de OTRA subred (capa 3): el portal ya no es vecino de capa 2 del cliente y su tabla ARP no lo tiene, pero la app DHCP
    de ONOS registra IP, MAC y ubicación de cada concesión. La IP es la de ORIGEN de la conexión del cliente al portal (la ve el socket del portal; no es un dato del cliente).
    Si varios hosts tienen esa IP no se adivina."""
    try:
        ip = str(ipaddress.IPv4Address(str(ip).strip()))
    except ValueError:
        return None
    ahora = reloj()
    if _hosts_cache["t"] is None or ahora - _hosts_cache["t"] >= _HOSTS_TTL_S:
        hosts = []
        try:
            async with (HaAsyncClient if HA_ENABLED else httpx.AsyncClient)() as client:
                r = await client.get(f"{onos_url}/hosts", auth=auth, timeout=3.0)
                if r.status_code == 200:
                    hosts = r.json()["hosts"]
        except (httpx.RequestError, ValueError, KeyError):
            hosts = []
        _hosts_cache.update(t=ahora, hosts=hosts)
    return elegir_mac(_hosts_cache["hosts"], ip)


async def ubicar_host(mac: str, onos_url: str, auth: tuple) -> Optional[Tuple[str, str]]:
    """(dpid, puerto) donde ONOS ve a la MAC, o None."""
    async with (HaAsyncClient if HA_ENABLED else httpx.AsyncClient)() as client:
        try:
            r = await client.get(f"{onos_url}/hosts/{mac.upper()}/None", auth=auth, timeout=3.0)
            if r.status_code != 200:
                return None
            loc = r.json()["locations"][0]
            return loc["elementId"], str(loc["port"])
        except (httpx.RequestError, ValueError, KeyError, IndexError):
            return None
