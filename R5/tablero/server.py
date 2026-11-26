#!/usr/bin/env python3
"""Tablero en vivo de R5: una página que reúne, en un solo lugar, lo que hoy se mira en varias terminales SSH.

Corre EN switch-borde (nunca en un nodo del clúster ONOS: ver docs/DISENO_TABLERO.md). Es de solo lectura salvo UNA
acción explícita de escritura: el botón "Desbloquear todo" (o "Quitar" por fila), que solo retira bloqueos que el
propio tablero ya vio en la tabla (nunca instala nada nuevo ni acepta una IP que no haya visto antes). Dos fuentes
de lectura, ambas por SSH a los sensores (ninguna necesita acceso a OVS en switch-borde):

  * eve-alerts.json de Suricata en node-sec-1 y node-sec-2 (`docker exec r5-suricata tail -F ...`), en vivo, una
    conexión SSH persistente por sensor con reconexión automática.
  * `r5ctl bloqueos`, que ya lee ONOS y agrega los tres switches (switch-borde, sw-core-1, sw-core-2) con sus
    paquetes descartados; se consulta cada pocos segundos a uno de los dos sensores (indistinto, el estado es el
    mismo en ONOS).

Ambas se difunden por Server-Sent Events (SSE) a los navegadores conectados. Sin dependencias fuera de la
biblioteca estándar (como R2/portal_cautivo/server.py); reutiliza su mismo CSS (Tabler).

ARRANQUE MANUAL, no como servicio systemd: se prende para la demo y se apaga al terminar (ver README.md). No
correrlo a la vez que `R5/deploy/medicion_r5.py recursos` o `escala`: el muestreo de esas pruebas no debe competir
con el tráfico SSH propio del tablero.

Variables: R5_TABLERO_PUERTO (8090), R5_PASS o ~/.r5_pass (contraseña de ubuntu, igual que repeticiones.py),
           R5_TABLERO_INTERVALO_BLOQUEOS (3.0 s).
Uso, en switch-borde:
    cd ~/proyecto-sdn && python3 R5/tablero/server.py
    (Ctrl+C para apagarlo)
"""
import html
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_REPO = os.path.dirname(os.path.dirname(AQUI))               # .../proyecto-sdn
sys.path.insert(0, os.path.join(RAIZ_REPO, "R5", "deploy"))
sys.path.insert(0, os.path.join(RAIZ_REPO, "R5", "codigo"))
import medicion_lib as lib                                        # noqa: E402  (leer_alertas: parsea una linea de eve-alerts.json)
from repeticiones import SENSORES, USUARIO, leer_clave             # noqa: E402  (mismas IP y misma contraseña que las demas herramientas)

EVE = "/var/log/suricata/eve-alerts.json"
STATIC_PORTAL = os.path.join(RAIZ_REPO, "R2", "portal_cautivo", "static")   # se reutiliza el CSS del portal de R1/R2 (Tabler, MIT)
PUERTO = int(os.getenv("R5_TABLERO_PUERTO", "8090"))
INTERVALO_BLOQUEOS = float(os.getenv("R5_TABLERO_INTERVALO_BLOQUEOS", "3.0"))

CLAVE = ""                       # se rellena en main(); los hilos la leen de aca (no hay credenciales en el navegador)
SSHPASS = shutil.which("sshpass")

# -------------------------------------------------------------------------------------------------- difusion SSE
_clientes = []
_lock_clientes = threading.Lock()
_ultimo_estado = {}        # sensor -> ultimo evento "estado" (para que un navegador que recien conecta sepa el punto verde/rojo YA mismo)
_ultimo_bloqueos = None    # ultimo evento "bloqueos" (idem; si no se espera a la proxima vuelta del sondeo, hasta 3 s en blanco)


def _formatear(tipo, datos):
    return f"event: {tipo}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"


def conectar_cliente():
    """Registra una cola nueva y le manda de entrada el ultimo estado/bloqueos conocido (ver `difundir`). Devuelve
    la cola; quien la use debe sacarla de `_clientes` al desconectarse (ver `_sse`)."""
    q = queue.Queue(maxsize=500)
    with _lock_clientes:
        _clientes.append(q)
        for estado in _ultimo_estado.values():
            q.put_nowait(_formatear("estado", estado))
        if _ultimo_bloqueos is not None:
            q.put_nowait(_formatear("bloqueos", _ultimo_bloqueos))
    return q


def difundir(tipo, datos):
    """Manda un evento SSE a todos los navegadores conectados ahora mismo, y recuerda el ultimo "estado"/"bloqueos"
    para dárselo de entrada a quien se conecte despues (si no, un cambio que ya paso nunca le llega: ve el punto
    rojo para siempre aunque el sensor este bien, porque el aviso de "conectado" se manda una sola vez)."""
    global _ultimo_bloqueos
    if tipo == "estado":
        _ultimo_estado[datos["sensor"]] = datos
    elif tipo == "bloqueos":
        _ultimo_bloqueos = datos
    msg = _formatear(tipo, datos)
    with _lock_clientes:
        muertos = []
        for q in _clientes:
            try:
                q.put_nowait(msg)
            except queue.Full:
                muertos.append(q)          # un cliente lento se descarta antes que frenar a los demas
        for q in muertos:
            _clientes.remove(q)


# -------------------------------------------------------------------------------------------------- SSH (solo lectura)
def comando_ssh(ip, remoto):
    base = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
            f"{USUARIO}@{ip}", remoto]
    return (["sshpass", "-e"] + base) if SSHPASS else base


def comando_raiz(remoto):
    """`docker exec` falla para el usuario ubuntu sin sudo ("permission denied... docker.sock"): se envuelve el
    comando remoto en sudo, igual que repeticiones.py (Remoto.correr). La contrasena se manda por stdin, nunca
    como argumento (no queda en los logs de auditoria ni en `ps`)."""
    return f"sudo -S -p '' bash -c {shlex.quote(remoto)}"


def _entorno():
    env = dict(os.environ)
    if SSHPASS:
        env["SSHPASS"] = CLAVE
    return env


def siguiente_espera(espera):
    """Mismo backoff acotado que integrador.py: 1 s -> 30 s, duplicando cada vez que falla la conexion."""
    return min(espera * 2, 30.0) if espera else 1.0


# -------------------------------------------------------------------------------------------------- alertas en vivo
def hilo_alertas(sensor, ip):
    """Conexion SSH persistente: reenvia cada alerta nueva del eve-alerts.json de `sensor`, en cuanto se escribe.

    El punto verde/rojo NO depende de que llegue una alerta (si no hay ataque, `tail -F` no imprime nada): antes
    de abrir el stream se hace una comprobacion rapida (`echo OK`) para saber si el SSH funciona. El error real
    de ssh/docker (stderr) se imprime en la consola del tablero, para poder diagnosticar un parpadeo rojo/verde."""
    espera = 0.0
    while True:
        try:
            chk = subprocess.run(comando_ssh(ip, "echo OK"), capture_output=True, text=True, timeout=8, env=_entorno())
        except (OSError, subprocess.TimeoutExpired) as e:
            chk = None
            print(f"[{sensor}] no se pudo verificar la conexion: {e}")
        if not chk or chk.returncode != 0 or "OK" not in (chk.stdout or ""):
            if chk and (chk.stderr or "").strip():
                print(f"[{sensor}] ssh: {chk.stderr.strip().splitlines()[-1]}")
            difundir("estado", {"sensor": sensor, "conectado": False})
            espera = siguiente_espera(espera)
            time.sleep(espera)
            continue
        difundir("estado", {"sensor": sensor, "conectado": True})
        print(f"[{sensor}] conectado, escuchando el eve-alerts.json")
        espera = 0.0
        try:
            p = subprocess.Popen(comando_ssh(ip, comando_raiz(f"docker exec r5-suricata tail -n 0 -F {EVE}")),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  bufsize=1, env=_entorno())
            p.stdin.write(CLAVE + "\n")
            p.stdin.flush()
            p.stdin.close()
            for linea in p.stdout:
                alertas = lib.leer_alertas(linea)
                if not alertas and linea.strip():
                    # no parece una alerta de Suricata: puede ser el error de sudo/docker saliendo por stdout en vez
                    # de stderr (o texto suelto). Se imprime tal cual para no perder la pista de un fallo silencioso.
                    print(f"[{sensor}] (stdout, no es alerta) {linea.rstrip()}")
                for a in alertas:
                    difundir("alerta", {"sensor": sensor, "hora": time.strftime("%H:%M:%S", time.localtime(a["epoch"])),
                                         "src": a["src"], "dst": a["dst"], "sev": a["sev"], "firma": a["firma"]})
            p.wait()
            err = (p.stderr.read() or "").strip()
            # SIEMPRE se avisa que la conexion de este sensor se cerro, pase lo que pase (antes solo se avisaba si
            # habia un error; si el proceso remoto termina LIMPIO (codigo 0, sin nada en stderr) no se veia nada).
            print(f"[{sensor}] el tail se cerro (codigo {p.returncode})" + (f": {err.splitlines()[-1]}" if err else " (sin stderr)"))
        except OSError as e:
            print(f"[{sensor}] no se pudo lanzar ssh: {e}")
        difundir("estado", {"sensor": sensor, "conectado": False})
        espera = siguiente_espera(espera)
        time.sleep(espera)


# -------------------------------------------------------------------------------------------------- bloquear vs solo registrar
# El integrador SOLO deja un rastro en su log cuando de verdad bloquea (registrar_incidente); una alerta "solo
# registrada" (severidad > MAX_SEVERITY) no aparece en ningun log. Por eso la unica forma fiel de distinguirlas no
# es reimplementar el umbral de severidad del lado del tablero (podria desincronizarse de MAX_SEVERITY, la lista
# blanca o origen_suplantado), sino escuchar tambien esta linea del log y marcarla en vivo junto a las alertas.
LINEA_BLOQUEO_LOG = re.compile(r"BLOQUEO (?P<ip>\S+) en (?P<switches>.+?) \((?P<firma>[^)]*)\) flow=\S+ lectura->201=(?P<ms>[\d.]+) ms")


def hilo_bloqueo_log(sensor, ip):
    """Conexion SSH persistente al log del integrador: reenvia cada linea BLOQUEO (no toca el punto verde/rojo,
    que refleja solo la conexion de alertas de hilo_alertas)."""
    espera = 0.0
    while True:
        try:
            chk = subprocess.run(comando_ssh(ip, "echo OK"), capture_output=True, text=True, timeout=8, env=_entorno())
        except (OSError, subprocess.TimeoutExpired) as e:
            chk = None
            print(f"[{sensor}] log BLOQUEO: no se pudo verificar la conexion: {e}")
        if not chk or chk.returncode != 0 or "OK" not in (chk.stdout or ""):
            espera = siguiente_espera(espera)
            time.sleep(espera)
            continue
        print(f"[{sensor}] conectado, escuchando el log de BLOQUEO")
        espera = 0.0
        try:
            p = subprocess.Popen(comando_ssh(ip, comando_raiz("docker logs -f --since 0s r5-integrador 2>&1 | grep --line-buffered BLOQUEO")),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  bufsize=1, env=_entorno())
            p.stdin.write(CLAVE + "\n")
            p.stdin.flush()
            p.stdin.close()
            for linea in p.stdout:
                m = LINEA_BLOQUEO_LOG.search(linea)
                if m:
                    difundir("bloqueo_log", {"sensor": sensor, "hora": time.strftime("%H:%M:%S"), "ip": m["ip"],
                                              "switches": m["switches"], "firma": m["firma"], "ms": float(m["ms"])})
            p.wait()
        except OSError as e:
            print(f"[{sensor}] log BLOQUEO: no se pudo lanzar ssh: {e}")
        print(f"[{sensor}] log de BLOQUEO se cerro")
        espera = siguiente_espera(espera)
        time.sleep(espera)


# -------------------------------------------------------------------------------------------------- bloqueos (DROP activos)
LINEA_BLOQUEO = re.compile(r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<switch>\S+)\s+(?P<prioridad>\d+)\s+(?P<paquetes>\d+)\s+(?P<vida>\d+)\s*s\s+(?P<vence>.+)$")


def parsear_bloqueos(texto):
    """Filas de `r5ctl bloqueos` (una tabla de texto) a una lista de dict; ignora la cabecera y lineas que no calcen."""
    filas = []
    for linea in (texto or "").splitlines():
        m = LINEA_BLOQUEO.match(linea.strip())
        if m:
            d = m.groupdict()
            filas.append({"ip": d["ip"], "switch": d["switch"], "prioridad": int(d["prioridad"]), "paquetes": int(d["paquetes"]),
                          "vida_s": int(d["vida"]), "vence": d["vence"].strip()})
    return filas


def consultar_bloqueos():
    for ip in SENSORES.values():            # cualquier sensor sirve: ambos leen el mismo estado en ONOS
        try:
            p = subprocess.run(comando_ssh(ip, comando_raiz("docker exec r5-integrador r5ctl bloqueos")),
                                input=CLAVE + "\n", capture_output=True, text=True, timeout=10, env=_entorno())
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0:
            return parsear_bloqueos(p.stdout)
        if (p.stderr or "").strip():
            print(f"[bloqueos] {ip}: {p.stderr.strip().splitlines()[-1]}")
    return None     # los dos sensores fallaron: no se difunde nada (se conserva lo ultimo que vio el navegador)


def hilo_bloqueos():
    while True:
        filas = consultar_bloqueos()
        if filas is not None:
            difundir("bloqueos", {"total": len(filas), "filas": filas})
        time.sleep(INTERVALO_BLOQUEOS)


# -------------------------------------------------------------------------------------------------- desbloquear (UNICA accion de escritura)
IP_VALIDA = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def ejecutar_desbloqueo(ips):
    """Desbloquea cada IP (una llamada a `r5ctl desbloquear` por IP, en una sola sesion SSH). `ips` debe salir
    SIEMPRE de `_ultimo_bloqueos` (lo que el propio tablero ya vio bloqueado), nunca de texto libre del navegador:
    es la unica accion de escritura del tablero, y solo retira bloqueos existentes, nunca instala nada nuevo.
    Devuelve (ok, salida)."""
    ips = [ip for ip in ips if IP_VALIDA.match(ip)]
    if not ips:
        return True, "nada que desbloquear"
    cmd = "; ".join(f"docker exec r5-integrador r5ctl desbloquear {ip}" for ip in ips)
    for ip_sensor in SENSORES.values():
        try:
            p = subprocess.run(comando_ssh(ip_sensor, comando_raiz(cmd)), input=CLAVE + "\n",
                                capture_output=True, text=True, timeout=15, env=_entorno())
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"[desbloquear] {ip_sensor}: {e}")
            continue
        if p.returncode == 0:
            return True, p.stdout
        print(f"[desbloquear] {ip_sensor}: codigo {p.returncode}: {(p.stderr or '').strip()[-300:]}")
    return False, "no se pudo conectar a ningun sensor"


def refrescar_bloqueos():
    """Pide el estado a ONOS de una vez (no esperar los hasta INTERVALO_BLOQUEOS s del sondeo periodico) y lo difunde."""
    filas = consultar_bloqueos()
    if filas is not None:
        difundir("bloqueos", {"total": len(filas), "filas": filas})


# -------------------------------------------------------------------------------------------------- pagina y servidor HTTP
INDEX_HTML = """<!doctype html>
<html lang="es" data-bs-theme="light">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tablero R5 (solo lectura)</title>
<link rel="stylesheet" href="/static/tabler.min.css">
<style>
  body{{padding:1rem}}
  .columna{{height:60vh;overflow-y:auto;font-family:monospace;font-size:.82rem}}
  .columna div{{border-bottom:1px solid var(--tblr-border-color);padding:.15rem 0}}
  .sev1{{color:#d63939;font-weight:bold}} .sev2{{color:#f76707}}
  .bloqueada{{color:#2fb344;background:#e9f7ec;padding:.1rem .3rem;border-radius:.25rem;display:inline-block}}
  .punto{{width:.6rem;height:.6rem;border-radius:50%;display:inline-block;margin-right:.35rem}}
  .arriba{{background:#2fb344}} .abajo{{background:#d63939}}
</style>
</head>
<body>
<div class="container-xl">
  <h2 class="mb-1">Tablero R5 <span class="badge bg-secondary-lt">solo lectura</span></h2>
  <p class="text-secondary mb-3">Alertas de Suricata en vivo y bloqueos activos (ONOS). No instala ni borra nada.</p>
  <div class="row">
    <div class="col-6">
      <div class="card"><div class="card-header"><span id="p-node-sec-1" class="punto abajo"></span>
        <strong>node-sec-1</strong> <span class="text-secondary">({ip1})</span>
        <button class="btn btn-sm btn-outline-secondary float-end" onclick="limpiar('a-node-sec-1')">Limpiar</button></div>
        <div class="card-body columna" id="a-node-sec-1"></div></div>
    </div>
    <div class="col-6">
      <div class="card"><div class="card-header"><span id="p-node-sec-2" class="punto abajo"></span>
        <strong>node-sec-2</strong> <span class="text-secondary">({ip2})</span>
        <button class="btn btn-sm btn-outline-secondary float-end" onclick="limpiar('a-node-sec-2')">Limpiar</button></div>
        <div class="card-body columna" id="a-node-sec-2"></div></div>
    </div>
  </div>
  <div class="card mt-3">
    <div class="card-header"><strong>Bloqueos activos (DROP en ONOS)</strong>
      <span class="text-secondary" id="b-total"></span>
      <button class="btn btn-sm btn-outline-danger float-end" onclick="desbloquearTodo()">Desbloquear todo</button></div>
    <div class="card-body">
      <table class="table table-sm table-vcenter">
        <thead><tr><th>IP bloqueada</th><th>switch</th><th>prioridad</th><th>paquetes</th><th>vida</th><th>vence</th><th></th></tr></thead>
        <tbody id="b-filas"></tbody>
      </table>
    </div>
  </div>
</div>
<script>
function limpiar(id) {{ document.getElementById(id).innerHTML = ""; }}
function agregar(id, html) {{
  const c = document.getElementById(id);
  const div = document.createElement("div"); div.innerHTML = html;
  c.appendChild(div);
  while (c.children.length > 300) c.removeChild(c.firstChild);
  c.scrollTop = c.scrollHeight;
}}
const es = new EventSource("/eventos");
es.addEventListener("alerta", e => {{
  const a = JSON.parse(e.data);
  const clase = a.sev <= 1 ? "sev1" : (a.sev === 2 ? "sev2" : "");
  agregar("a-" + a.sensor, `<span class="${{clase}}">${{a.hora}} ${{a.src}} -&gt; ${{a.dst}} sev ${{a.sev}} ${{a.firma}}</span>`);
}});
es.addEventListener("estado", e => {{
  const s = JSON.parse(e.data);
  document.getElementById("p-" + s.sensor).className = "punto " + (s.conectado ? "arriba" : "abajo");
}});
es.addEventListener("bloqueo_log", e => {{
  const b = JSON.parse(e.data);
  agregar("a-" + b.sensor, `<strong class="bloqueada">&#10003; BLOQUEADA ${{b.ip}} en ${{b.switches}} &mdash; ${{b.firma}} (${{b.ms.toFixed(1)}} ms)</strong>`);
}});
let ultimasIPs = [];
es.addEventListener("bloqueos", e => {{
  const b = JSON.parse(e.data);
  document.getElementById("b-total").textContent = b.total + " IP bloqueada(s)";
  ultimasIPs = [...new Set(b.filas.map(f => f.ip))];
  document.getElementById("b-filas").innerHTML = b.filas.map(f =>
    `<tr><td>${{f.ip}}</td><td>${{f.switch}}</td><td>${{f.prioridad}}</td><td>${{f.paquetes}}</td><td>${{f.vida_s}} s</td><td>${{f.vence}}</td>` +
    `<td><button class="btn btn-sm btn-outline-secondary" onclick="desbloquearIP('${{f.ip}}')">Quitar</button></td></tr>`
  ).join("");
}});

async function accionDesbloqueo(url, confirmar) {{
  if (!confirm(confirmar)) return;
  try {{
    const r = await fetch(url, {{method: "POST"}});
    const j = await r.json();
    if (!j.ok) alert("No se pudo desbloquear: " + (j.error || j.salida || "sin detalle"));
  }} catch (e) {{
    alert("No se pudo contactar al tablero: " + e);
  }}
}}
function desbloquearTodo() {{
  if (ultimasIPs.length === 0) {{ alert("No hay ninguna IP bloqueada ahora mismo."); return; }}
  accionDesbloqueo("/accion/desbloquear-todo", `Esto desbloquea las ${{ultimasIPs.length}} IP de la tabla ahora mismo. Continuar?`);
}}
function desbloquearIP(ip) {{
  accionDesbloqueo("/accion/desbloquear/" + encodeURIComponent(ip), `Desbloquear ${{ip}} ahora mismo?`);
}}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass   # el tablero es efimero (se prende y se apaga para la demo); no hace falta un log en disco

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            cuerpo = INDEX_HTML.format(ip1=html.escape(SENSORES["node-sec-1"]), ip2=html.escape(SENSORES["node-sec-2"])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/eventos":
            self._sse()
        elif self.path.startswith("/static/"):
            self._estatico(self.path[len("/static/"):])
        elif self.path == "/salud":
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"ok")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        """UNICA accion de escritura del tablero: desbloquear. Nunca instala nada nuevo; las IP salen SIEMPRE de
        lo que el propio tablero ya vio bloqueado (_ultimo_bloqueos), nunca de lo que mande el navegador."""
        conocidas = {f["ip"] for f in (_ultimo_bloqueos or {}).get("filas", [])}
        if self.path == "/accion/desbloquear-todo":
            ok, salida = ejecutar_desbloqueo(sorted(conocidas))
        elif self.path.startswith("/accion/desbloquear/"):
            ip = urllib.parse.unquote(self.path[len("/accion/desbloquear/"):])
            if ip not in conocidas:
                self._json(400, {"ok": False, "error": "esa IP no aparece en los bloqueos conocidos del tablero"})
                return
            ok, salida = ejecutar_desbloqueo([ip])
        else:
            self.send_response(404); self.end_headers()
            return
        refrescar_bloqueos()        # para que el navegador vea el resultado de inmediato, sin esperar el sondeo periodico
        self._json(200 if ok else 502, {"ok": ok, "salida": salida[-2000:]})

    def _json(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _estatico(self, nombre):
        ruta = os.path.normpath(os.path.join(STATIC_PORTAL, nombre))
        if not ruta.startswith(STATIC_PORTAL) or not os.path.isfile(ruta):
            self.send_response(404); self.end_headers(); return
        tipo = "text/css" if ruta.endswith(".css") else "application/octet-stream"
        with open(ruta, "rb") as f:
            cuerpo = f.read()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = conectar_cliente()
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = ": keep-alive\n\n"     # comentario SSE: mantiene viva la conexion a traves de proxies/NAT
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock_clientes:
                if q in _clientes:
                    _clientes.remove(q)


def main():
    global CLAVE
    CLAVE = leer_clave()
    if not SSHPASS:
        print("AVISO: falta sshpass (sudo apt-get install -y sshpass); se intentara con claves SSH.")
    for sensor, ip in SENSORES.items():
        threading.Thread(target=hilo_alertas, args=(sensor, ip), daemon=True).start()
        threading.Thread(target=hilo_bloqueo_log, args=(sensor, ip), daemon=True).start()
    threading.Thread(target=hilo_bloqueos, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PUERTO), Handler)
    print(f"Tablero R5 (solo lectura) en http://<IP de switch-borde>:{PUERTO}/   (Ctrl+C para apagarlo)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
