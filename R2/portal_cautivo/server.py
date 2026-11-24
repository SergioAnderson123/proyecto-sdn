"""Portal cautivo: página web con usuario y contraseña que pide al Policy Mapper instalar la sesión.

Corre en la IP del portal (policy.yaml -> portal.ip, 10.0.0.60) y escucha en dos puertos:
  * 8080  el portal en sí (formulario, estado, cierre de sesión);
  * 8083  el destino desviado: el switch reescribe hacia aquí el HTTP de un usuario sin sesión hacia 10.0.0.40:8003, y este puerto
          responde con una redirección (302) al formulario. Con sesión el flujo de R2 gana y el tráfico nunca llega aquí.

La MAC del cliente se lee de la tabla ARP del portal (mismo segmento L2), nunca de lo que envía el cliente. Con la capa 3 (clientes en otra subred, detrás del gateway)
el portal no es vecino de capa 2 del cliente y su ARP no la tiene: entonces el portal NO adivina nada, manda al Policy Mapper la IP de ORIGEN DE LA CONEXIÓN (la del
socket, no ninguna que declare el cliente) junto con la MAC si la tiene, y es el mapper quien la resuelve con los hosts de ONOS (los rellena la app DHCP). Así el portal no
necesita credenciales de ONOS. Requiere un mapper que acepte `ip` en /portal/login, /portal/logout y /portal/estado (docs/NOTA_PORTAL_MAC_POR_IP.md); uno anterior ignora `ip`.
Sin dependencias fuera de la biblioteca estándar. Diseño: Tabler (MIT).

HTTPS: con PORTAL_TLS_CERT y PORTAL_TLS_KEY (rutas a un certificado y su clave en PEM) todos los puertos hablan TLS 1.2+; sin ellas, HTTP
(el laboratorio de Mininet). La contrasena del usuario viaja en el formulario: sin TLS, quien se haga pasar por el portal (ARP abierto
en la cuarentena) la captura; con TLS y el certificado fijado en el cliente, no.

Variables: MAPPER_URL (http://policy_mapper:5000), PORTAL_TOKEN, PORTAL_IP (10.0.0.60), PORTAL_PUERTOS (8080,8083),
           PORTAL_TLS_CERT, PORTAL_TLS_KEY
"""
import html
import json
import mimetypes
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAPPER = os.getenv("MAPPER_URL", "http://policy_mapper:5000").rstrip("/")
TOKEN = os.getenv("PORTAL_TOKEN", "")
IP_PORTAL = os.getenv("PORTAL_IP", "10.0.0.60")
PUERTOS = [int(p) for p in os.getenv("PORTAL_PUERTOS", "8080,8083").split(",")]
PUERTO_PORTAL = PUERTOS[0]
RAIZ = os.path.dirname(os.path.abspath(__file__))
DESTINO_POR_DEFECTO = os.getenv("DESTINO_POR_DEFECTO", "http://10.0.0.40:8003/")
TLS_CERT = os.getenv("PORTAL_TLS_CERT", "")
TLS_KEY = os.getenv("PORTAL_TLS_KEY", "")
ESQUEMA = "https" if TLS_CERT else "http"


class ServidorTLS(ThreadingHTTPServer):
    """El mismo servidor con TLS. El apreton de manos se hace en el hilo de cada conexion (no en el `accept`), para que un cliente
    que se quede a medias no bloquee a los demas; un fallo de TLS solo cierra esa conexion."""
    contexto = None

    def get_request(self):
        conexion, direccion = super().get_request()
        conexion.settimeout(10)
        return self.contexto.wrap_socket(conexion, server_side=True, do_handshake_on_connect=False), direccion

    def handle_error(self, request, client_address):
        pass     # TLS fallido o cliente que se va: no hace falta una traza por cada uno


def contexto_tls(cert, clave):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, clave)
    return ctx


def crear_servidor(ip, puerto, cert="", clave=""):
    """Servidor HTTP, o HTTPS si hay certificado y clave."""
    if not cert:
        return ThreadingHTTPServer((ip, puerto), Manejador)
    srv = ServidorTLS((ip, puerto), Manejador)
    srv.contexto = contexto_tls(cert, clave)
    return srv


def mac_de(ip):
    """MAC de un vecino L2 según /proc/net/arp (None si no figura)."""
    try:
        with open("/proc/net/arp") as f:
            next(f)
            for linea in f:
                c = linea.split()
                if c[0] == ip and c[2] != "0x0" and c[3] != "00:00:00:00:00:00":
                    return c[3].lower()
    except OSError:
        pass
    return None


def llamar_mapper(ruta, cuerpo=None, espera=6):
    """(código HTTP, JSON) de una llamada al Policy Mapper; (0, {}) si no responde en `espera` segundos."""
    req = urllib.request.Request(MAPPER + ruta, method="POST" if cuerpo is not None else "GET",
                                 data=None if cuerpo is None else json.dumps(cuerpo).encode(),
                                 headers={"Content-Type": "application/json", "X-Portal-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=espera) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {}
    except Exception:  # noqa: BLE001 - el mapper caído se muestra como error, no tumba el portal
        return 0, {}


CIERRE_ESTADOS_OK = ("cerrada", "sin_sesion")      # lo que contesta el mapper cuando ya no queda sesión del portal para ese equipo (idempotente)
CIERRE_INTENTOS = int(os.getenv("PORTAL_CIERRE_INTENTOS", "3"))
CIERRE_ESPERA_S = float(os.getenv("PORTAL_CIERRE_ESPERA_S", "8"))   # por intento: el mapper llegó a tardar entre 5 y 11 s en una transacción (24-nov), más que los 6 s de antes
CIERRE_PAUSA_S = float(os.getenv("PORTAL_CIERRE_PAUSA_S", "0.5"))


def cerrar_sesion_en_mapper(quien):
    """Pide al mapper cerrar la sesión del portal y devuelve True SOLO si lo confirmó (`cerrada` o `sin_sesion`).

    Antes el portal ignoraba la respuesta: si el mapper tardaba más que el tiempo de espera, la llamada se perdía, el portal redirigía igual y la persona creía haber
    cerrado cuando su sesión (un docente elevado, por ejemplo) seguía abierta hasta vencer (24-nov-2026). Ahora reintenta (el cierre es idempotente) y, si no hay
    confirmación, lo dice. Cualquier otra cosa (timeout, error HTTP, respuesta sin `status` conocido) cuenta como no confirmada."""
    for intento in range(1, max(1, CIERRE_INTENTOS) + 1):
        codigo, r = llamar_mapper("/portal/logout", quien, CIERRE_ESPERA_S)
        if codigo == 200 and r.get("status") in CIERRE_ESTADOS_OK:
            return True
        print(f"[portal] cierre de sesión sin confirmar (intento {intento}/{CIERRE_INTENTOS}, mapper: {codigo or 'sin respuesta'})", flush=True)
        if intento < CIERRE_INTENTOS:
            time.sleep(CIERRE_PAUSA_S)
    return False


def pagina_cierre_fallido():
    return pagina("No se pudo cerrar la sesión", """<div class="card card-md"><div class="card-body text-center">
<span class="badge bg-red-lt mb-3">Sin confirmar</span><h2 class="card-title">No se pudo confirmar el cierre de su sesión</h2>
<p class="text-secondary">Su sesión puede seguir abierta. Reintente; si el problema continúa, avise al soporte de la red.</p>
<form method="post" action="/logout"><button class="btn btn-primary w-100" type="submit">Reintentar cierre de sesión</button></form></div></div>""")


def destino_seguro(d):
    """Solo se vuelve a un destino http(s) de la red de datos; evita usar el portal como redirección abierta."""
    p = urllib.parse.urlparse(d or "")
    return d if p.scheme == "http" and p.hostname and p.hostname.startswith("10.0.0.") else DESTINO_POR_DEFECTO


LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 4.5v5c0 4.5-3.2 7.4-8 9-4.8-1.6-8-4.5-8-9v-5z"/>'
        '<path d="M9 12l2 2 4-4"/></svg>')


def pagina(titulo, cuerpo):
    return f"""<!doctype html>
<html lang="es" data-bs-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)}</title><link rel="stylesheet" href="/static/tabler.min.css">
<style>:root{{--tblr-font-sans-serif:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif}}</style></head>
<body class="d-flex flex-column"><div class="page page-center"><div class="container container-tight py-4">
<div class="text-center mb-4"><span class="avatar avatar-lg bg-primary-lt">{LOGO}</span>
<h1 class="h2 mt-3 mb-0">Acceso a la red del campus</h1><div class="text-secondary">Portal cautivo</div></div>
{cuerpo}
<div class="text-center text-secondary small mt-3">Si su equipo usa 802.1X no necesita este portal, salvo para subir de rol (docentes).</div>
</div></div></body></html>"""


def formulario(destino, error=""):
    aviso = f'<div class="alert alert-danger" role="alert">{html.escape(error)}</div>' if error else ""
    return pagina("Acceso a la red", f"""<form class="card card-md" method="post" action="/login" autocomplete="off"><div class="card-body">
<h2 class="card-title text-center mb-4">Inicie sesión</h2>{aviso}
<input type="hidden" name="destino" value="{html.escape(destino)}">
<div class="mb-3"><label class="form-label">Usuario</label><input class="form-control" name="usuario" placeholder="p. ej. guest01" autofocus required></div>
<div class="mb-2"><label class="form-label">Contraseña</label><input class="form-control" type="password" name="clave" required></div>
<div class="form-footer"><button class="btn btn-primary w-100" type="submit">Conectarse</button></div></div></form>""")


def ascenso_disponible(sesion):
    """Roles a los que una persona puede subir este equipo desde el portal (la sesión es 802.1X del equipo y la política lo permite)."""
    return [r for r in (sesion.get("ascenso_a") or [])]


def pagina_ascenso(sesion, destino, error=""):
    """Equipo con sesión 802.1X que admite subir de rol: formulario para que una persona entre con su propio usuario."""
    roles = ", ".join(r.capitalize() for r in ascenso_disponible(sesion))
    aviso = f'<div class="alert alert-danger" role="alert">{html.escape(error)}</div>' if error else ""
    return pagina("Equipo conectado", f"""<div class="card card-md"><div class="card-body">
<div class="text-center"><span class="badge bg-green-lt mb-3">Equipo conectado</span>
<h2 class="card-title">{html.escape(sesion['usuario'])}</h2>
<p class="text-secondary mb-3">Rol del equipo: <strong>{html.escape(sesion['rol'])}</strong> · método: {html.escape(sesion['metodo'])}</p></div>
{aviso}<form method="post" action="/login" autocomplete="off">
<input type="hidden" name="destino" value="{html.escape(destino)}">
<p class="text-secondary small mb-2">¿Es usted {html.escape(roles)}? Entre con su usuario personal para subir de rol en este equipo.</p>
<div class="mb-3"><label class="form-label">Usuario</label><input class="form-control" name="usuario" required></div>
<div class="mb-3"><label class="form-label">Contraseña</label><input class="form-control" type="password" name="clave" required></div>
<button class="btn btn-primary w-100" type="submit">Subir de rol</button></form>
<a class="btn btn-outline-secondary w-100 mt-2" href="{html.escape(destino)}">Continuar como {html.escape(sesion['rol'])}</a></div></div>""")


def sesion_activa(sesion, destino):
    base = sesion.get("base")
    vuelve = (f'<p class="text-secondary small">Al cerrar sesión, el equipo vuelve al rol <strong>{html.escape(base["rol"])}</strong>.</p>'
              if base else "")
    return pagina("Sesión activa", f"""<div class="card card-md"><div class="card-body text-center">
<span class="badge bg-green-lt mb-3">Conectado</span><h2 class="card-title">Hola, {html.escape(sesion['usuario'])}</h2>
<p class="text-secondary mb-1">Rol: <strong>{html.escape(sesion['rol'])}</strong> · método: {html.escape(sesion['metodo'])}</p>
<p class="text-secondary">Ya puede usar los recursos que su rol permite.</p>{vuelve}
<a class="btn btn-primary w-100 mb-2" href="{html.escape(destino)}">Continuar</a>
<form method="post" action="/logout"><button class="btn btn-outline-secondary w-100" type="submit">Cerrar sesión</button></form></div></div>""")


class Manejador(BaseHTTPRequestHandler):
    def _enviar(self, codigo, cuerpo, tipo="text/html; charset=utf-8", extra=None):
        datos = cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(datos)

    def _redir(self, url):
        self._enviar(302, "", extra={"Location": url})

    def _ip(self):
        return self.client_address[0]

    def _mac(self):
        """La MAC del ARP del portal, solo si el cliente es vecino de capa 2; si no, None (el mapper la resolverá por la IP)."""
        return mac_de(self._ip())

    def _quien(self):
        """Lo que se manda al mapper para identificar al equipo: la MAC del ARP (o vacía) y la IP de origen de la conexión."""
        return {"mac": self._mac() or "", "ip": self._ip()}

    def do_GET(self):
        puerto = self.server.server_address[1]
        ruta, _, consulta = self.path.partition("?")
        if ruta.startswith("/static/") and ".." not in ruta:
            destino = os.path.join(RAIZ, ruta.lstrip("/"))
            if os.path.isfile(destino):
                with open(destino, "rb") as f:
                    return self._enviar(200, f.read(), mimetypes.guess_type(destino)[0] or "application/octet-stream")
        if puerto != PUERTO_PORTAL:
            # destino desviado: se manda al formulario recordando a dónde quería ir el usuario
            original = f"http://{self.headers.get('Host', '10.0.0.40:8003')}{self.path}"
            return self._redir(f"{ESQUEMA}://{IP_PORTAL}:{PUERTO_PORTAL}/?" + urllib.parse.urlencode({"destino": original}))
        destino = destino_seguro(urllib.parse.parse_qs(consulta).get("destino", [""])[0])
        _, r = llamar_mapper("/portal/estado?" + urllib.parse.urlencode(self._quien()))
        if r.get("sesion"):
            if ascenso_disponible(r["sesion"]):
                return self._enviar(200, pagina_ascenso(r["sesion"], destino))
            return self._enviar(200, sesion_activa(r["sesion"], destino))
        self._enviar(200, formulario(destino))

    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        campos = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(min(largo, 4096)).decode()).items()}
        quien = self._quien()
        if self.path == "/logout":
            if cerrar_sesion_en_mapper(quien):
                return self._redir("/")
            return self._enviar(503, pagina_cierre_fallido())      # no se redirige como si nada: la sesión puede seguir abierta
        if self.path != "/login":
            return self._enviar(404, "no encontrado", "text/plain")
        destino = destino_seguro(campos.get("destino"))
        codigo, r = llamar_mapper("/portal/login", {"usuario": campos.get("usuario", ""), "clave": campos.get("clave", ""), **quien})
        if codigo == 200:
            return self._enviar(200, sesion_activa({"usuario": r["usuario"], "rol": r["rol"], "metodo": "Portal"}, destino))
        mensaje = (r.get("detail") if codigo else "El servicio de acceso no responde; intente más tarde.") or "No se pudo iniciar sesión."
        _, estado = llamar_mapper("/portal/estado?" + urllib.parse.urlencode(quien))
        http = codigo if codigo in (401, 429) else 200           # 429: bloqueo temporal por intentos fallidos (lo decide el mapper)
        if (estado.get("sesion") or {}).get("ascenso_a"):       # equipo con sesión 802.1X: se vuelve a ofrecer subir de rol
            return self._enviar(http, pagina_ascenso(estado["sesion"], destino, mensaje))
        self._enviar(http, formulario(destino, mensaje))

    def log_message(self, fmt, *a):
        print(f"[portal] {self.client_address[0]}:{self.server.server_address[1]} {fmt % a}", flush=True)


if __name__ == "__main__":
    servidores = [crear_servidor(IP_PORTAL if os.getenv("PORTAL_BIND_IP") else "0.0.0.0", p, TLS_CERT, TLS_KEY) for p in PUERTOS]
    for s in servidores[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"Portal cautivo en {ESQUEMA}://{IP_PORTAL}:{PUERTO_PORTAL} (desvío en {PUERTOS[1:]}; mapper {MAPPER})", flush=True)
    try:
        servidores[0].serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
