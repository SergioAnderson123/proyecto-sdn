"""Demo en vivo del integrador R5 (M09) contra un ONOS simulado.

Parte 1: casos funcionales (C1-C5) con el integrador de la rama r5/integrador-llsd.
Parte 2: tiempo alerta -> POST, version de master (POLL_SEC 0.5) frente a la nueva (0.05).

Uso:  python demo_integrador.py
No toca ONOS real ni la red: el "ONOS" es un servidor HTTP local en el puerto 18190.
"""
import json
import os
import random
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(AQUI))   # raíz del repositorio (este archivo está en R5/pruebas/)
NUEVO = os.path.join(REPO, "R5", "codigo", "integrador.py")
VIEJO = os.path.join(AQUI, "integrador_master.py")
TRABAJO = os.path.join(AQUI, "trabajo")
os.makedirs(TRABAJO, exist_ok=True)
os.system("")  # activa colores ANSI en la consola de Windows

VERDE, ROJO, AMARILLO, CIAN, GRIS, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[90m", "\033[1m", "\033[0m")


def titulo(t):
    print(f"\n{NEGRITA}{CIAN}{'=' * 78}\n {t}\n{'=' * 78}{FIN}", flush=True)


def paso(t):
    print(f"\n{AMARILLO}>> {t}{FIN}", flush=True)


# ---------------- ONOS simulado ----------------
class Onos:
    def __init__(self, puerto):
        self.posts, self.fallos_503 = [], {}
        onos = self

        class H(BaseHTTPRequestHandler):
            def _r(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):   # GET /hosts: un solo host interno conocido, 10.0.0.21 en el borde s2
                print(f"{GRIS}   [ONOS] GET {self.path} -> 10.0.0.21 esta en of:0000000000000002 puerto 3{FIN}", flush=True)
                self._r(200, {"hosts": [{"ipAddresses": ["10.0.0.21"], "mac": "00:00:00:00:00:21",
                                         "locations": [{"elementId": "of:0000000000000002", "port": "3"}]}]})

            def do_POST(self):
                f = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["flows"][0]
                ip = f["selector"]["criteria"][1]["ip"]
                if onos.fallos_503.get(ip, 0) > 0:
                    onos.fallos_503[ip] -= 1
                    print(f"{ROJO}   [ONOS] POST {ip} -> 503 (simulo ONOS caido){FIN}", flush=True)
                    return self._r(503, {})
                onos.posts.append((time.time(), self.path, f))
                fid = str(1000 + len(onos.posts))
                print(f"{GRIS}   [ONOS] POST {self.path}  device={f['deviceId']} prio={f['priority']} "
                      f"timeout={f['timeout']} DROP {ip} -> 201 flowId={fid}{FIN}", flush=True)
                self._r(200, {"flows": [{"deviceId": f["deviceId"], "flowId": fid}]})

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", puerto), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{puerto}"

    def cerrar(self):
        self.srv.shutdown()
        self.srv.server_close()


def arrancar(script, onos, nombre, estado_inicial=None, silencioso=False):
    eve = os.path.join(TRABAJO, f"eve-{nombre}.json")
    st = os.path.join(TRABAJO, f"state-{nombre}.json")
    open(eve, "w").close()
    if estado_inicial is not None:
        json.dump(estado_inicial, open(st, "w"))
    elif os.path.exists(st):
        os.remove(st)
    env = dict(os.environ, EVE_PATH=eve, STATE_PATH=st, ONOS_URL=onos.url,
               ONOS_USER="demo", ONOS_PASS="demo", PYTHONUNBUFFERED="1")
    for k in ("POLL_SEC", "P_R5", "APP_ID", "HOME_NET", "LISTA_BLANCA", "DRY_RUN"):
        env.pop(k, None)
    p = subprocess.Popen([sys.executable, script], env=env,
                         stderr=subprocess.DEVNULL if silencioso else None)
    time.sleep(1)
    return p, eve, st


def alerta(src, dst, sev=2, direction="to_server", firma="LOCAL Port scan SYN"):
    return {"event_type": "alert", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000000+0000", time.gmtime()),
            "src_ip": src, "dest_ip": dst, "direction": direction,
            "alert": {"severity": sev, "signature_id": 9000002, "signature": firma}}


def escribir(eve, ev):
    with open(eve, "a") as f:
        f.write(json.dumps(ev) + "\n")


# ---------------- Parte 1 ----------------
def parte1():
    titulo("PARTE 1 - Casos funcionales del integrador nuevo (rama r5/integrador-llsd)")
    print("HOME_NET = 10.0.0.0/24 | lista blanca = 10.0.0.99, 10.0.0.10 | Core = of:0000000000000001")
    onos = Onos(18190)
    onos.fallos_503["10.0.1.66/32"] = 2
    viejo = {"bloqueadas": {"10.0.1.99": time.time() + 999}, "inode": None, "offset": 0}
    paso("Arranco el integrador con un state.json en formato antiguo (10.0.1.99 ya bloqueada)")
    p, eve, st = arrancar(NUEVO, onos, "funcional", estado_inicial=viejo)

    casos = [
        ("Atacante externo 10.0.1.10 escanea al servidor 10.0.0.10",
         alerta("10.0.1.10", "10.0.0.10"), ("10.0.1.10/32", "of:0000000000000001")),
        ("La misma IP vuelve a alertar (un escaneo genera ~180 alertas)",
         alerta("10.0.1.10", "10.0.0.10"), None),
        ("Alerta de severidad 3 (p. ej. LLDP de ONOS) desde 10.0.1.11",
         alerta("10.0.1.11", "10.0.0.10", sev=3, firma="Ethertype unknown"), None),
        ("Firma que dispara sobre la RESPUESTA (to_client): src=servidor 10.0.0.10, dst=atacante 10.0.1.12",
         alerta("10.0.0.10", "10.0.1.12", direction="to_client", firma="ET ATTACK_RESPONSE"), ("10.0.1.12/32", "of:0000000000000001")),
        ("Host interno 10.0.0.21 contacta a un C2 externo 10.0.1.50",
         alerta("10.0.0.21", "10.0.1.50", firma="ET MALWARE C2 beacon"), ("10.0.0.21/32", "of:0000000000000002")),
        ("Trafico interno <-> interno (10.0.0.21 -> 10.0.0.10): es R3, no R5",
         alerta("10.0.0.21", "10.0.0.10"), None),
        ("Atacante en lista blanca: 10.0.0.99 (sensor) hacia fuera",
         alerta("10.0.0.99", "10.0.1.70"), None),
        ("IP que ya estaba bloqueada en el state.json antiguo (10.0.1.99)",
         alerta("10.0.1.99", "10.0.0.10"), None),
        ("ONOS devuelve 503 dos veces para 10.0.1.66: debe reintentar sin perder la alerta",
         alerta("10.0.1.66", "10.0.0.10"), ("10.0.1.66/32", "of:0000000000000001")),
    ]
    resultados = []
    for i, (desc, ev, esperado) in enumerate(casos, 1):
        paso(f"Caso {i}: {desc}")
        print(f"{GRIS}   escribo en eve.json: src={ev['src_ip']} dst={ev['dest_ip']} "
              f"sev={ev['alert']['severity']} direction={ev['direction']}{FIN}", flush=True)
        antes = len(onos.posts)
        escribir(eve, ev)
        time.sleep(4.5 if esperado and esperado[0] == "10.0.1.66/32" else 1.5)
        nuevos = onos.posts[antes:]
        if esperado is None:
            ok = not nuevos
            detalle = "sin POST (correcto)" if ok else f"POST inesperado: {nuevos}"
        else:
            ok = len(nuevos) == 1 and nuevos[0][2]["selector"]["criteria"][1]["ip"] == esperado[0] \
                and nuevos[0][2]["deviceId"] == esperado[1] and nuevos[0][2]["priority"] == 42000 \
                and "appId=org.campus.r5" in nuevos[0][1]
            detalle = f"DROP {esperado[0]} en {esperado[1]}, prio 42000, appId org.campus.r5"
        print(f"   {VERDE + 'OK ' if ok else ROJO + 'FALLA'}{FIN} esperado: {detalle}", flush=True)
        resultados.append(ok)

    p.terminate()
    p.wait()
    paso("Estado guardado por el integrador (C4: flow_id y marcas de tiempo)")
    for ip, v in json.load(open(st))["bloqueadas"].items():
        if "t_201" in v:
            print(f"   {ip:<11} device={v['device']} flow_id={v['flow_id']} sid={v['sid']} "
                  f"lectura->201={(v['t_201'] - v['t_lectura']) * 1000:.1f} ms")
        else:
            print(f"   {ip:<11} (registro antiguo migrado: {v})")
    onos.cerrar()
    print(f"\n{NEGRITA}Parte 1: {sum(resultados)}/{len(resultados)} casos OK{FIN}")
    return all(resultados)


# ---------------- Parte 2 ----------------
def medir(script, nombre, puerto, n=15):
    onos = Onos(puerto)
    p, eve, st = arrancar(script, onos, nombre, silencioso=True)
    escritas = {}
    for i in range(n):
        time.sleep(random.uniform(0.3, 0.9))
        ip = f"10.0.1.{i + 100}"
        escribir(eve, alerta(ip, "10.0.0.10"))
        escritas[ip + "/32"] = time.time()
    time.sleep(1.5)
    p.terminate()
    p.wait()
    onos.cerrar()
    llegadas = {f["selector"]["criteria"][1]["ip"]: t for t, _, f in onos.posts}
    d = sorted((llegadas[ip] - t) * 1000 for ip, t in escritas.items() if ip in llegadas)
    return d


def parte2():
    titulo("PARTE 2 - Tiempo alerta escrita -> POST a ONOS (15 alertas en instantes aleatorios)")
    res = {}
    for script, nombre, etiqueta, puerto in ((VIEJO, "master", "master (POLL_SEC 0.5)", 18191),
                                            (NUEVO, "nuevo", "rama nueva (POLL_SEC 0.05)", 18192)):
        paso(f"Midiendo {etiqueta} ...")
        d = medir(script, nombre, puerto)
        media = sum(d) / len(d)
        res[etiqueta] = media
        print(f"   n={len(d)}  media={media:.1f} ms  min={d[0]:.1f}  p95={d[int(0.95 * (len(d) - 1))]:.1f}  max={d[-1]:.1f}")
        print("   " + " ".join(f"{x:.0f}" for x in d) + " ms")
    a, b = res.values()
    print(f"\n{NEGRITA}La espera media baja de {a:.0f} ms a {b:.0f} ms{FIN}")


if __name__ == "__main__":
    ok = parte1()
    parte2()
    print(f"\n{VERDE if ok else ROJO}{NEGRITA}Demo terminada.{FIN}")
