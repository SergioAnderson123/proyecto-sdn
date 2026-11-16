#!/usr/bin/env python3
"""Genera tráfico de ESTRÉS desde un host para comprobar que la red no multiplica difusión (no hay tormenta). Solo biblioteca estándar; necesita root.

    sudo python3 estres_difusion.py --iface ens4 --ip 10.0.0.202 --modo arp --pps 500 --segundos 20

Modos (cada uno prueba una forma distinta de inundación):
  arp          difusión ARP (who-has de IP distintas). Es lo ÚNICO que la red reparte: por el árbol, una copia por puerto. Cada puerto del árbol debe ver ~pps, no más.
  eth          difusión de un tipo de trama desconocido (0x88b5). Debe morir en el puerto de entrada: ni ARP, ni IPv4, ni EAPOL, ni LLDP.
  desconocido  TCP a un servicio PERMITIDO (10.0.0.10:8001) pero con MAC destino que nadie tiene. Pasa la tabla de rol y muere en el descarte de la tabla 60.
Límites de seguridad: como máximo 2000 pps y 120 s. Se detiene con Ctrl+C.
"""
import argparse
import random
import socket
import struct
import sys
import time

MAX_PPS = 2000
MAX_SEGUNDOS = 120
BROADCAST = b"\xff" * 6


def mac_bytes(texto):
    return bytes(int(x, 16) for x in texto.split(":"))


def checksum(datos):
    """Suma de complemento a uno de 16 bits (IPv4 y TCP)."""
    if len(datos) % 2:
        datos += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(datos) // 2), datos))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def relleno(trama):
    """Ethernet exige al menos 60 bytes (sin FCS)."""
    return trama + b"\x00" * max(0, 60 - len(trama))


def trama_arp(mac_origen, ip_origen, ip_objetivo):
    """Difusión ARP who-has: quién tiene `ip_objetivo`, dígaselo a `ip_origen`."""
    arp = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + mac_origen + socket.inet_aton(ip_origen) + b"\x00" * 6 + socket.inet_aton(ip_objetivo)
    return relleno(BROADCAST + mac_origen + b"\x08\x06" + arp)


def trama_eth_difusion(mac_origen, tipo=0x88B5):
    """Difusión con un tipo de trama que ninguna regla reconoce."""
    return relleno(BROADCAST + mac_origen + struct.pack("!H", tipo) + b"estres-difusion")


def trama_tcp(mac_origen, mac_destino, ip_origen, ip_destino, puerto_destino, puerto_origen=40000, secuencia=1):
    """Un SYN de TCP sobre IPv4 con sumas de verificación correctas."""
    tcp = struct.pack("!HHIIBBHHH", puerto_origen, puerto_destino, secuencia, 0, 5 << 4, 0x02, 1024, 0, 0)
    pseudo = socket.inet_aton(ip_origen) + socket.inet_aton(ip_destino) + struct.pack("!BBH", 0, 6, len(tcp))
    tcp = tcp[:16] + struct.pack("!H", checksum(pseudo + tcp)) + tcp[18:]
    ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(tcp), random.randint(1, 65535), 0, 64, 6, 0) + socket.inet_aton(ip_origen) + socket.inet_aton(ip_destino)
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    return relleno(mac_destino + mac_origen + b"\x08\x00" + ip + tcp)


def generador(modo, mac_origen, ip_origen, ip_servidor="10.0.0.10", puerto=8001):
    """Itera tramas del modo pedido (indefinidamente)."""
    i = 0
    while True:
        i += 1
        if modo == "arp":
            yield trama_arp(mac_origen, ip_origen, "10.0.0.%d" % (1 + i % 250))
        elif modo == "eth":
            yield trama_eth_difusion(mac_origen)
        elif modo == "desconocido":
            falsa = bytes([0x02, 0xDE, 0xAD, 0x00, (i >> 8) & 0xFF, i & 0xFF])          # MAC administrada localmente que nadie tiene
            yield trama_tcp(mac_origen, falsa, ip_origen, ip_servidor, puerto, 40000 + i % 20000, i)
        else:
            raise ValueError("modo desconocido: " + modo)


def validar(pps, segundos):
    if not 1 <= pps <= MAX_PPS:
        raise SystemExit(f"pps debe estar entre 1 y {MAX_PPS} (se pidió {pps}).")
    if not 1 <= segundos <= MAX_SEGUNDOS:
        raise SystemExit(f"segundos debe estar entre 1 y {MAX_SEGUNDOS} (se pidió {segundos}).")


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--iface", default="ens4")
    a.add_argument("--ip", required=True, help="IP de datos de este host (origen de los ARP y del TCP)")
    a.add_argument("--modo", choices=["arp", "eth", "desconocido"], default="arp")
    a.add_argument("--pps", type=int, default=500)
    a.add_argument("--segundos", type=int, default=20)
    arg = a.parse_args(argv)
    validar(arg.pps, arg.segundos)
    mac = mac_bytes(open(f"/sys/class/net/{arg.iface}/address").read().strip())
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    s.bind((arg.iface, 0))
    tramas = generador(arg.modo, mac, arg.ip)
    por_corte, corte_s, enviadas = max(1, arg.pps // 100), 0.01, 0
    inicio = time.perf_counter()
    siguiente = inicio
    try:
        while time.perf_counter() - inicio < arg.segundos:
            for _ in range(por_corte):
                s.send(next(tramas))
                enviadas += 1
            siguiente += corte_s
            espera = siguiente - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
    except KeyboardInterrupt:
        print("Interrumpido.")
    dur = time.perf_counter() - inicio
    print(f"modo={arg.modo} enviadas={enviadas} en {dur:.1f}s ({enviadas / max(dur, 0.001):.0f} pps) por {arg.iface}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
