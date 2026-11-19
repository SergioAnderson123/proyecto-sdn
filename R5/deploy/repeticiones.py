#!/usr/bin/env python3
"""Repeticiones de los ataques EXTERNOS de R5 desde vm-atacante, con tabla de resultados (tasa de detección, tiempos medios).

Se ejecuta en switch-borde (como verificar_r5.py). Pide la contraseña de ubuntu una vez (no se guarda).

    python3 R5/deploy/repeticiones.py                    # 10 repeticiones de cada escenario
    python3 R5/deploy/repeticiones.py -n 3 --solo scan   # prueba corta de un escenario
    python3 R5/deploy/repeticiones.py --lista            # nombres de escenarios

Por cada repetición: 1) desbloquea al atacante (r5ctl), 2) anota la hora del primer paquete EN el atacante (date +%s.%N),
3) lanza el ataque, 4) lee de los dos sensores las líneas `BLOQUEO` del integrador posteriores a esa hora.
  detección  = hora del primer BLOQUEO - hora de inicio del ataque (incluye el arranque de la herramienta)
  mitigación = `lectura->201` del integrador (alerta -> DROP aceptado por ONOS)
Supone relojes sincronizados entre las VM (en el slice lo estaban: diferencias de milisegundos).
Deja un CSV con todas las repeticiones en ~/r5_repeticiones_<fecha>.csv.
"""
import argparse
import calendar
import csv
import getpass
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time

USUARIO = "ubuntu"
ATACANTE = "10.20.11.203"          # ens3 de vm-atacante (DHCP de VNRT); alcanzable desde switch-borde (10.20.11.14, misma red)
SENSORES = {"node-sec-1": "192.168.201.31", "node-sec-2": "192.168.201.32"}
IP_ATACANTE = "203.0.113.103"
DESTINO = "10.0.0.10"

# nombre: (descripción, script que se ejecuta en el atacante, IPs que deben quedar bloqueadas)
ESCENARIOS = {
    "scan": ("Escaneo SYN rápido (nmap -sS -T4)", f"nmap -Pn -sS -T4 --min-rate 300 -p 1-300 {DESTINO}", [IP_ATACANTE]),
    "flood": ("SYN flood (hping3, 2000 SYN)", f"hping3 -S -p 8101 -i u1000 -c 2000 {DESTINO}", [IP_ATACANTE]),
    "fin": ("Escaneo sigiloso FIN (nmap -sF)", f"nmap -Pn -sF -T4 --min-rate 300 -p 1-300 {DESTINO}", [IP_ATACANTE]),
    "ddos": ("DDoS distribuido (10 orígenes, 40 SYN c/u)",
             f"for i in $(seq 101 110); do hping3 -S -p 8101 -a 203.0.113.$i -i u30000 -c 40 {DESTINO} >/dev/null 2>&1 & done; wait",
             [f"203.0.113.{i}" for i in range(101, 111)]),
}

LINEA_BLOQUEO = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3}) INFO BLOQUEO (\S+) .*?lectura->201=([\d.]+) ms")


def a_epoch(fecha, hora, ms):
    """Hora del log del integrador (UTC en los contenedores) a segundos desde la época."""
    t = time.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M:%S")
    return calendar.timegm(t) + int(ms) / 1000.0


def parsear_bloqueos(texto, desde):
    """[(epoch, ip, ms_mitigacion)] de las líneas BLOQUEO posteriores a `desde` (epoch)."""
    res = []
    for linea in texto.splitlines():
        m = LINEA_BLOQUEO.match(linea.strip())
        if not m:
            continue
        ts = a_epoch(m.group(1), m.group(2), m.group(3))
        if ts >= desde:
            res.append((ts, m.group(4), float(m.group(5))))
    return res


def resumen(valores):
    if not valores:
        return "-", "-", "-"
    return f"{statistics.mean(valores):.0f}", f"{min(valores):.0f}", f"{max(valores):.0f}"


class Remoto:
    def __init__(self, clave):
        self.clave = clave
        self.sshpass = shutil.which("sshpass")

    def correr(self, ip, script, timeout=90):
        """Ejecuta `script` como root (sudo -S) en `ip`. Devuelve (rc, salida)."""
        ssh = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8",
               f"{USUARIO}@{ip}", f"sudo -S -p '' bash -c {shlex.quote(script)}"]
        cmd = ([self.sshpass, "-e"] + ssh) if self.sshpass else ssh
        env = dict(os.environ, SSHPASS=self.clave)
        try:
            p = subprocess.run(cmd, input=self.clave + "\n", capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return None, "tiempo agotado"
        return p.returncode, (p.stdout + "\n" + p.stderr).strip()


def limpiar(rem, ips):
    """Quita los bloqueos de R5 de las IP del escenario (desde node-sec-1) y deja que ONOS asiente."""
    cmd = "; ".join(f"docker exec r5-integrador r5ctl desbloquear {ip} >/dev/null 2>&1" for ip in ips)
    rem.correr(SENSORES["node-sec-1"], cmd + "; true")
    time.sleep(3)


def una_repeticion(rem, escenario):
    desc, ataque, esperadas = ESCENARIOS[escenario]
    limpiar(rem, esperadas)
    rc, out = rem.correr(ATACANTE, f't0=$(date +%s.%N); {ataque} >/dev/null 2>&1; echo T0=$t0', timeout=120)
    m = re.search(r"T0=([\d.]+)", out or "")
    if not m:
        return {"ok": False, "error": f"no se obtuvo la hora de inicio ({out[-120:] if out else rc})"}
    t0 = float(m.group(1))
    time.sleep(2)
    bloqueos = []
    for s, ip in SENSORES.items():
        rc, logs = rem.correr(ip, "docker logs --since 60s r5-integrador 2>&1 | grep BLOQUEO; true")
        bloqueos += [(ts, ipb, ms, s) for ts, ipb, ms in parsear_bloqueos(logs or "", t0 - 1)]
    bloqueadas = {b[1] for b in bloqueos if b[1] in esperadas}
    if not bloqueos:
        return {"ok": True, "detectado": False, "t0": t0, "bloqueadas": 0, "esperadas": len(esperadas)}
    primero = min(bloqueos)
    return {"ok": True, "detectado": True, "t0": t0, "deteccion_ms": (primero[0] - t0) * 1000, "mitigacion_ms": primero[2],
            "sensor": primero[3], "bloqueadas": len(bloqueadas), "esperadas": len(esperadas)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=10, help="repeticiones por escenario (por defecto 10)")
    ap.add_argument("--solo", choices=list(ESCENARIOS), action="append", help="solo este escenario (repetible)")
    ap.add_argument("--lista", action="store_true")
    ap.add_argument("--pausa", type=float, default=4.0, help="segundos de espera entre repeticiones")
    a = ap.parse_args()
    if a.lista:
        for k, (d, _, _) in ESCENARIOS.items():
            print(f"{k:6} {d}")
        return 0
    clave = os.environ.get("R5_PASS") or getpass.getpass(f"Contraseña de {USUARIO} (SSH y sudo de las VM; no se guarda): ")
    rem = Remoto(clave)
    if not rem.sshpass:
        print("AVISO: falta sshpass (sudo apt-get install -y sshpass); se intentará con claves SSH.")
    nombres = a.solo or list(ESCENARIOS)
    filas, tabla = [], []
    print(f"Repeticiones de ataques externos · {time.strftime('%Y-%m-%d %H:%M:%S')} · n={a.n} · ejecutado en {os.uname().nodename if hasattr(os, 'uname') else '?'}")
    for nombre in nombres:
        desc = ESCENARIOS[nombre][0]
        print(f"\n=== {desc} ===")
        res = []
        for i in range(1, a.n + 1):
            r = una_repeticion(rem, nombre)
            r["escenario"], r["rep"] = nombre, i
            res.append(r)
            if not r["ok"]:
                print(f"  #{i:>2}  ERROR {r['error']}")
            elif r["detectado"]:
                print(f"  #{i:>2}  detectado en {r['deteccion_ms']:7.0f} ms · alerta->DROP {r['mitigacion_ms']:5.1f} ms · "
                      f"bloqueadas {r['bloqueadas']}/{r['esperadas']} · primer sensor {r['sensor']}")
            else:
                print(f"  #{i:>2}  NO detectado")
            time.sleep(a.pausa)
        filas += res
        validas = [r for r in res if r["ok"]]
        det = [r for r in validas if r["detectado"]]
        tabla.append((desc, len(validas), len(det), resumen([r["deteccion_ms"] for r in det]), resumen([r["mitigacion_ms"] for r in det]),
                      statistics.mean(r["bloqueadas"] / r["esperadas"] for r in det) * 100 if det else 0))
        limpiar(rem, ESCENARIOS[nombre][2])
    print("\n=== RESUMEN ===")
    print(f"{'Escenario':46} {'n':>3} {'detect.':>8} {'tasa':>6} | {'detección ms (media/mín/máx)':>30} | {'alerta->DROP ms':>22} | {'IP bloq.':>8}")
    for desc, n, d, dt, mt, ip in tabla:
        tasa = f"{100 * d / n:.0f}%" if n else "-"
        print(f"{desc:46} {n:>3} {d:>8} {tasa:>6} | {'/'.join(dt):>30} | {'/'.join(mt):>22} | {ip:>7.0f}%")
    archivo = os.path.expanduser(f"~/r5_repeticiones_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    with open(archivo, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=["escenario", "rep", "ok", "detectado", "t0", "deteccion_ms", "mitigacion_ms", "sensor", "bloqueadas", "esperadas", "error"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)
    print(f"\nCSV con todas las repeticiones: {archivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
