"""excepciones.py: permisos individuales de una cuenta del portal (docs/DISENO_EXCEPCIONES_POR_SESION.md, fase 1).

Una excepción abre a UNA cuenta (por ejemplo `profesor02`) uno o varios servicios que su rol no tiene, sin crear rol, VLAN ni tabla.
Se instala en la tabla del rol de la sesión, con la regla del rol MÁS `ETH_SRC` = la MAC del equipo, a la prioridad PRIO_EXCEPCION.

Todo lo de este módulo es puro (sin red): leer y validar `excepciones.yaml`, decidir qué servicios tiene una cuenta ahora y generar los flujos.
Instalar, retirar y reconciliar es del Policy Mapper (fases 2 y 3).

Formato de `excepciones.yaml` (junto a `policy.yaml`, o donde diga EXCEPCIONES_PATH; si no existe, no hay excepciones):

    roles: [docente]                         # solo las cuentas con este rol pueden tener excepciones
    no_excepcionables: [network_management]  # servicios que nunca se abren por excepción
    lista:
      - {usuario: profesor02, servicios: [student_records], hasta: "2026-12-31", motivo: "Coordinación de matrícula"}
"""
import datetime as dt
import os
from typing import Dict, Iterable, List, Optional

import yaml

PRIO_EXCEPCION = 30500                    # sobre las reglas del rol (30000, PRIO_ROL) y en la misma tabla; la 40001 está en la tabla 0
APP_ID_EXCEPCIONES = "org.campus.excepciones"   # propio: el contador de `dispositivos_incompletos` solo mira el appId de R2
MAX_SERVICIOS_POR_USUARIO = 3             # decisión 5
MAX_USUARIOS = 10                         # decisión 5
MAX_DIAS_HASTA = 90                       # decisión 4: `hasta` es opcional, pero como mucho a 90 días de hoy
ROLES_POR_DEFECTO = ["docente"]
NO_EXCEPCIONABLES_POR_DEFECTO = ["network_management"]   # decisión 10


class ExcepcionesInvalidas(ValueError):
    """El archivo de excepciones no es válido. Lleva la lista de errores en `errores`."""

    def __init__(self, errores: List[str]):
        super().__init__("excepciones.yaml no es válido: " + "; ".join(errores))
        self.errores = errores


def ruta_excepciones(ruta_politica: str) -> str:
    return os.getenv("EXCEPCIONES_PATH") or os.path.join(os.path.dirname(os.path.abspath(ruta_politica)), "excepciones.yaml")


def leer(ruta: str) -> Optional[dict]:
    """El contenido de `excepciones.yaml`, o None si el archivo no existe (sin excepciones)."""
    if not os.path.isfile(ruta):
        return None
    with open(ruta, encoding="utf8") as f:
        return yaml.safe_load(f) or {}


def normalizar_usuario(usuario) -> str:
    """Cómo se compara una cuenta: sin espacios y sin distinguir mayúsculas (`Profesor02 ` = `profesor02`)."""
    return str(usuario or "").strip().lower()


def _fecha(valor) -> Optional[dt.date]:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    return dt.date.fromisoformat(str(valor).strip())


def _permitidos_por_rol(politica: dict, rol: str) -> Dict[str, bool]:
    from policy_tables import servicios_permitidos
    return servicios_permitidos(politica, politica["roles"][rol]["matriz"])


def validar(politica: dict, datos: dict, hoy: dt.date, alta: bool = False) -> dict:
    """Valida `datos` (el contenido de excepciones.yaml) contra la política y devuelve la sección normalizada:
    {roles, no_excepcionables, lista: [{usuario, servicios, hasta (date o None), motivo}]}.

    Lanza ExcepcionesInvalidas con TODOS los errores (no solo el primero), para que nada quede a medias.
    Un `hasta` ya pasado no invalida el archivo al cargarlo (si no, un archivo válido dejaría de cargar al día siguiente de vencer una
    excepción): esa excepción simplemente no se aplica. Con `alta=True` (al dar de alta desde el panel) un `hasta` pasado sí es un error."""
    errores: List[str] = []
    if not isinstance(datos, dict):
        raise ExcepcionesInvalidas(["el archivo debe ser un diccionario con roles, no_excepcionables y lista"])
    desconocidas = set(datos) - {"roles", "no_excepcionables", "lista"}
    if desconocidas:
        errores.append(f"claves desconocidas: {sorted(desconocidas)}")

    roles = datos.get("roles", ROLES_POR_DEFECTO)
    if not isinstance(roles, list) or not roles:
        errores.append("roles debe ser una lista no vacía")
        roles = []
    for r in roles:
        if r not in politica.get("roles", {}):
            errores.append(f"rol desconocido: {r}")
    roles = [r for r in roles if r in politica.get("roles", {})]

    servicios = politica.get("services", {})
    no_exc = datos.get("no_excepcionables", NO_EXCEPCIONABLES_POR_DEFECTO)
    if not isinstance(no_exc, list):
        errores.append("no_excepcionables debe ser una lista")
        no_exc = []
    for s in no_exc:
        if s not in servicios:
            errores.append(f"no_excepcionables: servicio desconocido: {s}")

    lista = datos.get("lista", [])
    if not isinstance(lista, list):
        errores.append("lista debe ser una lista")
        lista = []
    if len(lista) > MAX_USUARIOS:
        errores.append(f"como mucho {MAX_USUARIOS} usuarios con excepción (hay {len(lista)})")

    normal, vistos = [], set()
    for i, e in enumerate(lista, 1):
        donde = f"entrada {i}"
        if not isinstance(e, dict):
            errores.append(f"{donde}: debe ser un diccionario")
            continue
        extra = set(e) - {"usuario", "servicios", "hasta", "motivo"}
        if extra:
            errores.append(f"{donde}: claves desconocidas: {sorted(extra)}")
        usuario = normalizar_usuario(e.get("usuario"))
        if not usuario:
            errores.append(f"{donde}: usuario vacío")
        else:
            donde = f"entrada {i} ({usuario})"
            if usuario in vistos:
                errores.append(f"{donde}: usuario repetido; junte sus servicios en una sola entrada")
            vistos.add(usuario)
        motivo = str(e.get("motivo") or "").strip()
        if not motivo:
            errores.append(f"{donde}: falta el motivo")
        srvs = e.get("servicios")
        if not isinstance(srvs, list) or not srvs:
            errores.append(f"{donde}: servicios debe ser una lista no vacía")
            srvs = []
        if len(srvs) != len(set(srvs)):
            errores.append(f"{donde}: servicio repetido")
        if len(set(srvs)) > MAX_SERVICIOS_POR_USUARIO:
            errores.append(f"{donde}: como mucho {MAX_SERVICIOS_POR_USUARIO} servicios por usuario")
        for s in srvs:
            if s not in servicios:
                errores.append(f"{donde}: servicio desconocido: {s}")
            elif s in no_exc:
                errores.append(f"{donde}: {s} no se puede abrir por excepción")
            elif roles and all(_permitidos_por_rol(politica, r).get(s) for r in roles):
                errores.append(f"{donde}: {s} ya lo tiene el rol ({', '.join(roles)}); la excepción sobra")
        hasta = None
        if e.get("hasta") not in (None, ""):
            try:
                hasta = _fecha(e["hasta"])
            except (TypeError, ValueError):
                errores.append(f"{donde}: hasta debe ser una fecha AAAA-MM-DD")
            else:
                if hasta > hoy + dt.timedelta(days=MAX_DIAS_HASTA):
                    errores.append(f"{donde}: hasta pasa del máximo de {MAX_DIAS_HASTA} días")
                if alta and hasta < hoy:
                    errores.append(f"{donde}: hasta ya pasó")
        normal.append({"usuario": usuario, "servicios": sorted(set(srvs)), "hasta": hasta, "motivo": motivo})

    if errores:
        raise ExcepcionesInvalidas(errores)
    return {"roles": list(roles), "no_excepcionables": list(no_exc), "lista": normal}


def excepciones_de(politica: dict, usuario: str, rol: str, ahora: dt.datetime) -> List[str]:
    """Servicios extra que tiene ahora la cuenta `usuario` con el rol `rol` (vacío si no tiene, si su rol no admite excepciones o si
    venció). `hasta` incluye todo ese día."""
    sec = politica.get("excepciones")
    if not sec or rol not in sec["roles"]:
        return []
    u = normalizar_usuario(usuario)
    for e in sec["lista"]:
        if e["usuario"] == u:
            if e["hasta"] is not None and ahora.date() > e["hasta"]:
                return []
            return list(e["servicios"])
    return []


def flujos_excepcion(politica: dict, dpid: str, tabla: int, mac: str, servicios: Iterable[str]) -> List[dict]:
    """Un flujo por servicio en la tabla `tabla` (la del rol de la sesión): la regla del rol MÁS `ETH_SRC` = `mac`, a PRIO_EXCEPCION,
    con el mismo tratamiento que las reglas del rol (NORMAL, o la tabla 60 con el reenvío proactivo). Sin `ETH_SRC` abriría el
    servicio a todo el rol, así que una MAC vacía es un error, igual que un servicio desconocido o no excepcionable."""
    from policy_tables import _base, _salida
    if not str(mac or "").strip():
        raise ValueError("flujos_excepcion necesita la MAC del equipo")
    no_exc = set((politica.get("excepciones") or {}).get("no_excepcionables", NO_EXCEPCIONABLES_POR_DEFECTO))
    flujos = []
    for nombre in sorted(set(servicios)):
        srv = politica.get("services", {}).get(nombre)
        if not srv:
            raise ValueError(f"servicio desconocido: {nombre}")
        if nombre in no_exc:
            raise ValueError(f"{nombre} no se puede abrir por excepción")
        flujos.append(_base(dpid, int(tabla), PRIO_EXCEPCION, [
            {"type": "VLAN_VID", "vlanId": int(tabla)},
            {"type": "ETH_TYPE", "ethType": "0x0800"},
            {"type": "IP_PROTO", "protocol": 6},
            {"type": "IPV4_DST", "ip": f"{srv['ip']}/32"},
            {"type": "TCP_DST", "tcpPort": int(srv["tcp"])},
            {"type": "ETH_SRC", "mac": str(mac).lower()},
        ], _salida(politica)))
    return flujos


def vista_previa(politica: dict, usuario: str, rol: str, mac: str, dpids: Iterable[str], ahora: dt.datetime) -> Dict[str, List[dict]]:
    """{dpid: flujos} que se instalarían para esa sesión (lo que el panel enseña antes de confirmar)."""
    servicios = excepciones_de(politica, usuario, rol, ahora)
    if not servicios:
        return {}
    tabla = int(politica["roles"][rol]["tabla"])
    return {d: flujos_excepcion(politica, d, tabla, mac, servicios) for d in dpids}
