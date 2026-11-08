#!/usr/bin/env python3
"""Agente 802.1X con interfaz web (R1).

Simula en un host de VNRT lo que hace una PC real: un servicio autentica la interfaz de datos con EAP-TLS en cuanto
arranca (o cuando el enlace sube) y una pequena ventana permite elegir la identidad y ver el estado. Se ejecuta como
root con systemd (unidad r1-agente.service) y escucha SOLO en 127.0.0.1; se ve desde el PC con un tunel SSH:

    ssh -L 8081:127.0.0.1:8080 -p <puerto-del-host> ubuntu@<IP>      y despues  http://localhost:8081

Solo usa la biblioteca estandar. Lanza `wpa_supplicant -D wired` con la configuracion del usuario elegido (una sola
instancia a la vez: dos instancias cruzan la conversacion EAP) y lee su estado con `wpa_cli`.

Variables de entorno: R1_IFACE (ens4), R1_DIR (carpeta con los wpa_*.conf y certs), R1_USUARIO (identidad por defecto,
la que se conecta sola al arrancar), R1_MAPPER (URL del Policy Mapper), R1_PUERTO (8080).
"""
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IFACE = os.environ.get("R1_IFACE", "ens4")
DIR = Path(os.environ.get("R1_DIR", "/home/ubuntu/supp"))
DEFECTO = os.environ.get("R1_USUARIO", "")
MAPPER = os.environ.get("R1_MAPPER", "http://192.168.0.10:5000").rstrip("/")
PUERTO = int(os.environ.get("R1_PUERTO", "8080"))
WPA_SUPPLICANT = os.environ.get("R1_WPA", "/usr/sbin/wpa_supplicant")
WPA_CLI = os.environ.get("R1_WPA_CLI", "/usr/sbin/wpa_cli")
CTRL = "/run/wpa_supplicant"

ROLES = {"profesor": ("Docente", 10), "alumno": ("Alumno", 20), "support": ("Soporte (Administrador)", 40)}


# ---------------------------------------------------------------- funciones puras (con pruebas)

def rol_de(uid):
    for prefijo, (rol, vlan) in ROLES.items():
        if uid.startswith(prefijo):
            return rol, vlan
    return "Sin rol", None


def descubrir_usuarios(carpeta):
    """{uid: ruta del wpa_*.conf}, leyendo la identidad de cada configuracion de suplicante."""
    encontrados = {}
    for conf in sorted(Path(carpeta).glob("wpa_*.conf")):
        m = re.search(r'identity="([^"@]+)@', conf.read_text(errors="ignore"))
        if m:
            encontrados[m.group(1)] = str(conf)
    return encontrados


def parsear_wpa(texto):
    """La salida `clave=valor` de `wpa_cli status` (en el modo con cable incluye 'Supplicant PAE state' y 'EAP state')."""
    datos = {}
    for linea in (texto or "").splitlines():
        if "=" in linea and not linea.startswith("Selected"):
            clave, _, valor = linea.partition("=")
            datos[clave.strip()] = valor.strip()
    return datos


def estado_desde_wpa(datos, proceso_vivo, log):
    """desconectado | autenticando | autenticado | rechazado, a partir de wpa_cli y, si no responde, del log."""
    if not proceso_vivo:
        return "desconectado"
    if datos.get("suppPortStatus") == "Authorized":
        return "autenticado"
    if datos.get("Supplicant PAE state") == "HELD" or datos.get("EAP state") == "FAILURE":
        return "rechazado"
    if datos:
        return "autenticando"
    for linea in reversed(list(log)):   # sin wpa_cli: el ultimo evento EAP del log
        if "CTRL-EVENT-EAP-SUCCESS" in linea:
            return "autenticado"
        if "CTRL-EVENT-EAP-FAILURE" in linea:
            return "rechazado"
    return "autenticando"


def sesion_de(mac, sesiones):
    """La sesion del Policy Mapper de esa MAC (el mapper devuelve {'sessions': {mac: {...}}})."""
    return (sesiones or {}).get("sessions", {}).get(mac.lower())


def ultimo_rechazo(mac, eventos, desde):
    """El ultimo evento de rechazo o conflicto del Policy Mapper para esa MAC, posterior a `desde`."""
    lista = eventos
    if isinstance(eventos, dict):
        lista = next((v for v in eventos.values() if isinstance(v, list)), [])
    candidatos = [e for e in lista if isinstance(e, dict) and str(e.get("mac", "")).lower() == mac.lower()
                  and e.get("tipo") in ("rechazado", "conflicto") and e.get("t", 0) >= desde - 2]
    return candidatos[-1] if candidatos else None


# ---------------------------------------------------------------- el agente

def http_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None


class Agente:
    def __init__(self, iface=IFACE, carpeta=DIR, defecto=DEFECTO, mapper=MAPPER):
        self.iface, self.carpeta, self.defecto, self.mapper = iface, carpeta, defecto, mapper
        self.lock = threading.RLock()
        self.proc = None
        self.usuario = None      # identidad con la que corre wpa_supplicant ahora
        self.deseado = None      # identidad que debe estar conectada (el vigilante la relanza si se cae)
        self.desde = 0.0
        self.log = deque(maxlen=60)

    def usuarios(self):
        return descubrir_usuarios(self.carpeta)

    def mac(self):
        try:
            return Path(f"/sys/class/net/{self.iface}/address").read_text().strip()
        except OSError:
            return ""

    def _leer_salida(self, proc):
        for linea in proc.stdout:
            self.log.append(time.strftime("%H:%M:%S ") + linea.rstrip())

    def _lanzar(self, uid):
        conf = self.usuarios().get(uid)
        if not conf:
            raise ValueError(f"No hay configuracion de suplicante para '{uid}' en {self.carpeta}")
        try:
            os.remove(f"{CTRL}/{self.iface}")   # un socket viejo impide que la nueva instancia arranque
        except OSError:
            pass
        self.log.append(time.strftime("%H:%M:%S ") + f"== Conectando como {uid}")
        self.proc = subprocess.Popen([WPA_SUPPLICANT, "-D", "wired", "-i", self.iface, "-c", conf, "-C", CTRL],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.usuario, self.desde = uid, time.time()
        threading.Thread(target=self._leer_salida, args=(self.proc,), daemon=True).start()

    def _parar(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()   # wpa_supplicant envia EAPOL-Logoff al cerrarse
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc:
            self.log.append(time.strftime("%H:%M:%S ") + "== Desconectado")
        self.usuario = None

    def conectar(self, uid):
        with self.lock:
            if uid not in self.usuarios():
                raise ValueError(f"Usuario desconocido: {uid}")
            self._parar()
            self.deseado = uid
            self._lanzar(uid)

    def desconectar(self):
        with self.lock:
            self.deseado = None
            self._parar()

    def vigilar(self):
        """Si wpa_supplicant se cae solo, se vuelve a lanzar con la identidad deseada."""
        while True:
            time.sleep(5)
            with self.lock:
                if self.deseado and (self.proc is None or self.proc.poll() is not None):
                    try:
                        self._lanzar(self.deseado)
                    except (ValueError, OSError) as e:
                        self.log.append(f"!! No pude relanzar el suplicante: {e}")

    def _wpa_cli(self):
        try:
            r = subprocess.run([WPA_CLI, "-p", CTRL, "-i", self.iface, "status"], capture_output=True, text=True, timeout=3)
            return parsear_wpa(r.stdout) if r.returncode == 0 else {}
        except (OSError, subprocess.TimeoutExpired):
            return {}

    def estado(self):
        with self.lock:
            vivo = self.proc is not None and self.proc.poll() is None
            activo, desde = self.usuario, self.desde
        datos = self._wpa_cli() if vivo else {}
        estado = estado_desde_wpa(datos, vivo, self.log)
        mac = self.mac()
        rol, vlan = rol_de(activo) if activo else ("", None)
        resp = {"host": socket.gethostname(), "iface": self.iface, "mac": mac, "estado": estado, "usuario": activo,
                "usuarios": [{"id": u, "rol": rol_de(u)[0], "vlan": rol_de(u)[1]} for u in self.usuarios()],
                "defecto": self.defecto, "mensaje": "", "sesion": None, "log": list(self.log)[-25:]}
        if estado == "autenticado":
            sesion = sesion_de(mac, http_json(f"{self.mapper}/sessions")) if mac else None
            if sesion:
                resp["sesion"] = sesion
                resp["mensaje"] = f"Acceso concedido como {rol} (VLAN {sesion.get('vlan', vlan)})"
            else:
                resp["mensaje"] = "Autenticado por 802.1X; sin datos del Policy Mapper"
        elif estado == "rechazado":
            ev = ultimo_rechazo(mac, http_json(f"{self.mapper}/events"), desde) if mac else None
            resp["mensaje"] = ev.get("detalle", "Acceso denegado") if ev else "El servidor rechazo la autenticacion"
        elif estado == "autenticando":
            resp["mensaje"] = "Negociando EAP-TLS con el servidor..."
        else:
            resp["mensaje"] = "Sin autenticar: la interfaz no tiene acceso a la red de usuarios"
        return resp


# ---------------------------------------------------------------- pagina web

PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Autenticacion de red 802.1X</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--tx:#1b2430;--mut:#667085;--bd:#d8dee6;--ok:#12805c;--okbg:#e3f6ee;--wa:#b25e09;--wabg:#fff1de;--er:#c0372d;--erbg:#fde8e6;--gr:#667085;--grbg:#eceff3;--ac:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f141b;--card:#18202b;--tx:#e8edf3;--mut:#93a1b3;--bd:#2a3544;--ok:#4fd1a0;--okbg:#12362b;--wa:#f0a64a;--wabg:#3a2a12;--er:#ff8b80;--erbg:#3d1a17;--gr:#93a1b3;--grbg:#222c39;--ac:#6ea0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,Segoe UI,sans-serif;display:flex;justify-content:center;padding:24px 16px}
.w{width:100%;max-width:560px}h1{font-size:18px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 16px;font-size:13px}
.c{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px;margin-bottom:14px}
.b{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;font-weight:600;font-size:14px}
.b i{width:9px;height:9px;border-radius:50%;background:currentColor}
.autenticado{color:var(--ok);background:var(--okbg)}.autenticando{color:var(--wa);background:var(--wabg)}.autenticando i{animation:p 1s infinite}
.rechazado{color:var(--er);background:var(--erbg)}.desconectado{color:var(--gr);background:var(--grbg)}@keyframes p{50%{opacity:.25}}
.m{margin:10px 0 0;color:var(--mut)}label{display:block;font-size:12px;color:var(--mut);margin:14px 0 4px;text-transform:uppercase;letter-spacing:.04em}
select{width:100%;padding:9px 10px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--tx);font-size:15px}
.r{display:flex;gap:10px;margin-top:14px}button{flex:1;padding:10px;border-radius:8px;border:1px solid var(--bd);font-size:15px;font-weight:600;cursor:pointer;background:var(--card);color:var(--tx)}
button.p{background:var(--ac);border-color:var(--ac);color:#fff}button:disabled{opacity:.5;cursor:default}
dl{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:0}dt{color:var(--mut)}dd{margin:0;font-variant-numeric:tabular-nums;word-break:break-all}
pre{margin:0;max-height:210px;overflow:auto;font:12px/1.5 ui-monospace,Consolas,monospace;color:var(--mut);white-space:pre-wrap}
h2{font-size:13px;margin:0 0 10px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
</style></head><body><div class="w">
<h1>Autenticacion de red (802.1X)</h1><p class="sub" id="sub">Adaptador de red cableado</p>
<div class="c"><span class="b desconectado" id="b"><i></i><span id="bt">Comprobando...</span></span><p class="m" id="msg"></p>
<label for="u">Identidad (certificado)</label><select id="u"></select>
<div class="r"><button class="p" id="con">Conectar</button><button id="des">Desconectar</button></div></div>
<div class="c"><h2>Conexion</h2><dl id="dl"></dl></div>
<div class="c"><h2>Registro del suplicante</h2><pre id="log"></pre></div></div>
<script>
const $=id=>document.getElementById(id);let primera=true;
const T={autenticado:"Autenticado",autenticando:"Autenticando...",rechazado:"Acceso denegado",desconectado:"Desconectado"};
async function api(p,b){const r=await fetch(p,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{});return r.json()}
function fila(k,v){return v?`<dt>${k}</dt><dd>${v}</dd>`:""}
async function refresca(){let e;try{e=await api("/api/estado")}catch(x){$("bt").textContent="Sin conexion con el agente";return}
 $("b").className="b "+e.estado;$("bt").textContent=T[e.estado]||e.estado;$("msg").textContent=e.mensaje;
 $("sub").textContent=e.host+" - adaptador "+e.iface;
 const s=$("u");if(primera||s.options.length!==e.usuarios.length){s.innerHTML=e.usuarios.map(u=>`<option value="${u.id}">${u.id} - ${u.rol}</option>`).join("");s.value=e.usuario||e.defecto||s.value;primera=false}
 const se=e.sesion||{};$("dl").innerHTML=fila("Adaptador",e.iface+" ("+e.mac+")")+fila("Identidad",e.usuario)+fila("Rol",se.rol)+fila("VLAN",se.vlan)+fila("Switch",se.dpid)+fila("Puerto",se.port)+fila("Metodo",e.estado==="autenticado"?"EAP-TLS":"");
 $("log").textContent=e.log.join("\\n");$("log").scrollTop=1e6;
 $("des").disabled=e.estado==="desconectado"}
$("con").onclick=async()=>{$("con").disabled=true;await api("/api/conectar",{usuario:$("u").value});$("con").disabled=false;refresca()};
$("des").onclick=async()=>{await api("/api/desconectar",{});refresca()};
refresca();setInterval(refresca,2000);
</script></body></html>
"""


class Manejador(BaseHTTPRequestHandler):
    agente = None   # se asigna en main()

    def log_message(self, *a):
        pass

    def _enviar(self, codigo, cuerpo, tipo="application/json"):
        datos = cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
        self.send_response(codigo)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def _host_valido(self):   # evita que una pagina ajena (DNS rebinding) mande ordenes al agente
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self):
        if not self._host_valido():
            return self._enviar(403, '{"error":"host no permitido"}')
        if self.path in ("/", "/index.html"):
            return self._enviar(200, PAGINA, "text/html")
        if self.path == "/api/estado":
            return self._enviar(200, json.dumps(self.agente.estado()))
        self._enviar(404, '{"error":"no existe"}')

    def do_POST(self):
        if not self._host_valido():
            return self._enviar(403, '{"error":"host no permitido"}')
        try:
            largo = int(self.headers.get("Content-Length", 0))
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
            if self.path == "/api/conectar":
                self.agente.conectar(str(cuerpo.get("usuario", "")))
            elif self.path == "/api/desconectar":
                self.agente.desconectar()
            else:
                return self._enviar(404, '{"error":"no existe"}')
            self._enviar(200, '{"ok":true}')
        except (ValueError, OSError) as e:
            self._enviar(400, json.dumps({"error": str(e)}))


def main():
    agente = Agente()
    Manejador.agente = agente
    if agente.defecto in agente.usuarios():     # como el demonio de una PC: autentica solo al arrancar
        agente.conectar(agente.defecto)
    elif agente.defecto:
        agente.log.append(f"!! La identidad por defecto '{agente.defecto}' no tiene configuracion en {agente.carpeta}")
    threading.Thread(target=agente.vigilar, daemon=True).start()
    servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)

    def salir(*_):
        agente.desconectar()
        os._exit(0)
    signal.signal(signal.SIGTERM, salir)
    print(f"Agente 802.1X en http://127.0.0.1:{PUERTO} (adaptador {agente.iface}, identidad por defecto {agente.defecto or '-'})", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        agente.desconectar()


if __name__ == "__main__":
    main()
