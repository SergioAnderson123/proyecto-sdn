"""Mide la admisión REAL de usuarios en R2, desde DENTRO del contenedor mininet (sin la sobrecarga de `docker exec`).

Por cada rol (docente h1, alumno h2, soporte h3) repite N veces: cierra la sesión (POST /sessions/logout, el mismo aviso que
envía el agente del host; requiere AGENTE_TOKEN en el entorno de este script y del Policy Mapper), mata y vuelve a lanzar
wpa_supplicant y mide con un reloj monotónico:
  - sesión : hasta que el Policy Mapper registra la sesión (EAP-TLS + RADIUS + LDAP + webhook + flujo 40001)
  - acceso : hasta que el primer servicio permitido responde 200 (incluye ARP y el flujo en el switch)

Uso (dentro del contenedor):  python3 /root/shared-r2/admision_r2.py [N] [rol ...]     (por defecto N=10 y los tres roles)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

MAPPER = "http://policy_mapper:5000"
USUARIOS = [("h1", "docente", "profesor01@example.org", "00-00-00-00-00-11", "wpa_profe_01"),
            ("h2", "alumno", "alumno02@example.org", "00-00-00-00-00-22", "wpa_alumno_02"),
            ("h3", "soporte", "support01@example.org", "00-00-00-00-00-31", "wpa_support_01")]
SERVICIO = ("10.0.0.40", 8001)   # virtual_library: permitido a los tres roles


def mnx(host, *cmd):
    return subprocess.run(["bash", "/root/shared-host/mnx.sh", host, *cmd], capture_output=True, text=True, timeout=30)


def sesiones():
    try:
        return json.load(urllib.request.urlopen(MAPPER + "/sessions", timeout=3))["sessions"]
    except Exception:
        return {}


def cerrar(usuario, mac_radius):
    r = urllib.request.Request(MAPPER + "/sessions/logout", method="POST", data=json.dumps({"mac": mac_radius}).encode(),
                               headers={"Content-Type": "application/json", "X-Agente-Token": os.environ.get("AGENTE_TOKEN", "")})
    urllib.request.urlopen(r, timeout=5).read()


def esperar(cond, limite, paso):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limite:
        if cond():
            return True
        time.sleep(paso)
    return False


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    roles = sys.argv[2:] or [u[1] for u in USUARIOS]
    ip, puerto = SERVICIO
    resumen = []
    for host, rol, usuario, mac_radius, conf in [u for u in USUARIOS if u[1] in roles]:
        mac = mac_radius.replace("-", ":")
        ses, acc, fallos = [], [], 0
        for i in range(1, n + 1):
            mnx(host, "pkill", "wpa_supplicant")
            try:
                cerrar(usuario, mac_radius)
            except Exception:
                pass
            esperar(lambda: mac not in sesiones(), 5, 0.1)
            time.sleep(1.0)
            t0 = time.monotonic()
            mnx(host, "wpa_supplicant", "-i", f"{host}-eth0", "-D", "wired", "-c", f"/root/shared-host/supplicants/{conf}.conf",
                "-B", "-f", f"/tmp/wpa_{host}.log")
            if not esperar(lambda: mac in sesiones(), 40, 0.1):
                fallos += 1
                print(f"{rol:<8} {i:>2}/{n}  NO se admitió en 40 s", flush=True)
                continue
            t_ses = (time.monotonic() - t0) * 1000
            ok = esperar(lambda: mnx(host, "curl", "-s", "-m", "1", "-o", "/dev/null", "-w", "%{http_code}",
                                     f"http://{ip}:{puerto}/").stdout.strip() == "200", 20, 0.0)
            t_acc = (time.monotonic() - t0) * 1000 if ok else float("nan")
            ses.append(t_ses)
            if ok:
                acc.append(t_acc)
            print(f"{rol:<8} {i:>2}/{n}  sesión activa a los {t_ses:>6.0f} ms   primer servicio responde a los {t_acc:>6.0f} ms", flush=True)
        if ses:
            resumen.append((rol, len(ses), fallos, sum(ses) / len(ses), pct(ses, 50), pct(ses, 95), max(ses),
                            sum(acc) / max(1, len(acc)), pct(acc or [0], 95)))
    print(flush=True)
    print(f"{'rol':<9}{'ok':>4}{'fallos':>8}{'ses.media':>11}{'ses.p50':>9}{'ses.p95':>9}{'ses.max':>9}{'acc.media':>11}{'acc.p95':>9}   (ms)", flush=True)
    for r in resumen:
        print(f"{r[0]:<9}{r[1]:>4}{r[2]:>8}{r[3]:>11.0f}{r[4]:>9.0f}{r[5]:>9.0f}{r[6]:>9.0f}{r[7]:>11.0f}{r[8]:>9.0f}", flush=True)


if __name__ == "__main__":
    main()
