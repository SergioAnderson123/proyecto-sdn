"""policy_mapper.py

Controlador de Políticas Zero Trust (PDP) y Enforzador SDN para ONOS.
Implementa validaciones criptográficas, anti-spoofing y programación de flujos.
"""

import asyncio
import os
import time
from typing import List, Optional
from fastapi import FastAPI, Header, HTTPException
import httpx
from pydantic import BaseModel, Field
import uvicorn
from fastapi.responses import JSONResponse

import eventos
import policy_tables
import portal_auth
from session_manager import ActiveSession, session_store

app = FastAPI(title="SDN Zero Trust Policy Mapper")

# URLs y Credenciales del Controlador ONOS
ONOS_HOST = os.getenv("ONOS_HOST", "onos_controller")
ONOS_PORT = os.getenv("ONOS_PORT", "8181")
ONOS_BASE_URL = f"http://{ONOS_HOST}:{ONOS_PORT}/onos/v1"
ONOS_AAA_URL = f"http://{ONOS_HOST}:{ONOS_PORT}/onos/aaa/app/users"
ONOS_AUTH = ("onos", "rocks")
R2_MAB_URL = os.getenv("R2_MAB_URL", "http://r2-mab-vm:5001").rstrip("/")

# -----------------------------------------------------------------------------
# R2: TABLAS DE ROL (qué recursos alcanza cada rol) a partir de policy.yaml
# -----------------------------------------------------------------------------
POLITICA = policy_tables.cargar_politica()


async def _sincronizar_tablas(forzar: bool = False) -> dict:
    """Instala las tablas de rol donde faltan; con `forzar` las reenvía a todos los switches."""
    dpids = await policy_tables.dispositivos(ONOS_BASE_URL, ONOS_AUTH)
    if not forzar:
        dpids = await policy_tables.faltantes(POLITICA, dpids, ONOS_BASE_URL, ONOS_AUTH)
    return await policy_tables.sincronizar(POLITICA, dpids, ONOS_BASE_URL, ONOS_AUTH) if dpids else {}


async def _sincronizar_periodico() -> None:
    """Los switches pueden conectarse después de arrancar el mapper: se reintenta cada 15 s (es idempotente)."""
    while True:
        try:
            res = await _sincronizar_tablas()
            if res:
                print(f"  [R2 TABLAS] sincronizado: {res}")
        except Exception as e:  # el lazo no debe morir por un error puntual
            print(f"  [R2 TABLAS ERROR] {e}")
        await asyncio.sleep(15)


async def _expirar_sesiones_portal() -> None:
    """Cierra las sesiones del portal cuyo plazo venció: borra su flujo 40001 y deja constancia en la bitácora."""
    while True:
        await asyncio.sleep(10)
        try:
            for s in session_store.vencidas(time.time()):
                session_store.remove_session(s.mac)
                await eliminar_flujo_onos(s.dpid, s.flow_id)
                print(f"\n[PORTAL] Sesión de {s.username} ({s.mac}) vencida; flujo eliminado")
                eventos.registrar("logout", s.username, s.mac, s.dpid, s.port, "Portal: sesión vencida por tiempo", s.rol)
        except Exception as e:  # el lazo no debe morir por un error puntual
            print(f"  [PORTAL EXPIRACION ERROR] {e}")


@app.on_event("startup")
async def _iniciar_sincronizacion() -> None:
    # las sesiones están en memoria: los flujos de sesión que quedaron en ONOS ya no tienen dueño
    n = await policy_tables.purgar_sesiones_huerfanas(ONOS_BASE_URL, ONOS_AUTH)
    if n:
        print(f"  [R2 SESIONES] {n} flujo(s) de sesión huérfanos eliminados al arrancar; los usuarios deben autenticar de nuevo")
    asyncio.create_task(_sincronizar_periodico())
    asyncio.create_task(_expirar_sesiones_portal())


class Revocacion(BaseModel):
    mac: str
    motivo: str = "R5"
    segundos: int = 300


@app.post("/sessions/revoke")
async def revocar_sesion(r: Revocacion):
    """I11 (R5): revoca la sesión de una MAC que el IDS bloqueó.

    Borra el flujo de sesión 40001 y deja la MAC en la lista negra temporal (45000) de R1 durante `segundos`, de modo que
    el usuario no recupere acceso con la misma credencial sin volver a pasar por R1 cuando termine el bloqueo."""
    mac = r.mac.lower().replace("-", ":")
    sesion = session_store.remove_session(mac)
    if not sesion:
        return {"status": "sin_sesion"}
    await eliminar_flujo_onos(sesion.dpid, sesion.flow_id)
    await inyectar_blacklist_temporal(sesion.dpid, sesion.port, sesion.mac, idle_timeout=r.segundos)
    print(f"\n[R5 -> R2] Sesión de {sesion.username} ({mac}) revocada por {r.motivo}; lista negra {r.segundos} s")
    eventos.registrar("revocado", sesion.username, mac, sesion.dpid, sesion.port,
                      f"Revocada por {r.motivo}; lista negra {r.segundos} s", sesion.rol)
    return {"status": "revocada", "usuario": sesion.username}


class PortalLogin(BaseModel):
    usuario: str
    clave: str
    mac: str


PORTAL_TOKEN = os.getenv("PORTAL_TOKEN", "")


def _verificar_portal(token: str) -> None:
    if PORTAL_TOKEN and token != PORTAL_TOKEN:
        raise HTTPException(status_code=403, detail="Portal no autorizado")


@app.post("/portal/login")
async def portal_login(p: PortalLogin, x_portal_token: str = Header(default="")):
    """Portal cautivo: valida usuario y contraseña contra LDAP e instala la sesión (mismo flujo 40001 que 802.1X).

    La MAC la aporta el portal (la lee de su tabla ARP, no del cliente), por eso el endpoint exige `X-Portal-Token` si PORTAL_TOKEN está definido."""
    _verificar_portal(x_portal_token)
    mac = p.mac.lower().replace("-", ":")
    grupos = await asyncio.to_thread(portal_auth.autenticar_ldap, p.usuario, p.clave)
    if grupos is None:
        eventos.registrar("rechazado", p.usuario, mac, detalle="Portal: usuario o contraseña incorrectos")
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    rol = portal_auth.rol_de_grupos(POLITICA, grupos)
    info = POLITICA["roles"].get(rol) if rol else None
    if not info:
        eventos.registrar("rechazado", p.usuario, mac, detalle="Portal: grupo LDAP sin rol de acceso")
        raise HTTPException(status_code=403, detail="Su grupo no tiene acceso")
    if session_store.get_by_mac(mac) or session_store.get_by_username(p.usuario):
        eventos.registrar("conflicto", p.usuario, mac, detalle="Portal: usuario o MAC con sesión activa", rol=rol)
        raise HTTPException(status_code=409, detail="Ya existe una sesión activa para este usuario o equipo")
    ubic = await portal_auth.ubicar_host(mac, ONOS_BASE_URL, ONOS_AUTH)
    if not ubic:
        eventos.registrar("rechazado", p.usuario, mac, detalle="Portal: ONOS no conoce al equipo", rol=rol)
        raise HTTPException(status_code=409, detail="No se pudo ubicar el equipo en la red; reintente")
    dpid, port = ubic
    vlan = int(info["tabla"])
    if await policy_tables.faltantes(POLITICA, [dpid], ONOS_BASE_URL, ONOS_AUTH):
        await policy_tables.sincronizar(POLITICA, [dpid], ONOS_BASE_URL, ONOS_AUTH)
    flow_id = await inyectar_flujo_tabla_0(dpid, port, mac, vlan)
    if not flow_id:
        raise HTTPException(status_code=502, detail="No se pudo instalar la sesión en el switch")
    nombre = rol.capitalize()
    session_store.register_session(ActiveSession(username=p.usuario, mac=mac, dpid=dpid, port=port, vlan=vlan,
                                                 rol=nombre, flow_id=flow_id, metodo="Portal",
                                                 expira=time.time() + policy_tables.duracion_sesion_portal(POLITICA, rol)))
    print(f"  [V] PORTAL: {p.usuario} ({rol}) en {dpid}:{port} -> tabla {vlan}")
    eventos.registrar("admitido", p.usuario, mac, dpid, port, f"Portal cautivo: tabla {vlan}", nombre, vlan)
    return {"status": "ok", "usuario": p.usuario, "rol": nombre, "tabla": vlan}


class PortalLogout(BaseModel):
    mac: str


@app.post("/portal/logout")
async def portal_logout(p: PortalLogout, x_portal_token: str = Header(default="")):
    """Cierra una sesión creada por el portal (no toca las de 802.1X)."""
    _verificar_portal(x_portal_token)
    mac = p.mac.lower().replace("-", ":")
    ses = session_store.get_by_mac(mac)
    if not ses or ses.metodo != "Portal":
        return {"status": "sin_sesion"}
    session_store.remove_session(mac)
    await eliminar_flujo_onos(ses.dpid, ses.flow_id)
    eventos.registrar("logout", ses.username, mac, ses.dpid, ses.port, "Portal: sesión cerrada por el usuario", ses.rol)
    return {"status": "cerrada"}


@app.get("/portal/estado")
async def portal_estado(mac: str):
    """Sesión (si hay) de una MAC; el portal lo usa para mostrar «ya inició sesión»."""
    ses = session_store.get_by_mac(mac.lower().replace("-", ":"))
    return {"sesion": None if not ses else {"usuario": ses.username, "rol": ses.rol, "metodo": ses.metodo}}


@app.post("/policy/sync")
async def sincronizar_ahora():
    """Vuelve a instalar las tablas de rol (tras editar policy.yaml: reiniciar el mapper para recargarlo)."""
    return {"sincronizado": await _sincronizar_tablas(forzar=True)}


class RadiusAttribute(BaseModel):
    type: str
    value: List[str]


class RadiusPayload(BaseModel):
    usuario_tls: Optional[RadiusAttribute] = Field(
        alias="TLS-Client-Cert-Common-Name", default=None
    )
    usuario_externo: RadiusAttribute = Field(alias="User-Name")
    nas_ip: Optional[RadiusAttribute] = Field(
        alias="NAS-IP-Address", default=None
    )
    mac_fisica: Optional[RadiusAttribute] = Field(
        alias="Calling-Station-Id", default=None
    )
    switch_port: Optional[RadiusAttribute] = Field(
        alias="NAS-Port-Id", default=None
    )

@app.get("/events")
async def ver_eventos(limit: int = 50):
    """Bitácora de accesos (admitidos, rechazados, conflictos, logout y revocaciones), la más reciente primero."""
    return {"events": eventos.listar(limit)}


@app.get("/sessions")
async def ver_sesiones():
    """Devuelve todas las sesiones activas en el SessionManager."""
    return {
        "total_sessions": len(session_store._sessions_by_mac),
        "sessions": {
            mac: {
                "username": ses.username,
                "dpid": ses.dpid,
                "port": ses.port,
                "vlan": ses.vlan,
                "rol": ses.rol,
                "flow_id": ses.flow_id,
                "metodo": ses.metodo,
                "expira": ses.expira,
            }
            for mac, ses in session_store._sessions_by_mac.items()
        },
    }
# -----------------------------------------------------------------------------
# INTEGRACIÓN SADIS (Para ONOS AAA)
# -----------------------------------------------------------------------------
@app.get("/sadis/{port_id:path}")
async def sadis_dinamico(port_id: str):
    """Resuelve el DPID y el Puerto físico a partir del formato enviado por ONOS."""
    try:
        if port_id.startswith("s") and "-eth" in port_id:
            # Ejemplo: "s1-eth2" -> switch="1", port="2" -> "of:0000000000000001/2"
            partes = port_id.split("-eth")
            num_switch = partes[0].replace("s", "")
            num_port = partes[1]
            dpid_port = f"of:{num_switch.zfill(16)}/{num_port}"
        elif "/" in port_id:
            # Si ONOS ya envía "of:0000000000000001/2", lo mantenemos intacto
            dpid_port = port_id
        else:
            # Fallback si solo llega "s1" sin puerto
            num_switch = port_id.replace("s", "")
            dpid_port = f"of:{num_switch.zfill(16)}/1"

        print(f"[SADIS] Puerto consultado: {port_id} -> nasPortId: {dpid_port}")
        return {"id": port_id, "nasPortId": dpid_port, "uniTagList": []}
    except Exception:
        return {"id": port_id, "nasPortId": "of:0000000000000099/1"}


# -----------------------------------------------------------------------------
# CLIENTE ONOS REST API
# -----------------------------------------------------------------------------
# Con varios logins a la vez, cada webhook volvía a pedirle a ONOS la tabla COMPLETA de /onos/aaa/app/users por su
# cuenta (medido en el laboratorio: a partir de 5 negociaciones simultáneas ya hay varias pidiéndola dentro de la
# misma ventana de milisegundos). Se cachea esa tabla cruda un instante muy corto -AAA_CACHE_TTL_S, por defecto
# afinado en pruebas de carga- y se comparte entre todas las llamadas que caigan dentro de esa ventana: solo UNA
# de ellas hace la petición HTTP real (las demás esperan ese resultado en vez de abrir su propia conexión). El
# filtrado por usuario sigue siendo por llamada (la caché solo evita repetir la petición a ONOS, no cambia a quién
# se admite). Nunca sirve nada más viejo que el TTL: con 50 ms de margen -muy por debajo de los ~2.3 s que tarda
# un EAP-TLS completo- no hay riesgo real de resolver con un estado atrasado.
AAA_CACHE_TTL_S = float(os.getenv("AAA_CACHE_TTL_S", "0.05"))

_cache_aaa_tabla: Optional[list] = None
_cache_aaa_hasta: float = 0.0
_cache_aaa_candado = asyncio.Lock()
_cache_aaa_peticiones_reales = 0   # cuántas veces se pidió la tabla de verdad a ONOS (lo usan las pruebas/mediciones)


async def _pedir_tabla_aaa() -> list:
  """La tabla cruda de /onos/aaa/app/users, sin cachear ni filtrar."""
  global _cache_aaa_peticiones_reales
  _cache_aaa_peticiones_reales += 1
  async with httpx.AsyncClient() as client:
    try:
      response = await client.get(ONOS_AAA_URL, auth=ONOS_AUTH, timeout=2.0)
      if response.status_code == 200:
        datos = response.json()
        sesiones_aaa = (
            datos.get("users", datos.get("entries", []))
            if isinstance(datos, dict)
            else datos
        )
        return sesiones_aaa if isinstance(sesiones_aaa, list) else []
    except httpx.RequestError as e:
      print(f"[ONOS API] Error de conexión: {e}")
  return []


async def _tabla_aaa() -> list:
  """La tabla de /onos/aaa/app/users, cacheada por AAA_CACHE_TTL_S segundos (ver comentario arriba)."""
  global _cache_aaa_tabla, _cache_aaa_hasta
  ahora = time.monotonic()
  if _cache_aaa_tabla is not None and ahora < _cache_aaa_hasta:
    return _cache_aaa_tabla
  async with _cache_aaa_candado:
    ahora = time.monotonic()
    if _cache_aaa_tabla is not None and ahora < _cache_aaa_hasta:
      return _cache_aaa_tabla   # alguien más ya la refrescó mientras esperábamos el candado
    _cache_aaa_tabla = await _pedir_tabla_aaa()
    _cache_aaa_hasta = time.monotonic() + AAA_CACHE_TTL_S
    return _cache_aaa_tabla


async def candidatos_onos(username_externo: str) -> List[dict]:
  """Todas las ubicaciones (dpid, puerto) donde ONOS AAA tiene a `username_externo` negociando ahora mismo.

  FreeRADIUS no recibe Calling-Station-Id ni NAS-Port-Id en el Access-Request que arma el AAA de ONOS (no son
  configurables en esta versión), así que el webhook no sabe por sí solo desde qué puerto físico llama cada vez
  que se invoca; tiene que volver a preguntarle a ONOS (o leer la tabla cacheada en `_tabla_aaa`). ONOS sí
  distingue los puertos internamente (un subscriberId "dpid/puerto" por negociación), así que si aquí aparece más
  de una ubicación para el MISMO usuario, hay dos negociaciones físicas a la vez con la misma identidad (nadie
  legítimo se conecta por dos puertos a la vez): es quien llama (`auth_webhook`) el que decide, con la ubicación
  fija de LDAP, cuál de las dos es la real, en vez de que esta función adivine."""
  sesiones_aaa = await _tabla_aaa()
  por_ubicacion: dict = {}   # (dpid, puerto) -> mejor candidato visto en esa ubicación

  for sesion in reversed(sesiones_aaa):
    if isinstance(sesion, dict) and sesion.get("username") == username_externo:
      # La ubicación real la da `connectPoint` ("of:<dpid>/<puerto>"): es donde ONOS recibió el EAPOL. El
      # `subscriberId` es lo que respondió SADIS, y SADIS solo recibe el NOMBRE del puerto ("ens5", "s1-eth2"), que en
      # una red con switches reales se repite entre switches; por eso solo se usa si ONOS no entrega `connectPoint`.
      punto = str(sesion.get("connectPoint", "") or "")
      subscriber_id = punto if "/" in punto else sesion.get("subscriberId", "")
      port = subscriber_id.split("/")[-1] if "/" in subscriber_id else "1"
      dpid = subscriber_id.rsplit("/", 1)[0] if "/" in subscriber_id else "of:0000000000000001"
      mac_onos = sesion.get("macAddress", "").strip().lower()
      estado = str(sesion.get("state", sesion.get("authState", ""))).upper()

      info = {
          "dpid": dpid,
          "port": str(port),
          "mac_onos": mac_onos,
          "username_onos": sesion.get("username", ""),
          "state": estado,
      }

      clave = (dpid, str(port))
      previo = por_ubicacion.get(clave)
      # por ubicación nos quedamos con la negociación activa (PENDING/START); si no hay, la más reciente
      if previo is None or "PENDING" in estado or "START" in estado:
        if previo is None or not ("PENDING" in previo["state"] or "START" in previo["state"]):
          por_ubicacion[clave] = info

  return list(por_ubicacion.values())


_candados_por_usuario: dict = {}   # identidad_onos -> asyncio.Lock


def _candado_de(identidad_onos: str) -> asyncio.Lock:
  """Un candado por identidad (no global): dos negociaciones del MISMO usuario se resuelven una tras otra (así se
  puede comparar sus ubicaciones sin que una pise a la otra), pero usuarios distintos se siguen autenticando en
  paralelo sin esperarse."""
  candado = _candados_por_usuario.get(identidad_onos)
  if candado is None:
    candado = asyncio.Lock()
    _candados_por_usuario[identidad_onos] = candado
  return candado


P_EAPOL_PUERTO = 50000   # sobre la sesion (40001), la lista negra (45000) y la regla propia de ONOS AAA (40000)


async def inyectar_eapol_en_puerto(client, dpid: str, port: str) -> None:
    """Todo el EAPOL de un puerto con sesion sube al controlador (Prio 50000, mapa de R5/deploy/prioridades.yaml).

    Sin esta regla, el flujo de sesion (40001: in_port + eth_src) captura TAMBIEN las tramas EAPOL del host ya autenticado y
    las manda a la tabla de rol, que las descarta: el EAPOL-Logoff y el EAPOL-Start de una reautenticacion nunca llegan a ONOS
    AAA (que instala su regla a 40000). El host espera 60 s y falla, y la sesion no se limpia al desconectarse."""
    payload = {
        "priority": P_EAPOL_PUERTO,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": dpid,
        "tableId": 0,
        "treatment": {"instructions": [{"type": "OUTPUT", "port": "CONTROLLER"}]},
        "selector": {"criteria": [{"type": "IN_PORT", "port": int(port)}, {"type": "ETH_TYPE", "ethType": "0x888e"}]},
    }
    try:
        resp = await client.post(f"{ONOS_BASE_URL}/flows/{dpid}", json=payload, auth=ONOS_AUTH, timeout=2.0)
        if resp.status_code in [200, 201]:
            print(f"  [TCAM] Regla EAPOL Prio {P_EAPOL_PUERTO} en {dpid}:{port} (el EAPOL del host llega siempre a ONOS)")
        else:
            print(f"  [TCAM ERROR] Regla EAPOL: ONOS respondio {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"  [TCAM ERROR] Regla EAPOL: {e}")


async def inyectar_flujo_tabla_0(
    dpid: str, port: str, mac: str, vlan_id: int
) -> Optional[str]:
    """Inyecta la regla Prio 40001 en Tabla 0:

    Match: in_port + eth_src -> push_vlan -> goto_table de rol (R1 + R2).
    """
    flow_payload = {
        "priority": 40001,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": dpid,
        "tableId": 0,
        "treatment": {
            "instructions": [
                {
                    "type": "L2MODIFICATION",
                    "subtype": "VLAN_PUSH",
                    "ethernetType": "0x8100",
                },
                {
                    "type": "L2MODIFICATION",
                    "subtype": "VLAN_ID",
                    "vlanId": vlan_id,
                },
                {"type": "TABLE", "tableId": vlan_id},  # Salto a Tabla de Rol
            ]
        },
        "selector": {
            "criteria": [
                {"type": "IN_PORT", "port": int(port)},
                {"type": "ETH_SRC", "mac": mac.lower()},
            ]
        },
    }

    url = f"{ONOS_BASE_URL}/flows/{dpid}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url, json=flow_payload, auth=ONOS_AUTH, timeout=2.0
            )
            if resp.status_code in [200, 201]:
                location = resp.headers.get("Location", "")
                flow_id = location.split("/")[-1] if location else "active_flow"
                print(
                    f"  [TCAM] Flujo Prio 40001 inyectado exitosamente en {dpid}. Flow ID: {flow_id}"
                )
                await inyectar_eapol_en_puerto(client, dpid, port)
                return flow_id
            print(f"  [TCAM ERROR] ONOS respondió {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"  [TCAM ERROR] Error inyectando flujo: {e}")
    return None


async def inyectar_blacklist_temporal(
    dpid: str, port: str, mac: str, idle_timeout: int = 300
) -> None:
    """Inyecta regla de descarte en hardware ante ataques o intrusiones (Prio 45000)."""
    flow_payload = {
        "priority": 45000,
        "timeout": idle_timeout,
        "isPermanent": False,
        "deviceId": dpid,
        "tableId": 0,
        "treatment": {"instructions": [{"type": "NOACTION"}]},  # DROP
        "selector": {
            "criteria": [
                {"type": "IN_PORT", "port": int(port)},
                {"type": "ETH_SRC", "mac": mac.lower()},
            ]
        },
    }
    url = f"{ONOS_BASE_URL}/flows/{dpid}"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                url, json=flow_payload, auth=ONOS_AUTH, timeout=2.0
            )
            print(
                f"  [BLACKLIST] Regla DROP Prio 45000 aplicada a MAC {mac} en {dpid}:{port} por {idle_timeout}s"
            )
        except Exception as e:
            print(f"  [BLACKLIST ERROR] No se pudo inyectar lista negra: {e}")


async def eliminar_flujo_onos(dpid: str, flow_id: str) -> None:
    """Elimina una regla de flujo específica en ONOS."""
    if not flow_id or flow_id == "active_flow":
        return
    url = f"{ONOS_BASE_URL}/flows/{dpid}/{flow_id}"
    async with httpx.AsyncClient() as client:
        try:
            await client.delete(url, auth=ONOS_AUTH, timeout=2.0)
            print(f"  [TCAM] Flujo {flow_id} eliminado de {dpid}")
        except Exception as e:
            print(f"  [TCAM ERROR] Fallo al eliminar flujo {flow_id}: {e}")


# -----------------------------------------------------------------------------
# WEBHOOK PRINCIPAL (EAP-TLS - R1 y R2)
# -----------------------------------------------------------------------------
@app.post("/webhook")
async def auth_webhook(
    payload: RadiusPayload,
    x_rol_ldap: str = Header(default="Sin Rol"),
    x_mac_ldap: str = Header(default=""),
    x_dpid_ldap: str = Header(default=""),  # DPID estático autorizado
    x_port_ldap: str = Header(default=""),  # Puerto estático autorizado
):
    identidad_ldap = payload.usuario_tls.value[0]
    identidad_onos = payload.usuario_externo.value[0]
    mac_esperada = x_mac_ldap.lower().strip()

    print(
        f"\n[>>>] EVALUANDO POLÍTICA ZERO TRUST: {identidad_ldap} ({x_rol_ldap})"
    )

    # Candado por usuario (no global): si el MISMO usuario negocia por dos puertos a la vez, se resuelve una
    # llamada completa antes de empezar la otra, para poder comparar sus ubicaciones sin pisarse; otros usuarios
    # siguen autenticando en paralelo sin esperar por este candado.
    async with _candado_de(identidad_onos):
        # 1. Obtener TODAS las ubicaciones donde ONOS ve a esta identidad negociando ahora mismo
        candidatos = await candidatos_onos(identidad_onos)
        if not candidatos:
            eventos.registrar("rechazado", identidad_ldap, mac_esperada, detalle="Sesión EAP no encontrada en ONOS AAA", rol=x_rol_ldap)
            raise HTTPException(
                status_code=403, detail="Sesion EAP no encontrada en ONOS AAA"
            )

        datos_onos = candidatos[0]
        if len(candidatos) > 1:
            # La misma identidad negocia por más de un puerto a la vez: con MAC clonada no hay forma de saber,
            # solo por el paquete, cuál de las dos llamadas es la física legítima. Se usa la ubicación fija de
            # LDAP como referencia (no depende de qué llamada se está procesando) para decidir cuál ganar y
            # rechazar cada una de las demás por su cuenta, con su propio motivo, en vez de un error compartido.
            ganador = next((c for c in candidatos if x_dpid_ldap and x_port_ldap
                            and c["dpid"] == x_dpid_ldap and c["port"] == str(x_port_ldap)), None)
            if not ganador:
                # Sin ubicación fija en LDAP para este usuario no hay con qué decidir: no se admite a nadie.
                ubicaciones = ", ".join(f"{u['dpid']}:{u['port']}" for u in candidatos)
                print(f"  [ALERTA DE SEGURIDAD] {identidad_ldap} negociando EAP en más de un puerto a la vez, sin ubicación fija en LDAP para decidir: {ubicaciones}")
                eventos.registrar("conflicto", identidad_ldap, mac_esperada,
                                  detalle=f"Misma identidad en varios puertos a la vez ({ubicaciones}); sin ubicación fija en LDAP para resolverlo", rol=x_rol_ldap)
                raise HTTPException(
                    status_code=409,
                    detail="Conflicto de seguridad: la misma identidad está negociando en más de un puerto a la vez",
                )
            sesion_del_ganador = session_store.get_by_username(identidad_ldap)
            ya_resuelto = (sesion_del_ganador is not None and sesion_del_ganador.dpid == ganador["dpid"]
                          and str(sesion_del_ganador.port) == ganador["port"])
            if not ya_resuelto:
                # primera vez que se ve esta ambigüedad: se rechaza cada ubicación que no ganó, por su cuenta
                for otro in candidatos:
                    if otro is ganador:
                        continue
                    print(f"  [ALERTA DE SEGURIDAD] {identidad_ldap} también negociando en {otro['dpid']}:{otro['port']} "
                          f"(se esperaba {x_dpid_ldap}:{x_port_ldap}); posible clonación")
                    await inyectar_blacklist_temporal(otro["dpid"], otro["port"], otro["mac_onos"], idle_timeout=600)
                    eventos.registrar("conflicto", identidad_ldap, otro["mac_onos"], otro["dpid"], otro["port"],
                                      f"Ubicación no autorizada ({otro['dpid']}:{otro['port']}); se esperaba {x_dpid_ldap}:{x_port_ldap}. Posible clonación; lista negra 600 s",
                                      x_rol_ldap)
            # si ya_resuelto, esta llamada es una segunda confirmación de la misma negociación ganadora: nada
            # que rechazar otra vez; _procesar_webhook la reconoce como la sesión ya activa y la confirma.
            datos_onos = ganador

        mac_fisica = datos_onos["mac_onos"]
        dpid = datos_onos["dpid"]
        port = datos_onos["port"]
        return await _procesar_webhook(identidad_ldap, mac_esperada, mac_fisica, dpid, port, x_rol_ldap, x_dpid_ldap, x_port_ldap)


async def _procesar_webhook(identidad_ldap: str, mac_esperada: str, mac_fisica: str, dpid: str, port: str,
                            x_rol_ldap: str, x_dpid_ldap: str, x_port_ldap: str) -> dict:
    """El resto de la política (validación de MAC, sesión duplicada, ubicación y asignación de rol), ya con una
    única ubicación física resuelta."""

    # -------------------------------------------------------------------------
    # VALIDACIÓN 1: Coincidencia de Hardware en LDAP (1. vs 1.2 Usuario Externo)
    # -------------------------------------------------------------------------
    if mac_esperada and mac_esperada != mac_fisica:
        print(
            f"  [X] RECHAZO: Usuario Externo. MAC registrada ({mac_esperada}) != MAC física ({mac_fisica})"
        )
        await inyectar_blacklist_temporal(
            dpid, port, mac_fisica, idle_timeout=300
        )
        eventos.registrar("rechazado", identidad_ldap, mac_fisica, dpid, port,
                          f"MAC registrada {mac_esperada} distinta de la física; lista negra 300 s", x_rol_ldap)
        raise HTTPException(
            status_code=403, detail="Device Binding Failed: MAC no coincide"
        )
    # -------------------------------------------------------------------------
    # BIFURCACIÓN: ¿Ya existe una sesión activa para esta MAC física?
    # -------------------------------------------------------------------------
    sesion_activa = session_store.get_by_username(
        identidad_ldap
    ) or session_store.get_by_mac(mac_fisica)

    if sesion_activa:
        if (sesion_activa.dpid == dpid and str(sesion_activa.port) == str(port) and sesion_activa.mac == mac_fisica
                and sesion_activa.username.lower() == identidad_ldap.lower()):
            # Es la MISMA sesión que ya está instalada (reintento o segunda llamada de la misma negociación que
            # ya ganó la resolución de ambigüedad), no un conflicto nuevo: se confirma sin volver a bloquear nada.
            print(f"  [=] {identidad_ldap} ya tiene esta misma sesión activa en {dpid}:{port}; se confirma sin reprocesar")
            return {
                "Tunnel-Type": "VLAN",
                "Tunnel-Medium-Type": "IEEE-802",
                "Tunnel-Private-Group-Id": str(sesion_activa.vlan),
                "Reply-Message": "Acceso Zero Trust Autorizado",
            }
        # CASO A: Ya existe una sesión activa EN OTRA UBICACIÓN -> Intento de clonación / suplantación
        print(
            f"  [ALERTA DE SEGURIDAD] MAC Spoofing / Conflicto de sesión detectado para {identidad_ldap} ({mac_fisica})."
        )
        await inyectar_blacklist_temporal(dpid, port, mac_fisica, idle_timeout=600)
        eventos.registrar("conflicto", identidad_ldap, mac_fisica, dpid, port,
                          "Usuario o MAC con sesión activa (posible suplantación); lista negra 600 s", x_rol_ldap)
        raise HTTPException(
            status_code=409,
            detail="Conflicto de seguridad: El usuario o MAC ya posee una sesión activa",
        )
    else:
        # CASO B: No hay sesión previa -> Se valida la ubicación asignada en LDAP
        if x_dpid_ldap and x_port_ldap:
            if dpid != x_dpid_ldap or str(port) != str(x_port_ldap):
                print(
                    f"  [X] RECHAZO: Ubicación física no autorizada. Esperado {x_dpid_ldap}:{x_port_ldap} -> Real: {dpid}:{port}"
                )
                eventos.registrar("rechazado", identidad_ldap, mac_fisica, dpid, port,
                                  f"Ubicación no autorizada: esperado {x_dpid_ldap}:{x_port_ldap}", x_rol_ldap)
                raise HTTPException(
                    status_code=403,
                    detail="Acceso restringido: Puerto físico no asignado",
                )
    # -------------------------------------------------------------------------
    # ASIGNACIÓN DE POLÍTICA Y ROL (R1 -> R2)
    # -------------------------------------------------------------------------
    rol_lower = x_rol_ldap.lower()
    if "docente" in rol_lower:
        vlan_final = 10  # VLAN 10 y Tabla 10 (Acceso a Sistema de Notas)
        rol_nombre = "Docente"
    elif "alumno" in rol_lower:
        vlan_final = 20  # VLAN 20 y Tabla 20 (Bloqueo a Sistema de Notas)
        rol_nombre = "Alumno"
    elif "soporte" in rol_lower:
        vlan_final = 40  # VLAN 40 y Tabla 40 (Administrador: acceso total; la 30 queda para IoT/MAB)
        rol_nombre = "Administrador"
    else:
        print("  [X] RECHAZO: Rol institucional sin permisos")
        eventos.registrar("rechazado", identidad_ldap, mac_fisica, dpid, port, "Rol institucional sin permisos", x_rol_ldap)
        raise HTTPException(status_code=403, detail="Rol sin acceso autorizado")

    # R2: las tablas de rol deben existir antes de que el flujo de sesión mande tráfico a ellas
    if await policy_tables.faltantes(POLITICA, [dpid], ONOS_BASE_URL, ONOS_AUTH):
        await policy_tables.sincronizar(POLITICA, [dpid], ONOS_BASE_URL, ONOS_AUTH)

    # Inyección de regla en hardware en Tabla 0
    flow_id = await inyectar_flujo_tabla_0(dpid, port, mac_fisica, vlan_final)

    # Registro en el gestor de sesiones
    session_store.register_session(
        ActiveSession(
            username=identidad_ldap,
            mac=mac_fisica,
            dpid=dpid,
            port=port,
            vlan=vlan_final,
            rol=rol_nombre,
            flow_id=flow_id,
        )
    )

    print(
        f"  [V] APROBADO: {identidad_ldap} asignado a VLAN {vlan_final} (Tabla {vlan_final})"
    )
    eventos.registrar("admitido", identidad_ldap, mac_fisica, dpid, port, f"VLAN {vlan_final}, tabla {vlan_final}", rol_nombre, vlan_final)

    return {
        "Tunnel-Type": "VLAN",
        "Tunnel-Medium-Type": "IEEE-802",
        "Tunnel-Private-Group-Id": str(vlan_final),
        "Reply-Message": "Acceso Zero Trust Autorizado",
    }


# -----------------------------------------------------------------------------
# WEBHOOK DE DESCONEXIÓN / LOGOUT (Cascade Revocation)
# -----------------------------------------------------------------------------
@app.post("/webhook/stop")
async def logout_webhook(payload: RadiusPayload):
    """Maneja eventos de desconexión (Accounting-Stop) purgando flujos en OVS."""
    identidad_onos = payload.usuario_externo.value[0]

    # 1. Intentar buscar la MAC desde el paquete RADIUS (Calling-Station-Id)
    mac = None
    if payload.mac_fisica and payload.mac_fisica.value:
        # Normalizar formato si viene como 00-00-00-00-00-11 -> 00:00:00:00:00:11
        mac = payload.mac_fisica.value[0].replace("-", ":").lower()

    # 2. Si no vino en el paquete, buscar en el SessionManager por nombre de usuario
    if not mac:
        for m, ses in list(session_store._sessions_by_mac.items()):
            if ses.username == identidad_onos:
                mac = m
                break

    # 3. Remover sesión y eliminar regla OpenFlow 40001 del switch OVS
    if mac:
        sesion = session_store.remove_session(mac)
        if sesion and sesion.flow_id:
            await eliminar_flujo_onos(sesion.dpid, sesion.flow_id)
            print(
                f"\n[<<< LOGOUT] Sesión revocada y flujo {sesion.flow_id} eliminado de OVS para {sesion.username} ({mac})"
            )
            eventos.registrar("logout", sesion.username, mac, sesion.dpid, sesion.port, "Accounting-Stop: flujo de sesión eliminado", sesion.rol)
            return {"status": "session_terminated"}

    print(
        f"\n[LOGOUT] Se recibió Stop para {identidad_onos}, pero no había sesión activa en memoria."
    )
    return {"status": "user_not_found"}


import ldap3

class MabRequest(BaseModel):
    mac: str
    deviceId: str
    port: str

@app.post("/api/mab-request")
async def mab_request(datos: MabRequest):
    mac = datos.mac.lower().strip()
    dpid = datos.deviceId
    port = str(datos.port)

    try:
        servidor = ldap3.Server("ldap", port=389, connect_timeout=5)
        conexion = ldap3.Connection(
            servidor,
            user="cn=admin,dc=example,dc=org",
            password="admin",
            auto_bind=True,
            receive_timeout=5,
        )
        try:
            encontrado = conexion.search(
                "ou=devices,dc=example,dc=org",
                "(objectClass=inetOrgPerson)",
                attributes=["uid", "description"],
            )
            if not encontrado and conexion.result.get("result") != 0:
                raise RuntimeError(f"Consulta LDAP falló: {conexion.result}")
            identidad = next(
                (
                    str(registro.uid)
                    for registro in conexion.entries
                    if str(registro.description).lower().strip() == mac
                ),
                None,
            )
        finally:
            conexion.unbind()
    except Exception as exc:
        print(f"[MAB] LDAP no disponible: {exc}")
        raise HTTPException(status_code=503, detail="Consulta LDAP fallida")

    if identidad is None:
        print(f"[MAB] MAC no registrada; delegando a R2 MAB VM: {mac}")
        try:
            async with httpx.AsyncClient(timeout=8) as cliente:
                respuesta = await cliente.post(
                    f"{R2_MAB_URL}/api/mab-request",
                    json={
                        "mac": mac,
                        "deviceId": dpid,
                        "port": port,
                    },
                )

            return JSONResponse(
                status_code=respuesta.status_code,
                content=respuesta.json(),
            )

        except httpx.RequestError as exc:
            print(f"[MAB] R2 MAB VM no disponible: {exc}")
            raise HTTPException(
                status_code=503,
                detail="R2 MAB VM no disponible",
            )

    if session_store.is_spoofing(mac, dpid, port):
        print(f"[MAB] Conflicto de ubicación para {mac} en {dpid}:{port}")
        raise HTTPException(status_code=409, detail="MAC activa en otro puerto")

    sesion = session_store.get_by_mac(mac)
    if sesion is not None:
        if sesion.rol != "IoT" or sesion.vlan != 30:
            raise HTTPException(status_code=409, detail="MAC ya tiene una sesión de otro rol")
        return {
            "status": "FOUND", "mac": mac, "deviceId": dpid,
            "port": port, "identity": identidad, "vlan": 30,
            "session": "EXISTING", "flow_id": sesion.flow_id,
        }

    # Solo dispositivos IoT registrados en LDAP pueden obtener VLAN 30.
    flow_id = await inyectar_flujo_tabla_0(dpid, port, mac, 30)
    if flow_id is None:
        raise HTTPException(status_code=502, detail="ONOS no confirmó el flujo VLAN 30")

    session_store.register_session(
        ActiveSession(
            username=identidad, mac=mac, dpid=dpid, port=port,
            vlan=30, rol="IoT", flow_id=flow_id,
        )
    )
    print(f"[MAB] Autorizado IoT {mac} en VLAN 30; flujo={flow_id}")
    return {
        "status": "FOUND", "mac": mac, "deviceId": dpid,
        "port": port, "identity": identidad, "vlan": 30,
        "flow_id": flow_id,
    }



if __name__ == "__main__":
    uvicorn.run("policy_mapper:app", host="0.0.0.0", port=5000, reload=True)
