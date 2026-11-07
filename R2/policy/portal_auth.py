"""Validación del portal cautivo: usuario y contraseña contra LDAP, rol por grupo y ubicación del host en ONOS.

Funciones puras en lo posible (`rol_de_grupos`) para poder probarlas sin LDAP ni ONOS.
"""
import os
from typing import Iterable, Optional, Tuple

import httpx

LDAP_URL = os.getenv("LDAP_URL", "ldap://ldap:389")
LDAP_BASE = os.getenv("LDAP_BASE", "dc=example,dc=org")
LDAP_ADMIN_DN = os.getenv("LDAP_ADMIN_DN", f"cn=admin,{LDAP_BASE}")
LDAP_ADMIN_PASSWORD = os.getenv("LDAP_ADMIN_PASSWORD", "admin")   # laboratorio: ver compose.yml


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


async def ubicar_host(mac: str, onos_url: str, auth: tuple) -> Optional[Tuple[str, str]]:
    """(dpid, puerto) donde ONOS ve a la MAC, o None."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{onos_url}/hosts/{mac.upper()}/None", auth=auth, timeout=3.0)
            if r.status_code != 200:
                return None
            loc = r.json()["locations"][0]
            return loc["elementId"], str(loc["port"])
        except (httpx.RequestError, ValueError, KeyError, IndexError):
            return None
