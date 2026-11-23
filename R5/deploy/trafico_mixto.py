#!/usr/bin/env python3
"""Tráfico MIXTO para R5: paquetes normales y sospechosos intercalados, con un CSV de "verdad" (qué se mandó y cuándo).

Se ejecuta en vm-atacante (necesita root por scapy). Solo contra el laboratorio propio.

    sudo python3 trafico_mixto.py --destino 10.0.0.10 --duracion 120 --pct-malo 20
    sudo python3 trafico_mixto.py --destino 10.0.0.10 --src-malo 203.0.113.103   # lo malicioso sale de otra IP (el normal no se bloquea)
    python3 trafico_mixto.py --dry-run                                           # solo muestra el plan, no envía nada

Cada "tick" (cada --pausa s) se elige al azar:
  NORMAL (por defecto 80 %): GET HTTP con navegador, conexión TCP completa, ping, consulta DNS.
  MALO   (por defecto 20 %): ráfaga que cruza una regla de local.rules:
      escaneo SYN (9000002), FIN (9000040), NULL (9000041), Xmas (9000042), inundación SYN (9000003), User-Agent sqlmap (9000010).

Salida: ~/trafico_mixto_<fecha>.csv con (hora epoch, etiqueta normal|malo, tipo, origen, destino, paquetes, regla esperada).
Luego se compara con eve-alerts.json: alertas con sid esperado = aciertos; alertas en ventanas "normal" = falsos positivos.

Nota: si lo normal y lo malo salen de la MISMA IP, tras la primera detección R5 bloquea a esa IP y también se pierde lo normal
(daño colateral, útil para mostrarlo). Con --src-malo el origen malicioso es otro y lo normal sigue pasando.
"""
import argparse
import csv
import random
import socket
import sys
import time
import urllib.request

UA_NAVEGADOR = "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0"

# tipo: (descripción, sid esperado)
MALOS = {
    "scan_syn":  ("Escaneo SYN a 120 puertos", 9000002),
    "scan_fin":  ("Escaneo FIN a 60 puertos", 9000040),
    "scan_null": ("Escaneo NULL a 60 puertos", 9000041),
    "scan_xmas": ("Escaneo Xmas a 60 puertos", 9000042),
    "syn_flood": ("Inundación SYN, 400 paquetes al puerto 80", 9000003),
    "web_sqlmap": ("GET HTTP con User-Agent sqlmap", 9000010),
}
NORMALES = ("http_get", "tcp_completo", "ping", "dns")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--destino", default="10.0.0.10")
    ap.add_argument("--puerto-web", type=int, default=80, help="puerto HTTP del destino (normal y sqlmap)")
    ap.add_argument("--duracion", type=int, default=120, help="segundos totales")
    ap.add_argument("--pausa", type=float, default=0.5, help="segundos entre eventos")
    ap.add_argument("--pct-malo", type=int, default=20, help="porcentaje de eventos maliciosos (0-100)")
    ap.add_argument("--src-malo", default=None, help="IP de origen para lo malicioso (por defecto, la propia)")
    ap.add_argument("--solo-malo", nargs="*", choices=list(MALOS), help="limita los ataques a estos tipos")
    ap.add_argument("--semilla", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv", default=time.strftime("trafico_mixto_%Y%m%d_%H%M%S.csv"))
    a = ap.parse_args()
    if a.semilla is not None:
        random.seed(a.semilla)
    malos = a.solo_malo or list(MALOS)

    if not a.dry_run:
        try:
            from scapy.all import ICMP, IP, TCP, UDP, DNS, DNSQR, Raw, send, conf
        except ImportError:
            sys.exit("falta scapy: sudo apt install python3-scapy")
        conf.verb = 0

        def paquetes_tcp(flags, puertos, src=None, dport_unico=None):
            ip = IP(dst=a.destino, **({"src": src} if src else {}))
            return [ip / TCP(sport=random.randint(20000, 60000), dport=p, flags=flags, seq=random.randint(0, 2**32 - 1))
                    for p in puertos]

    def normal(tipo):
        if tipo == "http_get":
            try:
                req = urllib.request.Request(f"http://{a.destino}:{a.puerto_web}/", headers={"User-Agent": UA_NAVEGADOR})
                urllib.request.urlopen(req, timeout=2).read(256)
            except Exception:
                pass
            return 6
        if tipo == "tcp_completo":
            try:
                socket.create_connection((a.destino, a.puerto_web), timeout=2).close()
            except OSError:
                pass
            return 3
        if tipo == "ping":
            send(IP(dst=a.destino) / ICMP() / Raw(b"hola"), count=3, inter=0.2)
            return 3
        send(IP(dst=a.destino) / UDP(sport=random.randint(20000, 60000), dport=53) / DNS(rd=1, qd=DNSQR(qname="www.pucp.edu.pe")))
        return 1

    def malo(tipo):
        src = a.src_malo
        if tipo == "scan_syn":
            pk = paquetes_tcp("S", random.sample(range(1, 1025), 120), src)
        elif tipo == "scan_fin":
            pk = paquetes_tcp("F", random.sample(range(1, 1025), 60), src)
        elif tipo == "scan_null":
            pk = paquetes_tcp(0, random.sample(range(1, 1025), 60), src)
        elif tipo == "scan_xmas":
            pk = paquetes_tcp("FPU", random.sample(range(1, 1025), 60), src)
        elif tipo == "syn_flood":
            pk = paquetes_tcp("S", [80] * 400, src)
        else:   # web_sqlmap: sin handshake no hay HTTP; con --src-malo no hay respuesta, así que se usa socket real solo sin spoof
            if src:
                return 0
            try:
                s = socket.create_connection((a.destino, a.puerto_web), timeout=2)
                s.sendall(f"GET /?id=1 HTTP/1.1\r\nHost: {a.destino}\r\nUser-Agent: sqlmap/1.7\r\n\r\n".encode())
                s.close()
            except OSError:
                pass
            return 4
        send(pk, inter=0.002)
        return len(pk)

    miproi = a.src_malo or "(propia)"
    print(f"destino={a.destino} duración={a.duracion}s malo={a.pct_malo}% origen malo={miproi} dry-run={a.dry_run}")
    fin = time.time() + a.duracion
    n = {"normal": 0, "malo": 0}
    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "etiqueta", "tipo", "origen", "destino", "paquetes", "sid_esperado"])
        while time.time() < fin:
            es_malo = random.randint(1, 100) <= a.pct_malo
            etiqueta = "malo" if es_malo else "normal"
            tipo = random.choice(malos if es_malo else NORMALES)
            t = time.time()
            if a.dry_run:
                pk = 0
            else:
                pk = malo(tipo) if es_malo else normal(tipo)
            sid = MALOS[tipo][1] if es_malo else ""
            w.writerow([f"{t:.3f}", etiqueta, tipo, a.src_malo if es_malo and a.src_malo else "propia", a.destino, pk, sid])
            f.flush()
            n[etiqueta] += 1
            print(f"{time.strftime('%H:%M:%S')} {etiqueta:<6} {tipo:<12} {pk:>4} paq" + (f"  -> regla {sid}" if es_malo else ""))
            time.sleep(a.pausa)
    print(f"listo: {n['normal']} eventos normales, {n['malo']} maliciosos. Verdad en {a.csv}")


if __name__ == "__main__":
    main()
