#!/usr/bin/env python3
"""Mide los tres criterios cuantitativos de R5 que faltaban, desde switch-borde (igual que repeticiones.py: pide la contraseña de ubuntu una vez).

  fp        Tasa de falsos positivos POR ALERTAS: tráfico mixto (normal + malicioso) desde vm-atacante, se cuentan las alertas de Suricata
            y se separan las que apuntan al origen malicioso (verdaderas) de las que apuntan al tráfico legítimo (falsas). Incluye, opcionalmente,
            el caso de 40 usuarios tras un NAT.
  escala    Escalabilidad de eventos: K orígenes maliciosos a la vez (10, 25, 50, 100...), cuántos se bloquean, en cuánto tiempo y con qué latencia.
  recursos  Consumo de CPU y memoria de Suricata y del integrador y ancho de banda del espejo, en reposo y bajo ataque (ejecuta medir_recursos.py en los sensores).

    python3 R5/deploy/medicion_r5.py fp --dry-run                     # muestra lo que haría, sin tocar ninguna VM
    python3 R5/deploy/medicion_r5.py fp -n 5 --duracion 120 --nat 3
    python3 R5/deploy/medicion_r5.py escala --origenes 10,25,50,100 -n 3
    python3 R5/deploy/medicion_r5.py recursos --fase-s 60 --ifaces ens4

Requisitos: sshpass en switch-borde, scapy y hping3 en vm-atacante, los sensores con los contenedores r5-suricata y r5-integrador activos.
Los orígenes falsos de `escala` y `recursos` son IPs de 203.0.113.104 en adelante (hasta 150): tienen que estar en el rango externo que el espejo copia al IDS, o Suricata
no los vería. Se desbloquean solos antes de cada corrida y, si algo se interrumpe, sus bloqueos caducan a los 300 s.
La contraseña de ubuntu se lee de R5_PASS o de ~/.r5_pass; solo se pregunta si no existe ninguno.
"""
import argparse
import csv
import getpass
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medicion_lib as lib  # noqa: E402
from repeticiones import ATACANTE, DESTINO, IP_ATACANTE, SENSORES, USUARIO, Remoto, leer_clave, limpiar, parsear_bloqueos  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
EVE = "/var/log/suricata/eve-alerts.json"


class RemotoSimulado:
    """Para --dry-run: imprime los comandos y no toca ninguna VM."""

    def __init__(self):
        self.clave = ""
        self.sshpass = None

    def correr(self, ip, script, timeout=90):
        print(f"    [simulado] {ip}: {script[:220]}{'...' if len(script) > 220 else ''}")
        return 0, ""


def subir(rem, ip, local, remoto):
    """Copia un archivo local a `remoto` en la VM `ip` (sin sudo)."""
    if isinstance(rem, RemotoSimulado):
        print(f"    [simulado] subir {os.path.basename(local)} -> {ip}:{remoto}")
        return True
    ssh = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8",
           f"{USUARIO}@{ip}", f"mkdir -p {shlex.quote(os.path.dirname(remoto))} && cat > {shlex.quote(remoto)}"]
    cmd = ([rem.sshpass, "-e"] + ssh) if rem.sshpass else ssh
    with open(local, "rb") as f:
        p = subprocess.run(cmd, stdin=f, capture_output=True, timeout=60, env=dict(os.environ, SSHPASS=rem.clave))
    return p.returncode == 0


def lineas_eve(rem, ip):
    """Numero de lineas del eve-alerts.json de un sensor (para leer luego solo lo nuevo)."""
    rc, out = rem.correr(ip, f"docker exec r5-integrador sh -c 'wc -l < {EVE}'")
    nums = re.findall(r"^\s*(\d+)\s*$", out or "", re.M)
    return int(nums[-1]) if nums else 0


def alertas_nuevas(rem, ip, desde_linea):
    rc, out = rem.correr(ip, f"docker exec r5-integrador sh -c 'tail -n +{desde_linea + 1} {EVE}'")
    return lib.leer_alertas(out or "")


def bloqueos_recientes(rem, ip, ventana_s, desde):
    rc, logs = rem.correr(ip, f"docker logs --since {int(ventana_s)}s r5-integrador 2>&1 | grep BLOQUEO; true")
    return [(ts, ipb, ms, ip) for ts, ipb, ms in parsear_bloqueos(logs or "", desde)]


def leer_verdad(texto):
    """Filas del CSV de verdad de trafico_mixto.py (se filtran avisos de sudo u otros textos mezclados)."""
    lineas = [l for l in (texto or "").splitlines() if l.startswith("epoch,") or re.match(r"^\d+\.\d+,(normal|malo),", l)]
    return list(csv.DictReader(io.StringIO("\n".join(lineas)))) if lineas else []


def pct(x):
    return "-" if x is None else f"{x:.1f} %"


# ------------------------------------------------------------------------------------------------------------------ fp
def codigo_nat(destino, clientes, conexiones, puerto=80):
    """Script de Python (para ejecutar en vm-atacante) que simula `clientes` usuarios legitimos tras una misma IP, cada uno con `conexiones` conexiones HTTP."""
    return f"""python3 - <<'PY'
import socket, threading
def cli():
    for _ in range({conexiones}):
        try:
            s = socket.create_connection(("{destino}", {puerto}), timeout=2)
            s.sendall(b"GET / HTTP/1.1\\r\\nHost: x\\r\\nUser-Agent: Mozilla/5.0\\r\\nConnection: close\\r\\n\\r\\n")
            s.recv(256); s.close()
        except OSError:
            pass
ts = [threading.Thread(target=cli) for _ in range({clientes})]
[t.start() for t in ts]; [t.join() for t in ts]
PY"""


def cmd_fp(rem, a):
    subir(rem, ATACANTE, os.path.join(AQUI, "trafico_mixto.py"), "/tmp/trafico_mixto.py")
    print(f"\n=== Falsos positivos por alertas · {a.n} corridas de {a.duracion} s con {a.pct_malo} % de tráfico malicioso ===")
    corridas, filas, falsos_bloqueos = [], [], 0
    for i in range(1, a.n + 1):
        ip_malo = f"203.0.113.{150 + i}"
        limpiar(rem, [ip_malo, IP_ATACANTE])
        antes = {s: lineas_eve(rem, ip) for s, ip in SENSORES.items()}
        t0 = time.time()
        rc, out = rem.correr(ATACANTE, f"cd /tmp && python3 trafico_mixto.py --destino {DESTINO} --duracion {a.duracion} --pct-malo {a.pct_malo} "
                                       f"--src-malo {ip_malo} --semilla {i} --csv /tmp/mixto_{i}.csv >/tmp/mixto_{i}.log 2>&1; cat /tmp/mixto_{i}.csv",
                             timeout=a.duracion + 120)
        verdad = leer_verdad(out)
        time.sleep(4)
        alertas, bloqueos = [], []
        for s, ip in SENSORES.items():
            alertas += alertas_nuevas(rem, ip, antes[s])
            bloqueos += bloqueos_recientes(rem, ip, a.duracion + 150, t0 - 5)
        c = lib.clasificar(alertas, ip_malo, IP_ATACANTE)
        malo_ev = sum(1 for v in verdad if v["etiqueta"] == "malo")
        normal_ev = len(verdad) - malo_ev
        fb = any(b[1] == IP_ATACANTE for b in bloqueos)
        falsos_bloqueos += fb
        corridas.append(c)
        est, _ = lib.tasa_falsos_positivos(c)
        print(f"  #{i}: eventos {normal_ev} normales / {malo_ev} maliciosos · alertas {c['total']} (verdaderas {c['verdaderas']}, falsas {c['falsas']}, "
              f"otras {c['otras']}) · FP {pct(est)} · IP legítima bloqueada: {'SÍ' if fb else 'no'}")
        filas.append({"escenario": "mixto", "rep": i, "eventos_normales": normal_ev, "eventos_maliciosos": malo_ev, "alertas": c["total"],
                      "verdaderas": c["verdaderas"], "falsas": c["falsas"], "otras": c["otras"], "ip_legitima_bloqueada": int(fb)})
        limpiar(rem, [ip_malo, IP_ATACANTE])
    tot = lib.sumar_clasificaciones(corridas)
    est, cons = lib.tasa_falsos_positivos(tot)
    print(f"\n  TOTAL mixto: {tot['total']} alertas · verdaderas {tot['verdaderas']} · falsas {tot['falsas']} · otras {tot['otras']}")
    print(f"  Tasa de falsos positivos: {pct(est)} (solo alertas sobre tráfico legítimo) · {pct(cons)} (contando también las no atribuibles)")
    print(f"  Corridas en que se bloqueó la IP legítima: {falsos_bloqueos} de {a.n}")
    print("  Por firma:")
    for sid, f in sorted(tot["por_firma"].items(), key=lambda kv: -sum(kv[1][k] for k in ("verdaderas", "falsas", "otras"))):
        print(f"    {sid} {f['firma'][:44]:44} verdaderas {f['verdaderas']:>4}  falsas {f['falsas']:>4}  otras {f['otras']:>4}")

    if a.nat:
        print(f"\n=== Caso NAT: {a.clientes} usuarios legítimos tras una misma IP, {a.conexiones} conexiones HTTP cada uno · {a.nat} repeticiones ===")
        bloqueadas = 0
        for j in range(1, a.nat + 1):
            limpiar(rem, [IP_ATACANTE])
            antes = {s: lineas_eve(rem, ip) for s, ip in SENSORES.items()}
            t0 = time.time()
            rem.correr(ATACANTE, codigo_nat(DESTINO, a.clientes, a.conexiones), timeout=120)
            time.sleep(4)
            alertas, bloqueos = [], []
            for s, ip in SENSORES.items():
                alertas += alertas_nuevas(rem, ip, antes[s])
                bloqueos += bloqueos_recientes(rem, ip, 150, t0 - 5)
            c = lib.clasificar(alertas, "0.0.0.0", IP_ATACANTE)
            fb = any(b[1] == IP_ATACANTE for b in bloqueos)
            bloqueadas += fb
            print(f"  #{j}: {c['falsas']} alertas falsas · IP del NAT bloqueada: {'SÍ' if fb else 'no'}")
            filas.append({"escenario": "nat", "rep": j, "eventos_normales": a.clientes * a.conexiones, "eventos_maliciosos": 0, "alertas": c["total"],
                          "verdaderas": 0, "falsas": c["falsas"], "otras": c["otras"], "ip_legitima_bloqueada": int(fb)})
            limpiar(rem, [IP_ATACANTE])
        print(f"  El NAT se bloqueó en {bloqueadas} de {a.nat} repeticiones.")
    return filas


# ------------------------------------------------------------------------------------------------------------------ escala
def cmd_escala(rem, a):
    print(f"\n=== Escalabilidad de eventos: K orígenes simultáneos × 40 SYN, {a.n} corridas por K ===")
    filas = []
    for k in [int(x) for x in a.origenes.split(",")]:
        try:
            ips = lib.origenes_externos(k)                                     # 203.0.113.104 ...: dentro del rango externo que el espejo copia al IDS
        except ValueError as e:
            sys.exit(f"--origenes: {e}")
        res = []
        for rep in range(1, a.n + 1):
            limpiar(rem, ips)                                                  # se reutilizan los mismos origenes: hay que desbloquearlos antes de cada corrida
            rc, out = rem.correr(ATACANTE, f't0=$(date +%s.%N); for i in $(seq {lib.ORIGEN_INICIO} {lib.ORIGEN_INICIO + k - 1}); do '
                                           f'hping3 -S -p 8101 -a 203.0.113.$i -i u30000 -c 40 {DESTINO} >/dev/null 2>&1 & done; wait; echo T0=$t0', timeout=180)
            m = re.search(r"T0=([\d.]+)", out or "")
            if not m:
                print(f"  K={k} #{rep}: no se obtuvo la hora de inicio ({(out or '')[-100:]})")
                continue
            t0 = float(m.group(1))
            time.sleep(3 + k * 0.05)
            bloqueos = []
            for s, ip in SENSORES.items():
                bloqueos += bloqueos_recientes(rem, ip, 300, t0 - 1)
            r = lib.resumen_escala(bloqueos, ips, t0)
            res.append(r)
            print(f"  K={k:>3} #{rep}: bloqueadas {r['bloqueadas']}/{r['esperadas']} · primero a los {r['primero_s'] if r['primero_s'] is None else round(r['primero_s'], 2)} s, "
                  f"último a los {r['ultimo_s'] if r['ultimo_s'] is None else round(r['ultimo_s'], 2)} s · "
                  f"{'-' if r['bloqueos_por_s'] is None else round(r['bloqueos_por_s'], 1)} bloqueos/s · "
                  f"mitigación p50/p95/máx " + "/".join("-" if r[c] is None else f"{r[c]:.1f}" for c in ("mitigacion_p50_ms", "mitigacion_p95_ms", "mitigacion_max_ms")) + " ms")
            filas.append(dict(r, origenes=k, rep=rep))
            time.sleep(a.pausa)
    limpiar(rem, lib.origenes_externos(max(int(x) for x in a.origenes.split(","))))    # al terminar no debe quedar ningún DROP de la prueba
    print("\n=== RESUMEN (media de las corridas) ===")
    print(f"{'K':>4} {'bloqueadas':>11} {'último bloqueo (s)':>19} {'bloqueos/s':>11} {'mitigación p95 (ms)':>20}")
    for k in sorted({f["origenes"] for f in filas}):
        g = [f for f in filas if f["origenes"] == k]
        med = lambda c: (sum(x[c] for x in g if x[c] is not None) / max(1, sum(1 for x in g if x[c] is not None))) if any(x[c] is not None for x in g) else None
        print(f"{k:>4} {100 * sum(x['bloqueadas'] for x in g) / sum(x['esperadas'] for x in g):>10.0f}% {med('ultimo_s') or 0:>19.2f} {med('bloqueos_por_s') or 0:>11.1f} {med('mitigacion_p95_ms') or 0:>20.1f}")
    return filas


# ------------------------------------------------------------------------------------------------------------------ recursos
def cmd_recursos(rem, a):
    destino_med = "/tmp/r5med"
    for ip in SENSORES.values():
        subir(rem, ip, os.path.join(AQUI, "medicion_lib.py"), f"{destino_med}/medicion_lib.py")
        subir(rem, ip, os.path.join(AQUI, "medir_recursos.py"), f"{destino_med}/medir_recursos.py")
    subir(rem, ATACANTE, os.path.join(AQUI, "trafico_mixto.py"), "/tmp/trafico_mixto.py")
    d = a.fase_s
    estimulos = {
        "reposo": None,
        # UDP al puerto 53 desde la IP real del atacante: el espejo lo copia al IDS (origen en 203.0.113.0/24) pero ninguna regla de R5 lo marca, asi que mide el espejo sin bloqueos
        "espejo": f"timeout {d - 3} hping3 --udp -p 53 -d 200 -i u{int(1e6 / a.pps)} {DESTINO} >/dev/null 2>&1; true",
        # ciclos de `origenes` orígenes nuevos cada vez (203.0.113.104 en adelante); se detiene al agotar el rango, porque un origen ya bloqueado no genera mas carga
        "ddos": (f"fin=$(( $(date +%s) + {d - 8} )); c=0; while [ $(date +%s) -lt $fin ] && [ $c -lt {lib.MAX_ORIGENES // a.origenes} ]; do "
                 f"base=$(( {lib.ORIGEN_INICIO} + c * {a.origenes} )); c=$((c+1)); "
                 f"for i in $(seq $base $((base + {a.origenes} - 1))); do hping3 -S -p 8101 -a 203.0.113.$i -i u30000 -c 40 {DESTINO} >/dev/null 2>&1 & done; wait; sleep 2; done; true"),
        "mixto": f"cd /tmp && python3 trafico_mixto.py --destino {DESTINO} --duracion {d - 5} --pct-malo 20 --src-malo 203.0.113.190 --semilla 1 --csv /tmp/mixto_rec.csv >/dev/null 2>&1; true",
    }
    resultados = {}
    for fase in a.fases.split(","):
        if fase not in estimulos:
            sys.exit(f"fase desconocida: {fase} (usa {', '.join(estimulos)})")
        print(f"\n--- fase «{fase}» ({d} s) ---")
        for s, ip in SENSORES.items():
            rem.correr(ip, f"nohup python3 {destino_med}/medir_recursos.py --fase {fase} --duracion {d} --ifaces {a.ifaces} "
                           f"--salida {destino_med}/{fase}_{s}.csv > {destino_med}/{fase}_{s}.json 2>&1 &")
        if estimulos[fase]:
            rem.correr(ATACANTE, estimulos[fase], timeout=d + 60)
        time.sleep(d + 6 if not estimulos[fase] else 6)            # sin estimulo hay que esperar la fase completa; con estimulo ya casi termino
        for s, ip in SENSORES.items():
            rc, out = rem.correr(ip, f"cat {destino_med}/{fase}_{s}.json", timeout=30)
            m = re.search(r"\{.*\}", out or "", re.S)
            if m:
                try:
                    resultados[(fase, s)] = json.loads(m.group(0))
                except ValueError:
                    pass
        if fase == "ddos":                                          # quita los DROP que dejó la fase (hasta 150 orígenes)
            limpiar(rem, lib.origenes_externos(lib.MAX_ORIGENES))
        time.sleep(3)
    print("\n=== RESUMEN (media / máximo) ===")
    print(f"{'fase':8} {'sensor':11} {'CPU Suricata %':>16} {'CPU integrador %':>18} {'RAM Suricata MB':>16} {'RAM integr. MB':>15} {'espejo Mbit/s':>14} {'espejo pps':>11}")
    for (fase, s), r in resultados.items():
        x = r["resumen"]
        f = lambda c: f"{x[c]['media']:.1f}/{x[c]['max']:.1f}" if x.get(c, {}).get("media") is not None else "-"
        print(f"{fase:8} {s:11} {f('cpu_suricata'):>16} {f('cpu_integrador'):>18} {f('rss_suricata_mb'):>16} {f('rss_integrador_mb'):>15} {f('espejo_mbps'):>14} {f('espejo_pps'):>11}")
    return resultados


# ------------------------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nombre in ("fp", "escala", "recursos"):
        p = sub.add_parser(nombre)
        p.add_argument("--dry-run", action="store_true", help="muestra los comandos sin ejecutarlos en las VM")
    fp = sub.choices["fp"]
    fp.add_argument("-n", type=int, default=5, help="corridas de tráfico mixto (por defecto 5)")
    fp.add_argument("--duracion", type=int, default=120, help="segundos por corrida")
    fp.add_argument("--pct-malo", type=int, default=20)
    fp.add_argument("--nat", type=int, default=0, help="repeticiones del caso NAT (0 = no se prueba)")
    fp.add_argument("--clientes", type=int, default=40)
    fp.add_argument("--conexiones", type=int, default=6)
    es = sub.choices["escala"]
    es.add_argument("--origenes", default="10,25,50,100", help="valores de K separados por coma (1 a 150)")
    es.add_argument("-n", type=int, default=3, help="corridas por K")
    es.add_argument("--pausa", type=float, default=5.0)
    rc = sub.choices["recursos"]
    rc.add_argument("--fase-s", type=int, default=60, help="segundos por fase")
    rc.add_argument("--fases", default="reposo,espejo,ddos,mixto")
    rc.add_argument("--ifaces", default="ens4", help="NIC de espejo del sensor (IDS_IFACES de R5/deploy/.env)")
    rc.add_argument("--pps", type=int, default=1000, help="paquetes por segundo de la fase «espejo»")
    rc.add_argument("--origenes", type=int, default=30, help="orígenes por ciclo en la fase «ddos» (1 a 150)")
    a = ap.parse_args()
    if a.dry_run:
        rem = RemotoSimulado()
    else:
        rem = Remoto(leer_clave())
        if not rem.sshpass:
            print("AVISO: falta sshpass (sudo apt-get install -y sshpass); se intentará con claves SSH.")
    print(f"Medición R5 · {a.cmd} · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    res = {"fp": cmd_fp, "escala": cmd_escala, "recursos": cmd_recursos}[a.cmd](rem, a)
    if a.cmd in ("fp", "escala") and res and not a.dry_run:
        ruta = os.path.expanduser(f"~/r5_{a.cmd}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            campos = sorted({k for r in res for k in r})
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(res)
        print(f"\nCSV: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
