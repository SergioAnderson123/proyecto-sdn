"""Escenarios de ataque del laboratorio R5 (solo contra la red virtual de Mininet de este contenedor).

Uso (desde el anfitrión):
  docker exec r5-lab python3 /opt/r5/lab/docker/attacks.py <escenario>
Escenarios: scan  rafaga  multi  web  c2  nat  flood  r3  r4udp  r4icmp  todo
Cada escenario narra lo que hace y por qué en el panel (http://localhost:8080), deja un pcap del espejo
y un JSON con las mediciones en /evidencia.
"""
import json
import os
import re
import subprocess
import sys
import time

import requests

ONOS = os.environ["ONOS_URL"]
AUTH = (os.environ["ONOS_USER"], os.environ["ONOS_PASS"])
APP_ID = os.getenv("APP_ID", "org.campus.r5")
EVE = os.environ["EVE_PATH"]
STATE = os.environ["STATE_PATH"]
R5 = os.getenv("R5_DIR", "/opt/r5")
VNRT = os.getenv("VNRT") == "1"      # 1: ejecutar los ataques por SSH en las VM de VNRT (h1..h4) en vez de Mininet
LOG_R5 = os.getenv("LOG_R5", "/var/log/r5")
SURI_LOG = os.getenv("SURI_LOG", "/var/log/suricata")
SRV = os.getenv("SRV_IP", "10.0.0.10")
IP = {"hext1": "10.0.1.11", "hext2": "10.0.1.12", "hext3": "10.0.1.13", "hext4": "10.0.1.14",
      "hnat": "10.0.1.100", "hc2": "10.0.1.50", "hint": "10.0.0.21"}
# VNRT: los "externos" son alias IP de h4 (ens4); el atacante interno es h3; el servidor es h1
VM = {"hsrv": "192.168.0.21", "hint": "192.168.0.23", "hext1": "192.168.0.24", "hext2": "192.168.0.24", "hext3": "192.168.0.24",
      "hext4": "192.168.0.24", "hnat": "192.168.0.24", "hc2": "192.168.0.24"}
VM["hint2"] = "192.168.0.22"
if VNRT:
    IP = {"hext1": "10.0.0.101", "hext2": "10.0.0.102", "hext3": "10.0.0.103", "hext4": "10.0.0.104",
          "hnat": "10.0.0.110", "hc2": "10.0.0.120", "hint": "10.0.0.3", "hint2": "10.0.0.2"}
C2_URL = "http://%s:8080/" % IP["hc2"]
C2H = "hint2" if VNRT else "hint"       # VNRT: h3 y h4 cuelgan de sw3 y su tráfico no cruza el Core; h2 (sw2) sí
IFACE = "ens4" if VNRT else None
UA_NAVEGADOR = "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0"
resultados = {}
EVID = os.getenv("EVID_DIR", "/evidencia")


# ---------- utilidades ----------
def narrar(titulo, texto="", tipo="paso"):
    print(f"[{tipo}] {titulo} {texto}", flush=True)
    try:
        requests.post("http://127.0.0.1:8080/api/event", json={"titulo": titulo, "texto": texto, "tipo": tipo}, timeout=2)
    except requests.RequestException:
        pass


def cmd_en(host, cmd, tty=False):
    """Lista de argv para ejecutar `cmd` en `host`: `hx` de Mininet o SSH a la VM de VNRT (con la IP de origen del alias)."""
    if not VNRT:
        return ["hx", host] + cmd
    ip = IP.get(host)
    cmd = list(cmd)
    if host in VM and host != "hsrv" and ip and cmd:
        if cmd[0] == "nmap":
            cmd[1:1] = ["-S", ip, "-e", IFACE]
        elif cmd[0] == "curl":
            cmd[1:1] = ["--interface", ip]
        elif cmd[0] == "hping3":
            cmd[1:1] = ["-a", ip]
        elif cmd[0] == "bash" and len(cmd) > 2:
            cmd[2] = cmd[2].replace("curl ", f"curl --interface {ip} ")
    if cmd and cmd[0] in ("nmap", "hping3", "tcpdump"):
        cmd = ["timeout", "--foreground", "120", "sudo", "-n"] + cmd      # timeout: no deja tcpdump colgado si se corta el SSH
    import shlex
    return ["ssh"] + (["-tt"] if tty else []) + ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            f"ubuntu@{VM[host]}", shlex.join(cmd)]


def hx(host, cmd, timeout=120):
    try:
        r = subprocess.run(cmd_en(host, cmd), capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        return (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")


def curl(host, url, extra=()):
    """Código HTTP o 'sin respuesta' (3 s de espera)."""
    out = hx(host, ["curl", "-s", "-m", "3", "-A", UA_NAVEGADOR, "-o", "/dev/null", "-w", "%{http_code}"] + list(extra) + [url], 6).strip()
    return out if out and out != "000" else "sin respuesta"


def bloqueos():
    try:
        fl = requests.get(f"{ONOS}/flows/application/{APP_ID}", auth=AUTH, timeout=3).json().get("flows", [])
    except requests.RequestException:
        return {}
    res = {}
    for f in fl:
        ip = next((c["ip"].split("/")[0] for c in f["selector"]["criteria"] if c["type"] == "IPV4_SRC"), None)
        if ip:
            res[ip] = {"dev": f["deviceId"], "prio": f["priority"], "pkts": f.get("packets", 0), "vida": f.get("life", 0)}
    return res


def alertas_desde(t0, src=None):
    """Alertas de eve-alerts.json cuya hora de captura es posterior a t0 (epoch)."""
    out = []
    try:
        for l in open(EVE, encoding="utf8", errors="replace"):
            try:
                e = json.loads(l)
            except ValueError:
                continue
            ts = e.get("timestamp", "")
            m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)([+-]\d{4})", ts)
            if not m:
                continue
            seg = time.mktime(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) - time.timezone + float("0." + m.group(2))
            if seg >= t0 - 0.05 and (src is None or e.get("src_ip") == src):
                out.append((seg, e))
    except OSError:
        pass
    return out


def estado_integrador():
    time.sleep(6)   # el integrador guarda su estado cada 5 s
    try:
        return json.load(open(STATE)).get("bloqueadas", {})
    except (OSError, ValueError):
        return {}


def stats_suricata():
    """(paquetes, descartes) acumulados de la captura, desde stats.log."""
    try:
        txt = open(f"{SURI_LOG}/stats.log", errors="replace").read()
    except OSError:
        return None, None
    p = re.findall(r"capture.kernel_packets\s*\|\s*Total\s*\|\s*(\d+)", txt)
    d = re.findall(r"capture.kernel_drops\s*\|\s*Total\s*\|\s*(\d+)", txt)
    return (int(p[-1]) if p else None), (int(d[-1]) if d else (0 if p else None))


class _SinPcap:
    def terminate(self):
        pass


def iniciar_pcap(nombre):
    if VNRT:          # el IDS está en el controller y tcpdump necesita root: la evidencia es eve-alerts.json
        return _SinPcap()
    ruta = f"{EVID}/{nombre}.pcap"
    if os.path.exists(ruta):
        os.remove(ruta)
    p = subprocess.Popen(["hx", "hids", "tcpdump", "-Z", "root", "-i", "hids-eth0", "-U", "-w", ruta], stderr=subprocess.DEVNULL)
    time.sleep(1)
    return p


def reiniciar():
    narrar("Reinicio limpio", "borro los bloqueos de R5 en ONOS y reinicio el estado del integrador", "info")
    subprocess.run(["pkill", "-f", "codigo/integrador.py"])
    for _ in range(50):            # el integrador guarda su estado al terminar: hay que esperar a que acabe
        if subprocess.run(["pgrep", "-f", "codigo/integrador.py"], capture_output=True).returncode != 0:
            break
        time.sleep(0.1)
    for f in requests.get(f"{ONOS}/flows/application/{APP_ID}", auth=AUTH, timeout=5).json().get("flows", []):
        requests.delete(f"{ONOS}/flows/{f['deviceId']}/{f['id']}", auth=AUTH, timeout=5)
    for _ in range(50):            # ONOS borra de forma asíncrona: sin esto bloqueos() ve flujos del escenario anterior
        if not bloqueos():
            break
        time.sleep(0.1)
    estable, ult = 0.0, -1
    for _ in range(300):           # Suricata puede ir atrasado: se espera a que deje de escribir alertas viejas
        try:
            tam = os.stat(EVE).st_size
        except OSError:
            break
        estable = estable + 0.1 if tam == ult else 0.0
        ult = tam
        if estable >= 4:
            break
        time.sleep(0.1)
    # No se trunca eve-alerts.json (EveBox y Prometheus conservan el historial); el integrador arranca
    # con el offset al final del archivo para no reprocesar alertas de escenarios anteriores.
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    try:
        st_eve = os.stat(EVE)
        inodo, offset = st_eve.st_ino, st_eve.st_size
    except OSError:
        inodo, offset = None, 0
    json.dump({"bloqueadas": {}, "inode": inodo, "offset": offset}, open(STATE, "w"))
    log = open(f"{LOG_R5}/integrador.log", "ab")
    subprocess.Popen(["python3", f"{R5}/codigo/integrador.py"], stdout=log, stderr=log)
    time.sleep(1.5)
    requests.post("http://127.0.0.1:8080/api/event", json={"limpiar": True}, timeout=2)


def guardar(nombre, datos):
    resultados[nombre] = datos
    with open(f"{EVID}/resultados-{nombre}.json", "w") as f:
        json.dump(datos, f, indent=2, default=str)
    narrar("Medición guardada", json.dumps(datos, ensure_ascii=False, default=str)[:300], "ok")


def capturar_syn(host, origen, destino, ruta):
    """tcpdump de los SYN origen->destino en la interfaz de `host` (eth0)."""
    filtro = f"tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn and src host {origen} and dst host {destino}"
    if VNRT:      # sin archivos: se cuentan las líneas que tcpdump imprime por la salida estándar
        return subprocess.Popen(cmd_en(host, ["tcpdump", "-n", "-l", "-i", IFACE, filtro], tty=True), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if os.path.exists(ruta):
        os.remove(ruta)          # tcpdump (usuario "tcpdump") no sobrescribe un archivo anterior: sin esto se releería la captura vieja
    p = subprocess.Popen(["hx", host, "tcpdump", "-Z", "root", "-n", "-i", f"{host}-eth0", "-U", "-w", ruta, filtro], stderr=subprocess.DEVNULL)
    return p


def contar_vnrt(p):
    """Detiene el tcpdump remoto con Ctrl-C (por el pty) y cuenta los SYN impresos; así no se pierden líneas."""
    try:
        p.stdin.write("")
        p.stdin.flush()
        salida = p.communicate(timeout=15)[0] or ""
    except (OSError, subprocess.TimeoutExpired, ValueError):
        p.kill()
        salida = ""
    return len([l for l in salida.splitlines() if "Flags [S]" in l])


def contar_syn(ruta):
    out = subprocess.run(["tcpdump", "-n", "-r", ruta], capture_output=True, text=True).stdout
    return len([l for l in out.splitlines() if l.strip()])


def medir_paso(host_atacante, cmd, tag, timeout=40):
    """Ejecuta `cmd` en el atacante y cuenta los SYN enviados frente a los que llegaron al servidor."""
    marca = int(time.time() * 1000)
    env, rec = f"/tmp/{tag}-{marca}-enviados.pcap", f"/tmp/{tag}-{marca}-recibidos.pcap"
    p1 = capturar_syn(host_atacante, IP[host_atacante], SRV, env)
    p2 = capturar_syn("hsrv", IP[host_atacante], SRV, rec)
    time.sleep(1)
    t0 = time.time()
    salida = hx(host_atacante, cmd, timeout)
    t1 = time.time()
    time.sleep(1.5)
    if not VNRT:
        for p in (p1, p2):
            p.terminate()
    time.sleep(0.5)
    if VNRT:
        enviados, recibidos = contar_vnrt(p1), contar_vnrt(p2)
    else:
        enviados, recibidos = contar_syn(env), contar_syn(rec)
        for ruta in (env, rec):
            os.remove(ruta)
    return t0, t1, salida, enviados, recibidos


# ---------- escenarios ----------
def esc_scan():
    reiniciar()
    narrar("Escenario 1 · Escaneo SYN sostenido", "Un atacante externo (hext1, 10.0.1.11) recorre 1000 puertos del servidor a 100 paquetes/s. "
           "El Core copia el tráfico a Suricata; al llegar a 20 SYN en 5 s se dispara la regla 9000002 y el integrador pide un DROP a ONOS.")
    narrar("Antes del ataque", f"hext1 → servidor web: HTTP {curl('hext1', f'http://{SRV}/')}", "ok")
    pcap = iniciar_pcap("scan")
    narrar("Lanzando nmap", "nmap -sS --max-rate 100 -p 1-1000 (solo contra el laboratorio)")
    t0, t1, salida, enviados, recibidos = medir_paso(
        "hext1", ["nmap", "-n", "-Pn", "-sS", "--max-rate", "100", "--max-retries", "0", "-p", "1-1000", SRV], "scan")
    pcap.terminate()
    al = alertas_desde(t0, IP["hext1"])
    st = estado_integrador()
    b = bloqueos().get(IP["hext1"])
    d = {"escaneo_s": round(t1 - t0, 2), "syn_enviados": enviados, "syn_que_llegaron_al_servidor": recibidos,
         "pct_pasa": round(100 * recibidos / enviados, 1) if enviados else None,
         "primera_alerta_s": round(al[0][0] - t0, 3) if al else None, "n_alertas": len(al),
         "bloqueado_en": b["dev"] if b else None}
    v = st.get(IP["hext1"], {})
    if v.get("t_lectura") and v.get("t_201"):
        d["lectura_a_201_ms"] = round((v["t_201"] - v["t_lectura"]) * 1000, 1)
        if al:
            d["alerta_a_201_s"] = round(v["t_201"] - al[0][0], 3)
        d["primer_syn_a_201_s"] = round(v["t_201"] - t0, 3)
    guardar("scan", d)
    narrar("Después del ataque", f"hext1 → servidor web: HTTP {curl('hext1', f'http://{SRV}/')}  (bloqueado: el DROP está en el Core)", "mal")


def esc_rafaga():
    reiniciar()
    narrar("Escenario 2 · Barrido en ráfaga", "Un barrido muy rápido (5000 puertos, ≥ 2000 paquetes/s) termina antes de que el lazo detección + decisión + bloqueo complete (≈ 0,3–0,5 s). "
           "Es el límite de un IDS fuera de banda: corta lo que sigue, no el primer tramo.")
    pcap = iniciar_pcap("rafaga")
    t0, t1, salida, enviados, recibidos = medir_paso(
        "hext2", ["nmap", "-n", "-Pn", "-sS", "-T5", "--min-rate", "3000", "--max-retries", "0", "-p", "1-5000", SRV], "rafaga")
    pcap.terminate()
    d = {"duracion_s": round(t1 - t0, 2), "syn_enviados": enviados, "syn_que_llegaron_al_servidor": recibidos,
         "pct_pasa": round(100 * recibidos / enviados, 1) if enviados else None, "bloqueado": IP["hext2"] in bloqueos()}
    guardar("rafaga", d)
    narrar("Segundo barrido tras el bloqueo", "mismo ataque, ya con el DROP activo", "info")
    _, _, _, e2, r2 = medir_paso("hext2", ["nmap", "-n", "-Pn", "-sS", "-T4", "--max-retries", "0", "-p", "1-200", SRV], "rafaga2", 25)
    narrar("Resultado", f"{r2} de {e2} SYN llegaron al servidor: el origen quedó aislado", "ok")


def esc_multi():
    reiniciar()
    narrar("Escenario 3 · Cuatro atacantes a la vez", "hext1..hext4 escanean el servidor al mismo tiempo. Esperamos un DROP por cada IP "
           "(1 entrada de TCAM por atacante) sin perder alertas. Mide la escalabilidad por número de atacantes.")
    pcap = iniciar_pcap("multi")
    t0 = time.time()
    procs = [subprocess.Popen(cmd_en(h, ["nmap", "-n", "-sS", "--max-rate", "100", "--max-retries", "0", "-p", "1-1000", SRV]),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for h in ("hext1", "hext2", "hext3", "hext4")]
    visto = {}
    while time.time() - t0 < 25 and len(visto) < 4:
        for ip in bloqueos():
            visto.setdefault(ip, round(time.time() - t0, 2))
        time.sleep(0.2)
    for p in procs:
        p.terminate()
    pcap.terminate()
    guardar("multi", {"atacantes": 4, "bloqueados": len(visto), "segundos_hasta_cada_drop": visto})


def esc_web():
    reiniciar()
    narrar("Escenario 4 · Herramienta de ataque web", "Peticiones HTTP con el User-Agent 'sqlmap' (firma local 9000010). "
           "Demuestra que R5 no solo detecta escaneos: cualquier firma de severidad ≤ 2 dispara el bloqueo.")
    pcap = iniciar_pcap("web")
    t0 = time.time()
    for _ in range(3):
        curl("hext3", f"http://{SRV}/index.php?id=1", ["-A", "sqlmap/1.7 (https://sqlmap.org)"])
    time.sleep(2)
    pcap.terminate()
    al = [e for _, e in alertas_desde(t0, IP["hext3"])]
    guardar("web", {"alertas": sorted({e["alert"]["signature"] for e in al}), "bloqueado": IP["hext3"] in bloqueos(),
                    "http_despues": curl("hext3", f"http://{SRV}/")})


def esc_c2():
    reiniciar()
    narrar("Escenario 5 · Host interno comprometido (C2)", "hint (10.0.0.21) llama a un servidor externo con una cabecera de prueba (firma local 9000011). "
           "Aquí el atacante es INTERNO: el integrador consulta ONOS (GET /hosts) para ubicar el switch de borde y bloquea ahí al host.")
    narrar("Antes", f"hint → servidor interno: HTTP {curl(C2H, f'http://{SRV}/')} · hint → C2 externo: HTTP {curl(C2H, C2_URL)}", "ok")
    pcap = iniciar_pcap("c2")
    t0 = time.time()
    curl(C2H, C2_URL, ["-H", "X-R5-TEST-C2: beacon"])
    time.sleep(3)
    pcap.terminate()
    b = bloqueos().get(IP[C2H])
    d = {"bloqueado_en": b["dev"] if b else None, "prio": b["prio"] if b else None,
         "hint_a_c2_despues": curl(C2H, C2_URL),
         "hint_a_servidor_despues": curl(C2H, f"http://{SRV}/")}
    guardar("c2", d)
    narrar("Observación", "el DROP por IP de origen aísla TODO el tráfico IPv4 del host, también el interno. Ese es el motivo de I11: revocar además su sesión en R1/R2.", "info")


def esc_nat():
    reiniciar()
    narrar("Escenario 6 · Falso positivo: muchos clientes legítimos detrás de un NAT", "hnat representa a 40 usuarios reales tras una misma IP que abren una página web en 3 segundos. "
           "La regla '20 SYN en 5 s por IP' no distingue eso de un escaneo: es la observación #1 del jurado.")
    pcap = iniciar_pcap("nat")
    t0 = time.time()
    hx("hnat", ["bash", "-c", f"for i in $(seq 1 40); do curl -s -m 2 -A '{UA_NAVEGADOR}' -o /dev/null http://{SRV}/ & sleep 0.07; done; wait"], 30)
    time.sleep(3)
    pcap.terminate()
    al = [e for _, e in alertas_desde(t0, IP["hnat"])]
    bloq = IP["hnat"] in bloqueos()
    d = {"conexiones_legitimas": 40, "alertas": len(al), "firmas": sorted({e["alert"]["signature"] for e in al}),
         "bloqueado": bloq, "http_despues": curl("hnat", f"http://{SRV}/")}
    guardar("nat", d)
    if bloq:
        narrar("Conclusión", "tráfico 100 % legítimo terminó bloqueado: el umbral necesita calibración (ventana/umbral mayor, puertos distintos o lista blanca 'solo alerta' para NAT conocidos).", "mal")
    else:
        narrar("Conclusión", "el NAT no se bloqueó con estas condiciones: el umbral de 20 SYN/5 s no se cruzó", "ok")


def esc_flood():
    reiniciar()
    narrar("Escenario 7 · Inundación SYN y pérdida de captura", "hext4 envía ≈ 1000 SYN/s al puerto 80 (regla local 9000003: 200 SYN en 2 s). "
           "Se mide cuántos paquetes pierde Suricata (kernel_drops) mientras ocurre.")
    p0, d0 = stats_suricata()
    pcap = iniciar_pcap("flood")
    t0 = time.time()
    hx("hext4", ["hping3", "-S", "-p", "80", "-i", "u1000", "-c", "5000", SRV], 30)
    time.sleep(2)
    pcap.terminate()
    time.sleep(9)   # stats.log se actualiza cada 8 s
    p1, d1 = stats_suricata()
    al = [e for _, e in alertas_desde(t0, IP["hext4"])]
    d = {"alertas": len(al), "firmas": sorted({e["alert"]["signature"] for e in al}), "bloqueado": IP["hext4"] in bloqueos(),
         "paquetes_capturados": (p1 - p0) if None not in (p0, p1) else None,
         "descartes_suricata": (d1 - d0) if None not in (d0, d1) else None}
    guardar("flood", d)


def esc_r3():
    reiniciar()
    narrar("R3 · Ataque encubierto en la intranet", "hint (10.0.0.21, interno) escanea el servidor interno. El tráfico cruza el Core, "
           "lo copia la regla de espejo interno y Suricata dispara la regla 9000002.")
    narrar("Antes", f"hint → servidor: HTTP {curl('hint', f'http://{SRV}/')}", "ok")
    pcap = iniciar_pcap("r3")
    t0, t1, _, enviados, recibidos = medir_paso(
        "hint", ["nmap", "-n", "-Pn", "-sS", "--max-rate", "100", "--max-retries", "0", "-p", "1-1000", SRV], "r3", 25)
    pcap.terminate()
    al = alertas_desde(t0, IP["hint"])
    v = estado_integrador().get(IP["hint"], {})
    b = bloqueos().get(IP["hint"])
    d = {"syn_enviados": enviados, "syn_que_llegaron_al_servidor": recibidos,
         "pct_pasa": round(100 * recibidos / enviados, 1) if enviados else None,
         "primera_alerta_s": round(al[0][0] - t0, 3) if al else None, "n_alertas": len(al),
         "bloqueado_en": b["dev"] if b else None}
    if v.get("t_201"):
        d["primer_syn_a_201_s"] = round(v["t_201"] - t0, 3)
        d["lectura_a_201_ms"] = round((v["t_201"] - v["t_lectura"]) * 1000, 1)
    guardar("r3", d)
    narrar("Después", f"hint → servidor: HTTP {curl('hint', f'http://{SRV}/')}", "mal")


def esc_r4(tipo="udp"):
    reiniciar()
    narrar(f"R4 · Inundación {tipo.upper()} dentro de la intranet", "hint inunda al servidor con paquetes pequeños a máxima velocidad. "
           "Suricata mide la tasa por origen (300 UDP o 200 ICMP en 2 s) y el integrador bloquea en el borde del atacante.")
    cmd = (["hping3", "--udp", "-p", "5000", "-i", "u200", "-c", "20000", SRV] if tipo == "udp"
           else ["hping3", "--icmp", "-i", "u200", "-c", "20000", SRV])
    p0, d0 = stats_suricata()
    pcap = iniciar_pcap(f"r4-{tipo}")
    t0 = time.time()
    proc = subprocess.Popen(cmd_en("hint", cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    visto = None
    while time.time() - t0 < 20 and visto is None:
        if IP["hint"] in bloqueos():
            visto = round(time.time() - t0, 2)
        time.sleep(0.1)
    time.sleep(1)
    proc.terminate()
    if not VNRT:
        subprocess.run(["pkill", "-f", "hping3"])
    pcap.terminate()
    time.sleep(9)
    p1, d1 = stats_suricata()
    al = alertas_desde(t0, IP["hint"])
    v = estado_integrador().get(IP["hint"], {})
    d = {"tipo": tipo, "segundos_hasta_el_drop": visto, "n_alertas": len(al), "firmas": sorted({e["alert"]["signature"] for e in [a for _, a in al]}),
         "primera_alerta_s": round(al[0][0] - t0, 3) if al else None,
         "bloqueado_en": (bloqueos().get(IP["hint"]) or {}).get("dev"),
         "paquetes_capturados": (p1 - p0) if None not in (p0, p1) else None,
         "descartes_suricata": (d1 - d0) if None not in (d0, d1) else None}
    if v.get("t_201"):
        d["lectura_a_201_ms"] = round((v["t_201"] - v["t_lectura"]) * 1000, 1)
    guardar(f"r4-{tipo}", d)


def esc_scan_n(n=30):
    """Repite el escaneo n veces con la metodología única (un solo reloj, estado del integrador, pcap de SYN)."""
    import statistics
    filas = []
    for i in range(n):
        reiniciar()
        narrar(f"Repetición {i + 1}/{n}", "escaneo SYN a 100 pps contra el servidor (nmap se corta a los 5 s)", "paso")
        t0, t1, _, enviados, recibidos = medir_paso(
            "hext1", ["nmap", "-n", "-Pn", "-sS", "--max-rate", "100", "--max-retries", "0", "-p", "1-1000", SRV], "scann", 5)
        al = alertas_desde(t0, IP["hext1"])
        v = estado_integrador().get(IP["hext1"], {})
        fila = {"i": i + 1, "syn_enviados": enviados, "syn_llegaron": recibidos,
                "pct_pasa": round(100 * recibidos / enviados, 1) if enviados else None,
                "primera_alerta_s": round(al[0][0] - t0, 3) if al else None}
        if v.get("t_lectura") and v.get("t_201"):
            fila["lectura_a_201_ms"] = round((v["t_201"] - v["t_lectura"]) * 1000, 1)
            fila["primer_syn_a_201_s"] = round(v["t_201"] - t0, 3)
            if al:
                fila["alerta_a_201_ms"] = round((v["t_201"] - al[0][0]) * 1000, 1)
        filas.append(fila)
    resumen = {}
    for k in ("primera_alerta_s", "alerta_a_201_ms", "lectura_a_201_ms", "primer_syn_a_201_s", "pct_pasa", "syn_llegaron"):
        v = sorted(f[k] for f in filas if f.get(k) is not None)
        if v:
            resumen[k] = {"n": len(v), "media": round(statistics.mean(v), 3), "desv": round(statistics.pstdev(v), 3),
                          "min": v[0], "p50": v[len(v) // 2], "p95": v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))], "max": v[-1]}
    guardar("scan_n", {"n": n, "resumen": resumen, "filas": filas})


ESCENARIOS = {"scan": esc_scan, "rafaga": esc_rafaga, "multi": esc_multi, "web": esc_web, "c2": esc_c2, "nat": esc_nat, "flood": esc_flood,
              "r3": esc_r3, "r4udp": lambda: esc_r4("udp"), "r4icmp": lambda: esc_r4("icmp")}

if __name__ == "__main__":
    nombre = sys.argv[1] if len(sys.argv) > 1 else ""
    if nombre == "todo":
        for f in ESCENARIOS.values():
            f()
            time.sleep(3)
    elif nombre == "scan_n":
        esc_scan_n(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif nombre in ESCENARIOS:
        ESCENARIOS[nombre]()
    else:
        raise SystemExit(__doc__)
