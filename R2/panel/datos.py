"""datos.py: lógica PURA del panel de administración (sin red; el historial usa SQLite de la biblioteca estándar). Diseño: docs/DISENO_PANEL_ADMIN.md.

Todo lo que el panel muestra sale de aquí: normaliza lo que devuelven el Policy Mapper, ONOS y el vigilante, compara lo que ve cada nodo de ONOS, filtra los eventos y compone el resumen con
sus avisos. No consulta nada por su cuenta: `panel.py` le pasa los datos ya leídos (así se prueba sin red)."""
import re
import sqlite3
import threading
from typing import Dict, Iterable, List, Optional

TIPOS_EVENTO = ("admitido", "rechazado", "conflicto", "logout", "revocado")
VIGILANTE_BIEN = "sin cambios"
VIGILANTE_EDAD_MAX_S = 30
_MAC = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


# ---------- sesiones ----------
def normalizar_sesiones(resp: Optional[dict], ahora: float) -> List[dict]:
    """De `GET /sessions` del mapper a una lista ordenada y plana. `expira_en_s` es None si la sesión no vence."""
    ses = (resp or {}).get("sessions") or {}
    salida = []
    for mac, s in ses.items():
        exp = s.get("expira")
        salida.append({
            "mac": str(mac).lower().replace("-", ":"), "usuario": s.get("username"), "rol": s.get("rol"), "vlan": s.get("vlan"), "dpid": s.get("dpid"), "puerto": s.get("port"),
            "metodo": s.get("metodo"), "expira_en_s": round(exp - ahora) if isinstance(exp, (int, float)) else None, "base": s.get("base"), "flujo": s.get("flow_id"),
        })
    salida.sort(key=lambda x: (str(x["usuario"] or ""), x["mac"]))
    return salida


# ---------- quién puede entrar ----------
LOOPBACK = ("127.0.0.1", "::1")


def identificar_por_ip(ip: str, hosts: Optional[dict], sesiones: List[dict]) -> Optional[dict]:
    """La sesión del equipo que se conecta desde `ip`: IP -> MAC por `GET /hosts` de ONOS -> sesión del mapper con esa MAC. Si la IP no la tiene ningún host, o la tienen dos MAC distintas
    (ambigua: no se adivina), no hay identidad. Es tan fiable como la protección contra suplantación de IP de la red de datos; por eso R2 además abre el puerto solo a ese rol."""
    if not ip or not isinstance(hosts, dict):
        return None
    macs = {str(h.get("mac", "")).lower().replace("-", ":") for h in hosts.get("hosts", []) if ip in (h.get("ipAddresses") or [])}
    if len(macs) != 1:
        return None
    mac = next(iter(macs))
    return next((s for s in sesiones if s["mac"] == mac), None)


def decidir_acceso(ip: str, accion: str, hosts: Optional[dict], sesiones: List[dict], roles_admin: Iterable[str]):
    """(permitido, motivo). Solo el rol de administración (`roles_admin`, p. ej. «superadmin») entra, sin contraseña ni token: lo que cuenta es la sesión 802.1X del equipo.

    - `leer`: el rol de administración; y, como vía de EMERGENCIA, una conexión local (túnel SSH al server, `127.0.0.1`).
    - `editar`: SOLO el rol de administración. Un túnel local no basta: editar siempre deja rastro de quién fue.
    Una sesión vencida, un equipo sin sesión o una IP ambigua se rechazan."""
    roles = {r.strip().lower() for r in roles_admin if r and r.strip()}
    if ip in LOOPBACK:
        return (True, "conexión local (emergencia, solo lectura)") if accion == "leer" else (False, "editar exige el rol de administración; no basta la conexión local")
    s = identificar_por_ip(ip, hosts, sesiones)
    if s is None:
        return False, "el equipo no tiene una sesión identificable"
    if isinstance(s.get("expira_en_s"), (int, float)) and s["expira_en_s"] <= 0:
        return False, "la sesión del equipo está vencida"
    if (s.get("rol") or "").lower() not in roles:
        return False, f"el rol {s.get('rol')} no tiene permiso"
    return True, f"{s.get('usuario')} ({s.get('rol')})"


# ---------- eventos ----------
def _clave(ev: dict):
    return (round(float(ev.get("t", 0)), 3), ev.get("tipo"), (ev.get("mac") or "").lower(), ev.get("usuario") or "", ev.get("detalle") or "")


class Historial:
    """Bitácora de eventos propia del panel (SQLite). Guarda cada evento una sola vez aunque se lea muchas veces, así el panel conserva más que las últimas 200 del mapper y no pierde las que
    éste rota. Con `ruta=":memory:"` vive en memoria (pruebas)."""

    def __init__(self, ruta: str = ":memory:", maximo: int = 20000):
        self._bd = sqlite3.connect(ruta, check_same_thread=False)
        self._cerrojo = threading.Lock()
        self.maximo = maximo
        with self._cerrojo:
            self._bd.execute("CREATE TABLE IF NOT EXISTS eventos (t REAL, tipo TEXT, usuario TEXT, mac TEXT, dpid TEXT, puerto TEXT, detalle TEXT, rol TEXT, vlan INTEGER, "
                             "UNIQUE (t, tipo, mac, usuario, detalle))")
            self._bd.execute("CREATE INDEX IF NOT EXISTS eventos_t ON eventos (t)")
            self._bd.commit()

    def agregar(self, eventos: Iterable[dict]) -> int:
        """Guarda los eventos que no estaban. Devuelve cuántos eran nuevos."""
        nuevos = 0
        with self._cerrojo:
            for ev in eventos or []:
                if not isinstance(ev, dict) or "t" not in ev:
                    continue
                cur = self._bd.execute(
                    "INSERT OR IGNORE INTO eventos VALUES (?,?,?,?,?,?,?,?,?)",
                    (round(float(ev["t"]), 3), ev.get("tipo") or "", ev.get("usuario") or "", (ev.get("mac") or "").lower(), ev.get("dpid") or "", str(ev.get("port") or ""),
                     ev.get("detalle") or "", ev.get("rol") or "", ev.get("vlan")))
                nuevos += cur.rowcount
            if nuevos:
                self._bd.execute("DELETE FROM eventos WHERE t < (SELECT t FROM eventos ORDER BY t DESC LIMIT 1 OFFSET ?)", (self.maximo,))
            self._bd.commit()
        return nuevos

    def listar(self, tipo: Optional[str] = None, usuario: Optional[str] = None, mac: Optional[str] = None, texto: Optional[str] = None,
               desde: Optional[float] = None, hasta: Optional[float] = None, limite: int = 100) -> List[dict]:
        """Los más recientes primero, con filtros. `usuario` y `texto` buscan por subcadena sin distinguir mayúsculas; `mac` por prefijo o completa; todo se pasa como parámetro (nunca se
        compone SQL con lo que escribe la persona)."""
        donde, args = [], []
        if tipo:
            donde.append("tipo = ?")
            args.append(tipo)
        if usuario:
            donde.append("usuario LIKE ? ESCAPE '\\'")
            args.append("%" + _like(usuario) + "%")
        if mac:
            donde.append("mac LIKE ? ESCAPE '\\'")
            args.append(_like(mac.lower().replace("-", ":")) + "%")
        if texto:
            donde.append("(detalle LIKE ? ESCAPE '\\' OR usuario LIKE ? ESCAPE '\\' OR mac LIKE ? ESCAPE '\\')")
            args += ["%" + _like(texto) + "%"] * 3
        if desde is not None:
            donde.append("t >= ?")
            args.append(float(desde))
        if hasta is not None:
            donde.append("t <= ?")
            args.append(float(hasta))
        sql = "SELECT t, tipo, usuario, mac, dpid, puerto, detalle, rol, vlan FROM eventos" + (" WHERE " + " AND ".join(donde) if donde else "") + " ORDER BY t DESC LIMIT ?"
        args.append(max(1, min(1000, int(limite))))
        with self._cerrojo:
            filas = self._bd.execute(sql, args).fetchall()
        return [dict(zip(("t", "tipo", "usuario", "mac", "dpid", "puerto", "detalle", "rol", "vlan"), f)) for f in filas]

    def contar(self) -> int:
        with self._cerrojo:
            return self._bd.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]

    def cerrar(self):
        with self._cerrojo:
            self._bd.close()


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------- vistas de ONOS ----------
def vistas_onos(por_nodo: Dict[str, Optional[dict]]) -> dict:
    """Compara lo que dice cada nodo de ONOS de cada switch. `por_nodo[nodo]` es el JSON de `GET /devices` o None si ese nodo no contestó (no cuenta como «no lo ve»).

    Devuelve {"switches": [{id, por_nodo: {nodo: {disponible, rol}}, desigual}], "nodos": {nodo: {responde, ven, total}}, "desiguales": [ids]}."""
    ids = sorted({d.get("id") for datos in por_nodo.values() if datos for d in datos.get("devices", []) if d.get("id")})
    switches = []
    for i in ids:
        vista = {}
        for nodo, datos in por_nodo.items():
            if not datos:
                continue
            d = next((x for x in datos.get("devices", []) if x.get("id") == i), None)
            vista[nodo] = {"disponible": bool(d.get("available")) if d else None, "rol": d.get("role") if d else None}
        valores = {v["disponible"] for v in vista.values() if v["disponible"] is not None}
        switches.append({"id": i, "por_nodo": vista, "desigual": len(valores) > 1})
    nodos = {}
    for nodo, datos in por_nodo.items():
        devs = (datos or {}).get("devices", [])
        nodos[nodo] = {"responde": bool(datos), "ven": sum(1 for d in devs if d.get("available")), "total": len(devs)}
    return {"switches": switches, "nodos": nodos, "desiguales": [s["id"] for s in switches if s["desigual"]]}


# ---------- resumen ----------
def estado_vigilante(salud: Optional[dict], ahora: float) -> dict:
    """De `/salud` del vigilante: si está bien (`sin cambios` y ciclo reciente) o qué atender."""
    if not salud:
        return {"responde": False, "ok": False, "motivo": "no responde"}
    edad = round(ahora - salud["ultimo_ciclo"], 1) if isinstance(salud.get("ultimo_ciclo"), (int, float)) else None
    estado = salud.get("estado")
    motivo = None
    if estado != VIGILANTE_BIEN:
        motivo = f"estado «{estado}»"
    elif edad is None or edad > VIGILANTE_EDAD_MAX_S:
        motivo = f"último ciclo hace {edad} s" if edad is not None else "sin último ciclo"
    return {"responde": True, "ok": motivo is None, "motivo": motivo, "rol": salud.get("rol"), "estado": estado, "edad_ciclo_s": edad, "aplica": salud.get("aplica"), "activo": salud.get("activo")}


def avisos_de_topologia(topologia: Optional[dict]) -> List[dict]:
    """De la topología que publica el vigilante (`/estado`): red partida (alta) y un aviso por cada cable caído (media; la red sigue por el otro camino)."""
    if not topologia:
        return []
    out = []
    if topologia.get("conexa") is False:
        nombres = ", ".join(a.get("nombre", "?") for a in topologia.get("aislados", [])) or "algún switch"
        out.append({"gravedad": "alta", "codigo": "RED_PARTIDA", "texto": f"La red está partida: sin camino a la raíz ({nombres})"})
    for e in topologia.get("enlaces", []):
        if e.get("estado") == "caido":
            out.append({"gravedad": "media", "codigo": "CABLE_CAIDO", "texto": f"Cable caído: {e.get('a_nombre')}/{e.get('a_puerto')} ↔ {e.get('b_nombre')}/{e.get('b_puerto')}"})
    return out


def avisos(vistas: dict, vigilantes: Dict[str, dict], mapper_ok: Optional[bool], fuentes_caidas: Iterable[str] = (), topologia: Optional[dict] = None) -> List[dict]:
    """Lista de avisos activos (gravedad «alta» o «media»), de lo más grave a lo menos."""
    out = avisos_de_topologia(topologia)
    for i in vistas.get("desiguales", []):
        out.append({"gravedad": "alta", "codigo": "VISTA_DESIGUAL", "texto": f"Los nodos de ONOS no coinciden sobre si {i} está disponible"})
    for nodo, n in vistas.get("nodos", {}).items():
        if not n["responde"]:
            out.append({"gravedad": "alta", "codigo": "NODO_ONOS_SIN_RESPUESTA", "texto": f"El nodo de ONOS {nodo} no responde"})
        elif n["total"] and n["ven"] == 0:
            out.append({"gravedad": "alta", "codigo": "NODO_SIN_SWITCHES", "texto": f"El nodo {nodo} no ve ningún switch disponible ({n['total']} conocidos)"})
    activos = [v for v in vigilantes.values() if v.get("responde") and v.get("activo")]
    if vigilantes and not activos:
        out.append({"gravedad": "alta", "codigo": "VIGILANTE_SIN_ACTIVO", "texto": "Ningún vigilante está activo"})
    for nombre, v in vigilantes.items():
        if not v.get("responde"):
            out.append({"gravedad": "media", "codigo": "VIGILANTE_SIN_RESPUESTA", "texto": f"El vigilante {nombre} no responde"})
        elif not v.get("ok") and v.get("activo"):
            out.append({"gravedad": "alta", "codigo": "VIGILANTE_ATENCION", "texto": f"El vigilante {nombre}: {v.get('motivo')}"})
    if mapper_ok is False:
        out.append({"gravedad": "alta", "codigo": "MAPPER_NO_LISTO", "texto": "El Policy Mapper no está listo (/health/ha)"})
    for f in fuentes_caidas:
        out.append({"gravedad": "media", "codigo": "FUENTE_CAIDA", "texto": f"No se pudo leer: {f}"})
    out.sort(key=lambda a: 0 if a["gravedad"] == "alta" else 1)
    return out


def sesiones_resumen(sesiones: List[dict]) -> dict:
    por_rol: Dict[str, int] = {}
    for s in sesiones:
        por_rol[s["rol"] or "?"] = por_rol.get(s["rol"] or "?", 0) + 1
    return {"total": len(sesiones), "por_rol": por_rol}


def resumen(vistas: dict, vigilantes: Dict[str, dict], sesiones: List[dict], mapper_ok: Optional[bool], fuentes_caidas: Iterable[str], ahora: float, topologia: Optional[dict] = None) -> dict:
    ids = vistas.get("switches", [])
    disponibles = sum(1 for s in ids if any(v["disponible"] for v in s["por_nodo"].values()))
    return {"t": ahora, "switches": {"total": len(ids), "disponibles": disponibles}, "nodos_onos": vistas.get("nodos", {}), "vigilantes": vigilantes,
            "sesiones": sesiones_resumen(sesiones), "mapper_ok": mapper_ok, "avisos": avisos(vistas, vigilantes, mapper_ok, fuentes_caidas, topologia)}


def elegir_vigilante(saludes: Dict[str, Optional[dict]]) -> Optional[str]:
    """Qué vigilante enseña el panel: el que está activo; si ninguno lo está, el primero que responda (el principal va primero en la configuración)."""
    vivos = [n for n, s in saludes.items() if s]
    return next((n for n in vivos if saludes[n].get("activo")), vivos[0] if vivos else None)
