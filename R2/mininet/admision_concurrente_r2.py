"""Admisiones SIMULTÁNEAS en R2: K usuarios inician sesión a la vez y se mide cuánto tarda cada uno y cuántas sesiones por
segundo se completan. Cinco usuarios reales de LDAP (cada uno con su MAC y puerto asignados); tres de ellos por interfaces macvlan:
  profesor01 h1-eth0 (…:11)   profesor02 h1-p2 (…:12)   alumno02 h2-eth0 (…:22)
  alumno01   h2-p1   (…:21)   support01 h3-eth0 (…:31)

Uso (dentro del contenedor mininet):  python3 /root/shared-r2/admision_concurrente_r2.py [REPETICIONES] [K ...]
Por defecto 5 repeticiones y niveles de concurrencia 1, 2, 3 y 5.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

MAPPER = "http://policy_mapper:5000"
#            host  interfaz   usuario                   MAC                  configuración
USUARIOS = [("h1", "h1-eth0", "profesor01@example.org", "00:00:00:00:00:11", "wpa_profe_01"),
            ("h2", "h2-eth0", "alumno02@example.org", "00:00:00:00:00:22", "wpa_alumno_02"),
            ("h3", "h3-eth0", "support01@example.org", "00:00:00:00:00:31", "wpa_support_01"),
            ("h1", "h1-p2", "profesor02@example.org", "00:00:00:00:00:12", "wpa_profe_02"),
            ("h2", "h2-p1", "alumno01@example.org", "00:00:00:00:00:21", "wpa_alumno_01")]
# alumno03 queda fuera: en una prueba aislada el mapper lo rechazó por device binding (MAC …:22); después autenticó bien, causa sin establecer


def mnx(host, *cmd):
    return subprocess.run(["bash", "/root/shared-host/mnx.sh", host, *cmd], capture_output=True, text=True, timeout=30)


def preparar():
    for host, iface, _, mac, _ in USUARIOS:
        if "-eth0" in iface:
            continue
        if mnx(host, "ip", "link", "show", iface).returncode != 0:
            mnx(host, "ip", "link", "add", "link", f"{host}-eth0", "name", iface, "type", "macvlan", "mode", "bridge")
        mnx(host, "ip", "link", "set", iface, "down")
        mnx(host, "ip", "link", "set", iface, "address", mac)      # siempre: la MAC debe ser la que LDAP asigna al usuario
        mnx(host, "ip", "link", "set", iface, "up")


def sesiones():
    try:
        return json.load(urllib.request.urlopen(MAPPER + "/sessions", timeout=3))["sessions"]
    except Exception:
        return {}


def cerrar(usuario, mac):
    r = urllib.request.Request(MAPPER + "/sessions/logout", method="POST", data=json.dumps({"mac": mac}).encode(),
                               headers={"Content-Type": "application/json", "X-Agente-Token": os.environ.get("AGENTE_TOKEN", "")})
    try:
        urllib.request.urlopen(r, timeout=5).read()
    except Exception:
        pass


def limpiar():
    for host, iface, usuario, mac, _ in USUARIOS:
        mnx(host, "pkill", "-f", f"/tmp/wpa_{iface}.log")
        cerrar(usuario, mac)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8 and any(mac in sesiones() for _, _, _, mac, _ in USUARIOS):
        time.sleep(0.2)
    time.sleep(1.5)


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def rafaga(k):
    grupo = USUARIOS[:k]
    limpiar()
    t0 = time.monotonic()
    for host, iface, _, _, conf in grupo:     # se lanzan todos juntos; wpa_supplicant -B vuelve de inmediato
        subprocess.Popen(["bash", "/root/shared-host/mnx.sh", host, "wpa_supplicant", "-i", iface, "-D", "wired", "-c",
                          f"/root/shared-host/supplicants/{conf}.conf", "-B", "-f", f"/tmp/wpa_{iface}.log"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    vistos = {}
    while time.monotonic() - t0 < 60 and len(vistos) < k:
        activas = sesiones()
        for _, _, _, mac, _ in grupo:
            if mac in activas and mac not in vistos:
                vistos[mac] = (time.monotonic() - t0) * 1000
        time.sleep(0.05)
    return sorted(vistos.values()), k - len(vistos)


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    niveles = [int(x) for x in sys.argv[2:]] or [1, 2, 3, 5]
    preparar()
    print(f"{'K':>2}{'rep':>5}{'primera':>9}{'última':>8}{'sesiones/s':>12}{'fallos':>8}   (ms desde que se lanzan los K supplicants)", flush=True)
    resumen = []
    for k in niveles:
        ultimas, medias, fallos = [], [], 0
        for r in range(1, reps + 1):
            tiempos, falta = rafaga(k)
            fallos += falta
            if tiempos:
                ultimas.append(tiempos[-1])
                medias.append(sum(tiempos) / len(tiempos))
                print(f"{k:>2}{r:>5}{tiempos[0]:>9.0f}{tiempos[-1]:>8.0f}{len(tiempos) / (tiempos[-1] / 1000):>12.2f}{falta:>8}", flush=True)
            else:
                print(f"{k:>2}{r:>5}  ninguna sesión", flush=True)
        if ultimas:
            resumen.append((k, sum(medias) / len(medias), sum(ultimas) / len(ultimas), pct(ultimas, 95), k / (sum(ultimas) / len(ultimas) / 1000), fallos))
    limpiar()
    print(flush=True)
    print(f"{'K':>2}{'media/usuario':>15}{'última media':>14}{'última p95':>12}{'sesiones/s':>12}{'fallos':>8}", flush=True)
    for k, m, u, p95, sps, f in resumen:
        print(f"{k:>2}{m:>15.0f}{u:>14.0f}{p95:>12.0f}{sps:>12.2f}{f:>8}", flush=True)


if __name__ == "__main__":
    main()
