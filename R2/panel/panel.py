#!/usr/bin/env python3
"""panel.py: panel web de administración, VERSIÓN 1, SOLO LECTURA (docs/DISENO_PANEL_ADMIN.md). Solo biblioteca estándar de Python 3.

Un recolector consulta cada pocos segundos (no por navegador) y deja el resultado en una caché que leen las pantallas:
  ONOS (/devices de cada nodo) · Policy Mapper (/sessions, /events, /health/ha) · vigilante (/salud de cada instancia).
La bitácora de eventos se guarda además en SQLite, para conservar lo que el mapper rota. El panel NO cambia nada en la red: no hay ninguna ruta que escriba, y solo acepta GET (más el POST del acceso).

Acceso: la página muestra usuarios, MAC e IP (datos personales). Por defecto escucha SOLO en 127.0.0.1 (se llega por túnel SSH). Si se abre en otra IP, exige un token (cookie HttpOnly tras el acceso o
cabecera Authorization: Bearer) y se niega a arrancar sin él. El token y las credenciales de ONOS salen de variables de entorno (archivo 0600), no de la línea de comandos."""
import argparse
import base64
import hmac
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import datos  # noqa: E402

log = logging.getLogger("panel")
ESTATICOS = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
             "/app.js": ("app.js", "application/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
CABECERAS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
             "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


# ---------- lectura de las fuentes ----------
def leer_json(url: str, usuario: str = "", clave: str = "", timeout: float = 4.0):
    """(datos, None) o (None, motivo). Nunca lanza."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if usuario:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cuerpo = r.read(5_000_000)
            return (json.loads(cuerpo.decode("utf-8")) if cuerpo.strip() else {}), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, type(e).__name__


class Recolector:
    """Una ronda de lecturas por ciclo. `leer(url, **auth) -> (datos, error)` es inyectable para probar sin red."""

    def __init__(self, cfg: dict, historial: "datos.Historial", leer=leer_json, reloj=time.time):
        self.cfg, self.historial, self.leer, self.reloj = cfg, historial, leer, reloj
        self._cerrojo = threading.Lock()
        self._estado = {"listo": False, "t": None, "resumen": None, "sesiones": [], "vistas": None, "vigilantes": {}, "caidas": [], "mapper_ok": None}

    def ciclo(self) -> dict:
        ahora = self.reloj()
        c = self.cfg
        caidas, por_nodo = [], {}
        for nombre, url in c["onos"].items():
            r, err = self.leer(f"{url}/onos/v1/devices", usuario=c.get("onos_usuario", ""), clave=c.get("onos_clave", ""))
            por_nodo[nombre] = r
            if err:
                caidas.append(f"ONOS {nombre}: {err}")
        vistas = datos.vistas_onos(por_nodo)
        ses_raw, err = self.leer(f"{c['mapper']}/sessions")
        if err:
            caidas.append(f"mapper /sessions: {err}")
        sesiones = datos.normalizar_sesiones(ses_raw, ahora)
        ev_raw, err = self.leer(f"{c['mapper']}/events?limit=200")
        if err:
            caidas.append(f"mapper /events: {err}")
        elif isinstance(ev_raw, dict):
            self.historial.agregar(ev_raw.get("events") or [])
        _, err = self.leer(f"{c['mapper']}/health/ha")
        mapper_ok = True if err is None else (False if err.startswith("HTTP") else None)     # un 503 es «no listo»; un fallo de conexión es «no sé»
        if err and mapper_ok is None:
            caidas.append(f"mapper /health/ha: {err}")
        vigilantes = {}
        for nombre, url in c["vigilantes"].items():
            salud, err = self.leer(f"{url}/salud")
            vigilantes[nombre] = datos.estado_vigilante(salud, ahora)
            if err:
                caidas.append(f"vigilante {nombre}: {err}")
        res = datos.resumen(vistas, vigilantes, sesiones, mapper_ok, [x for x in caidas if x.startswith("mapper /")], ahora)
        with self._cerrojo:
            self._estado = {"listo": True, "t": ahora, "resumen": res, "sesiones": sesiones, "vistas": vistas, "vigilantes": vigilantes, "caidas": caidas, "mapper_ok": mapper_ok}
        return res

    def estado(self) -> dict:
        with self._cerrojo:
            return dict(self._estado)

    def correr(self, intervalo: float, parar: threading.Event):
        while not parar.is_set():
            try:
                self.ciclo()
            except Exception:                                        # un fallo de una ronda no debe tumbar el panel
                log.exception("fallo en la ronda del recolector")
            parar.wait(intervalo)


# ---------- servidor ----------
def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def hacer_manejador(rec: Recolector, historial: "datos.Historial", token: str, reloj=time.time):
    estaticos = {}
    for ruta, (archivo, tipo) in ESTATICOS.items():
        with open(os.path.join(AQUI, "static", archivo), "rb") as f:
            estaticos[ruta] = (f.read(), tipo)

    class Manejador(BaseHTTPRequestHandler):
        server_version = "panel"
        sys_version = ""

        def log_message(self, fmt, *a):
            log.info("%s %s", self.address_string(), fmt % a)

        def _enviar(self, codigo, cuerpo: bytes, tipo="application/json; charset=utf-8", extra=None):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            for k, v in {**CABECERAS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, obj, codigo=200):
            self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _autorizado(self) -> bool:
            if not token:
                return True                                           # solo se permite sin token en loopback (lo exige main)
            cab = self.headers.get("Authorization", "")
            if cab.startswith("Bearer ") and hmac.compare_digest(cab[7:].strip(), token):
                return True
            for parte in self.headers.get("Cookie", "").split(";"):
                k, _, v = parte.strip().partition("=")
                if k == "panel" and hmac.compare_digest(urllib.parse.unquote(v), token):
                    return True
            return False

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            ruta, q = url.path, {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            if ruta == "/salud":                                       # para comprobar que vive; no revela datos
                return self._json({"ok": True, "listo": rec.estado()["listo"]})
            if ruta in estaticos:
                cuerpo, tipo = estaticos[ruta]                         # la página y su código no son secretos; los datos sí (abajo)
                return self._enviar(200, cuerpo, tipo)
            if not self._autorizado():
                return self._json({"error": "no autorizado"}, 401)
            est = rec.estado()
            if ruta == "/api/resumen":
                return self._json({"listo": est["listo"], "resumen": est["resumen"], "caidas": est["caidas"], "ahora": reloj()})
            if ruta == "/api/sesiones":
                return self._json({"listo": est["listo"], "sesiones": est["sesiones"], "t": est["t"]})
            if ruta == "/api/vistas":
                return self._json({"listo": est["listo"], "vistas": est["vistas"], "t": est["t"]})
            if ruta == "/api/eventos":
                evs = historial.listar(tipo=q.get("tipo") or None, usuario=q.get("usuario") or None, mac=q.get("mac") or None, texto=q.get("texto") or None,
                                       desde=_num(q.get("desde")), hasta=_num(q.get("hasta")), limite=int(_num(q.get("limite")) or 100))
                return self._json({"eventos": evs, "total_guardados": historial.contar(), "tipos": list(datos.TIPOS_EVENTO)})
            return self._json({"error": "no existe"}, 404)

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path != "/entrar":
                return self._json({"error": "solo lectura"}, 405)
            largo = min(int(self.headers.get("Content-Length") or 0), 4096)
            dato = urllib.parse.parse_qs(self.rfile.read(largo).decode("utf-8", "replace")).get("token", [""])[0]
            if token and hmac.compare_digest(dato, token):
                return self._enviar(200, b'{"ok": true}', extra={"Set-Cookie": f"panel={urllib.parse.quote(token)}; HttpOnly; SameSite=Strict; Path=/"})
            time.sleep(0.5)                                            # frena la fuerza bruta
            return self._json({"error": "token incorrecto"}, 401)

        do_PUT = do_DELETE = do_PATCH = lambda self: self._json({"error": "solo lectura"}, 405)

    return Manejador


def _pares(texto: str) -> dict:
    out = {}
    for p in (texto or "").split(","):
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip().rstrip("/")
    return out


def main(argv=None) -> int:
    a = argparse.ArgumentParser(description="Panel de administración (solo lectura)")
    a.add_argument("--escuchar", default="127.0.0.1", help="IP donde escucha (por defecto solo este equipo)")
    a.add_argument("--puerto", type=int, default=8480)
    a.add_argument("--onos", required=True, metavar="ctrl1=http://IP:8181,...", help="nodos de ONOS")
    a.add_argument("--mapper", required=True, help="URL del Policy Mapper (gateway)")
    a.add_argument("--vigilantes", default="", metavar="ctrl3=http://IP:8098,...")
    a.add_argument("--db", default="panel.sqlite")
    a.add_argument("--intervalo", type=float, default=10.0, help="segundos entre rondas del recolector")
    a.add_argument("--maximo-eventos", type=int, default=20000)
    arg = a.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("PANEL_TOKEN", "")
    if arg.escuchar not in ("127.0.0.1", "::1", "localhost") and len(token) < 16:
        log.error("NO ARRANCO: escuchar en %s exige PANEL_TOKEN de al menos 16 caracteres (la página muestra datos personales).", arg.escuchar)
        return 2
    cfg = {"onos": _pares(arg.onos), "mapper": arg.mapper.rstrip("/"), "vigilantes": _pares(arg.vigilantes),
           "onos_usuario": os.environ.get("PANEL_ONOS_USUARIO", ""), "onos_clave": os.environ.get("PANEL_ONOS_CLAVE", "")}
    historial = datos.Historial(arg.db, arg.maximo_eventos)
    rec = Recolector(cfg, historial)
    parar = threading.Event()
    threading.Thread(target=rec.correr, args=(arg.intervalo, parar), daemon=True).start()
    srv = ThreadingHTTPServer((arg.escuchar, arg.puerto), hacer_manejador(rec, historial, token))
    log.info("Panel (solo lectura) en http://%s:%d, token %s, %d nodos de ONOS, cada %.0f s", arg.escuchar, arg.puerto, "exigido" if token else "no (solo loopback)", len(cfg["onos"]), arg.intervalo)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        parar.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
