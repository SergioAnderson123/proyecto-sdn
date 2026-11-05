"""Barrido de recursos de R2: limita CPU y RAM de ONOS y del Policy Mapper con `docker update` y repite una carga fija para ver
a partir de qué tamaño se degrada la admisión. Sirve para dimensionar las VM del Controller (informe del Laboratorio 6).

Carga fija por configuración:  (a) 3 admisiones simultáneas reales (h1, h2, h3), 3 repeticiones;  (b) 500 flujos de sesión
instalados de golpe en s1.  Métricas: tiempo de la última admisión, flujos/s, y fallos.

Uso:  python tests/barrido_r2.py            (restablece los límites al terminar)
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metricas_r2 as m  # noqa: E402

# (contenedor, opciones de docker update, etiqueta)
CONFIGS = [
    ("onos", ["--cpus", "8"], "ONOS 8 CPU (referencia)"),
    ("onos", ["--cpus", "4"], "ONOS 4 CPU"),
    ("onos", ["--cpus", "2"], "ONOS 2 CPU"),
    ("onos", ["--cpus", "1"], "ONOS 1 CPU"),
    ("onos", ["--cpus", "0.5"], "ONOS 0,5 CPU"),
    ("onos", ["--cpus", "0.25"], "ONOS 0,25 CPU"),
    ("onos", ["--cpus", "8", "--memory", "3g", "--memory-swap", "3g"], "ONOS 3 GB RAM"),
    ("onos", ["--cpus", "8", "--memory", "2g", "--memory-swap", "2g"], "ONOS 2 GB RAM"),
    ("onos", ["--cpus", "8", "--memory", "1700m", "--memory-swap", "1700m"], "ONOS 1,7 GB RAM"),
    ("policy_mapper", ["--cpus", "0.5"], "Mapper 0,5 CPU"),
    ("policy_mapper", ["--cpus", "0.1"], "Mapper 0,1 CPU"),
]


def actualizar(contenedor, opciones):
    return subprocess.run(["docker", "update", *opciones, contenedor], capture_output=True, text=True)


def restablecer():
    actualizar("onos", ["--cpus", "0", "--memory", "0", "--memory-swap", "-1"])
    actualizar("policy_mapper", ["--cpus", "0", "--memory", "0", "--memory-swap", "-1"])


def rafagas():
    out = subprocess.run(["docker", "exec", "mininet", "python3", "/root/shared-host/admision_concurrente_r2.py", "3", "3"],
                         capture_output=True, text=True, encoding="utf8", timeout=600).stdout
    ultimas, fallos = [], 0
    for l in out.splitlines():
        f = l.split()
        if len(f) == 6 and f[0] == "3" and f[1].isdigit():
            ultimas.append(float(f[3]))
            fallos += int(f[5])
        elif "ninguna" in l:
            fallos += 3
    return ultimas, fallos


def main():
    m.log("")
    m.log("== Barrido de CPU/RAM (3 admisiones simultáneas x3 + 500 flujos de sesión) ==")
    m.log(f"{'configuración':<26}{'última admisión (ms)':>22}{'500 flujos visibles':>21}{'flujos/s':>10}{'fallos':>8}{'RSS ONOS':>11}")
    filas = []
    try:
        for cont, opc, etiqueta in CONFIGS:
            r = actualizar(cont, opc)
            if r.returncode != 0:
                m.log(f"{etiqueta:<26}docker update falló: {r.stderr.strip()[:80]}")
                continue
            m.time.sleep(20)
            ultimas, fallos = rafagas()
            carga = m.carga([500])
            rec = m.recursos()
            adm = f"{sum(ultimas) / len(ultimas):.0f}" if ultimas else "—"
            vis = f"{carga[0][2]} ms" if carga and carga[0][2] else "falló"
            fps = carga[0][3] if carga and carga[0][3] else "—"
            m.log(f"{etiqueta:<26}{adm:>22}{vis:>21}{fps!s:>10}{fallos:>8}{rec.get('onos', ('?', '?'))[1]:>11}")
            filas.append((etiqueta, adm, vis, fps, fallos))
    finally:
        restablecer()
        m.log("límites restablecidos")


if __name__ == "__main__":
    main()
