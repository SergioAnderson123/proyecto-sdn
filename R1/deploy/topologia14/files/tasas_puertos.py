#!/usr/bin/env python3
"""SOLO LECTURA. Mide los paquetes por segundo de cada puerto de un bridge de Open vSwitch (dos lecturas de `ovs-ofctl dump-ports`).

Una tormenta de broadcast por un lazo de capa 2 se ve como muchos paquetes por segundo en varios puertos a la vez, aunque no haya
tráfico de usuarios. Salida: una fila por puerto y, al final, `RESULTADO tormenta=<si|no> maximo=<pps>`.

    python3 tasas_puertos.py [bridge] [segundos] [umbral_pps]        (por defecto: br0 5 2000)
"""
import re
import subprocess
import sys
import time


def leer_puertos(texto):
    """{puerto: (rx_paquetes, tx_paquetes)} a partir de la salida de `ovs-ofctl dump-ports`."""
    puertos, actual = {}, None
    for linea in texto.splitlines():
        m = re.match(r"\s*port\s+(\S+?):\s+rx pkts=(\d+|\?)", linea)
        if m:
            actual = m.group(1)
            rx = int(m.group(2)) if m.group(2).isdigit() else 0
            puertos[actual] = [rx, 0]
            continue
        m = re.match(r"\s*tx pkts=(\d+|\?)", linea)
        if m and actual is not None:
            puertos[actual][1] = int(m.group(1)) if m.group(1).isdigit() else 0
            actual = None
    return {k: tuple(v) for k, v in puertos.items()}


def tasas(antes, despues, segundos):
    """[(puerto, rx_pps, tx_pps)] ordenado de mayor a menor tráfico."""
    filas = []
    for p, (rx1, tx1) in despues.items():
        rx0, tx0 = antes.get(p, (rx1, tx1))
        filas.append((p, max(rx1 - rx0, 0) / segundos, max(tx1 - tx0, 0) / segundos))
    return sorted(filas, key=lambda f: -(f[1] + f[2]))


def hay_tormenta(filas, umbral):
    """Tormenta probable: dos o más puertos (sin contar LOCAL) por encima del umbral, de entrada o de salida."""
    altos = [f for f in filas if f[0] != "LOCAL" and max(f[1], f[2]) >= umbral]
    return len(altos) >= 2


def _volcar(bridge):
    return subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-ports", bridge], capture_output=True, text=True, check=True).stdout


def main(argv):
    bridge = argv[1] if len(argv) > 1 else "br0"
    segundos = float(argv[2]) if len(argv) > 2 else 5.0
    umbral = float(argv[3]) if len(argv) > 3 else 2000.0
    antes = leer_puertos(_volcar(bridge))
    time.sleep(segundos)
    despues = leer_puertos(_volcar(bridge))
    filas = tasas(antes, despues, segundos)
    print(f"{'puerto':<10}{'rx pps':>12}{'tx pps':>12}")
    for p, rx, tx in filas:
        print(f"{p:<10}{rx:>12.1f}{tx:>12.1f}")
    maximo = max([max(f[1], f[2]) for f in filas if f[0] != "LOCAL"] or [0.0])
    print(f"RESULTADO tormenta={'si' if hay_tormenta(filas, umbral) else 'no'} maximo={maximo:.0f}")


if __name__ == "__main__":
    main(sys.argv)
