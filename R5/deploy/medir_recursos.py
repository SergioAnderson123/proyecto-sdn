#!/usr/bin/env python3
"""Mide en un sensor (nodo-sec-1 o nodo-sec-2) el consumo de CPU y memoria de Suricata y del integrador, y el ancho de banda del espejo.

Criterio del proyecto: "Consumo de recursos: nivel de CPU, memoria y ancho de banda utilizado por las funciones de deteccion/mitigacion".
Solo usa la biblioteca estandar y lee /proc y /sys, asi que no necesita permisos de Docker (los contenedores comparten el kernel del host).

    python3 medir_recursos.py --fase reposo --duracion 60 --ifaces ens4          # una fase de 60 s, una muestra por segundo
    python3 medir_recursos.py --fase ataque --duracion 90 --ifaces ens4,ens5 --salida ~/rec_ataque.csv

Guarda un CSV (una fila por muestra) y al final imprime un resumen en una linea JSON: media, p95 y maximo de cada magnitud.
  cpu_*  : % de UN nucleo (100 % = un nucleo completo; un sensor de 2 vCPU llega a 200 %)
  rss_*  : memoria residente en MB
  espejo : Mbit/s y paquetes/s recibidos por las NIC de espejo (lo que el espejo copia al sensor)
"""
import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medicion_lib as lib  # noqa: E402

PROC = os.environ.get("R5_PROC", "/proc")
SYS = os.environ.get("R5_SYS", "/sys")


def es_suricata(argv):
    """El proceso de Suricata: su ejecutable se llama suricata (no un bash cuyo texto lo mencione)."""
    return bool(argv) and os.path.basename(argv[0]) == "suricata"


def es_integrador(argv):
    """El integrador: un interprete de Python que ejecuta integrador.py."""
    return bool(argv) and "python" in os.path.basename(argv[0]) and any(x.endswith("integrador.py") for x in argv[1:])


PATRONES = {"suricata": es_suricata, "integrador": es_integrador}


def leer(ruta):
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def buscar_pids(criterio, proc=None):
    """PIDs cuyo comando (lista de argumentos) cumple `criterio` (se excluye este mismo proceso)."""
    proc = proc or PROC
    yo = str(os.getpid())
    res = []
    for d in os.listdir(proc):
        if not d.isdigit() or d == yo:
            continue
        cmd = leer(os.path.join(proc, d, "cmdline"))
        if cmd and criterio([x for x in cmd.split("\0") if x]):
            res.append(int(d))
    return res


def leer_proceso(pids, proc=None):
    """(jiffies de CPU, RSS en kB) sumados sobre los `pids`."""
    proc = proc or PROC
    jif = rss = 0
    for p in pids:
        st = leer(os.path.join(proc, str(p), "stat"))
        su = leer(os.path.join(proc, str(p), "status"))
        if st:
            jif += lib.jiffies_de_stat(st)
        if su:
            rss += lib.rss_kb_de_status(su)
    return jif, rss


def leer_nics(ifaces, sys_dir=None):
    """(bytes, paquetes) recibidos, sumados sobre las NIC de espejo."""
    sys_dir = sys_dir or SYS
    b = p = 0
    for i in ifaces:
        b += int(leer(os.path.join(sys_dir, "class", "net", i, "statistics", "rx_bytes")) or 0)
        p += int(leer(os.path.join(sys_dir, "class", "net", i, "statistics", "rx_packets")) or 0)
    return b, p


def muestrear(duracion, intervalo, ifaces, hz, alinear=None):
    """Lista de muestras (dicts), una por `intervalo` segundos durante `duracion` segundos."""
    pids = {k: buscar_pids(v) for k, v in PATRONES.items()}
    jif0 = {k: leer_proceso(v)[0] for k, v in pids.items()}
    nic0 = leer_nics(ifaces)
    t0 = time.time()
    muestras = []
    while time.time() - t0 < duracion:
        time.sleep(intervalo)
        t1 = time.time()
        dt = t1 - t0 if not muestras else t1 - muestras[-1]["epoch"]
        fila = {"epoch": round(t1, 3)}
        for k, v in pids.items():
            jif, rss = leer_proceso(v)
            fila[f"cpu_{k}"] = round(lib.cpu_porcentaje(jif0[k], jif, dt, hz), 2)
            fila[f"rss_{k}_mb"] = round(rss / 1024.0, 1)
            jif0[k] = jif
        nic1 = leer_nics(ifaces)
        fila["espejo_mbps"] = round(lib.mbps(nic0[0], nic1[0], dt), 3)
        fila["espejo_pps"] = round(max(nic1[1] - nic0[1], 0) / dt, 1) if dt > 0 else 0.0
        nic0 = nic1
        muestras.append(fila)
    return muestras, {k: len(v) for k, v in pids.items()}


def resumen(muestras):
    campos = [c for c in muestras[0] if c != "epoch"] if muestras else []
    return {c: {k: (round(v, 2) if v is not None else None) for k, v in lib.resumen_serie([m[c] for m in muestras]).items()} for c in campos}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fase", default="medicion", help="etiqueta de la fase (reposo, ataque...)")
    ap.add_argument("--duracion", type=float, default=60)
    ap.add_argument("--intervalo", type=float, default=1.0)
    ap.add_argument("--ifaces", default="ens4", help="NIC de espejo del sensor, separadas por coma (R5/deploy/.env: IDS_IFACES)")
    ap.add_argument("--salida", default=None, help="CSV de salida (por defecto ~/r5_recursos_<fase>_<fecha>.csv)")
    a = ap.parse_args()
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    ifaces = [i.strip() for i in a.ifaces.split(",") if i.strip()]
    muestras, hallados = muestrear(a.duracion, a.intervalo, ifaces, hz)
    ruta = os.path.expanduser(a.salida or f"~/r5_recursos_{a.fase}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    if muestras:
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["fase"] + list(muestras[0]))
            w.writeheader()
            for m in muestras:
                w.writerow(dict(m, fase=a.fase))
    print(json.dumps({"fase": a.fase, "muestras": len(muestras), "procesos_hallados": hallados, "csv": ruta, "resumen": resumen(muestras)}))
    if not hallados["suricata"] or not hallados["integrador"]:
        print("AVISO: no se encontro " + " ni ".join(k for k, v in hallados.items() if not v) + " entre los procesos; revisa que los contenedores esten activos.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
