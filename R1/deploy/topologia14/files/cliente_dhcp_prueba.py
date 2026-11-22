#!/usr/bin/env python3
"""Cliente DHCP MINIMO para probar la app DHCP por subred (83_prueba_dhcp.yml). Habla por un socket crudo sobre una interfaz (una macvlan de prueba) y NO configura nada en el host: ni IP, ni rutas, ni DNS.

    python3 cliente_dhcp_prueba.py --iface dhc1 --mac 02:aa:bb:00:01:01 --salida /tmp/dhcp_prueba_dhc1.json --mantener 15

Hace DISCOVER (hasta 5 intentos), REQUEST de la IP ofrecida y escribe el resultado en --salida (JSON: estado ACK, NAK, SIN_OFERTA o SIN_ACK, y la IP, el router, el servidor y la concesion).
Con ACK mantiene la concesion --mantener segundos (o hasta recibir SIGTERM) y entonces envia un DHCPRELEASE de verdad. Necesita root (socket AF_PACKET).
"""
import argparse
import json
import os
import random
import signal
import socket
import struct
import sys
import time

MAGIA = b"\x63\x82\x53\x63"
DISCOVER, OFFER, REQUEST, ACK, NAK, RELEASE = 1, 2, 3, 5, 6, 7
parar = False


def mac_bytes(texto):
    return bytes(int(x, 16) for x in texto.split(":"))


def mac_texto(b):
    return ":".join(f"{x:02x}" for x in b)


def checksum(datos):
    if len(datos) % 2:
        datos += b"\0"
    s = sum(struct.unpack("!%dH" % (len(datos) // 2), datos))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def opciones(tipo, extra=()):
    o = bytes([53, 1, tipo])
    for codigo, valor in extra:
        o += bytes([codigo, len(valor)]) + valor
    return o + bytes([55, 4, 1, 3, 6, 51]) + b"\xff"


def trama(mac, xid, tipo, ip_origen, ip_destino, mac_destino, ciaddr, extra=()):
    bootp = struct.pack("!BBBBIHH4s4s4s4s16s64s128s4s", 1, 1, 6, 0, xid, 0, 0x8000 if ip_origen == b"\0\0\0\0" else 0, ciaddr, b"\0" * 4, b"\0" * 4, b"\0" * 4,
                        mac, b"", b"", MAGIA) + opciones(tipo, extra)
    udp = struct.pack("!HHHH", 68, 67, 8 + len(bootp), 0) + bootp
    cab = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, ip_origen, ip_destino)
    cab = cab[:10] + struct.pack("!H", checksum(cab)) + cab[12:]
    return mac_destino + mac + b"\x08\x00" + cab + udp


def analizar(datos, mac, xid):
    """La respuesta DHCP (UDP 67 a 68) para este xid y esta MAC, o None."""
    if len(datos) < 14 + 20 + 8 + 240 or datos[12:14] != b"\x08\x00":
        return None
    ihl = (datos[14] & 0x0F) * 4
    if datos[14 + 9] != 17:
        return None
    udp = 14 + ihl
    origen, destino = struct.unpack("!HH", datos[udp:udp + 4])
    if origen != 67 or destino != 68:
        return None
    b = datos[udp + 8:]
    if b[0] != 2 or struct.unpack("!I", b[4:8])[0] != xid or b[28:34] != mac or b[236:240] != MAGIA:
        return None
    r = {"yiaddr": socket.inet_ntoa(b[16:20]), "origen_mac": datos[6:12]}
    i = 240
    while i < len(b) and b[i] != 255:
        if b[i] == 0:
            i += 1
            continue
        codigo, largo = b[i], b[i + 1]
        valor = b[i + 2:i + 2 + largo]
        if codigo == 53:
            r["tipo"] = valor[0]
        elif codigo == 54:
            r["servidor"] = socket.inet_ntoa(valor)
        elif codigo == 1:
            r["mascara"] = socket.inet_ntoa(valor)
        elif codigo == 3:
            r["router"] = socket.inet_ntoa(valor[:4])
        elif codigo == 51:
            r["lease"] = struct.unpack("!I", valor)[0]
        elif codigo == 121:
            r["rutas"] = rutas_sin_clase(valor)
        i += 2 + largo
    return r


def rutas_sin_clase(valor):
    """Decodifica la opcion 121 (RFC 3442): por cada ruta, longitud del prefijo, (longitud + 7) // 8 octetos de la red y 4 octetos del gateway. Devuelve ["red/longitud via gateway", ...]."""
    rutas, i = [], 0
    while i < len(valor):
        longitud = valor[i]
        n = (longitud + 7) // 8
        if longitud > 32 or i + 1 + n + 4 > len(valor):
            rutas.append("(opcion 121 mal formada)")
            break
        red = bytes(valor[i + 1:i + 1 + n]) + b"\x00" * (4 - n)
        gw = valor[i + 1 + n:i + 1 + n + 4]
        rutas.append("%s/%d via %s" % (socket.inet_ntoa(red), longitud, socket.inet_ntoa(bytes(gw))))
        i += 1 + n + 4
    return rutas


def esperar(sock, mac, xid, tipos, segundos):
    limite = time.time() + segundos
    while time.time() < limite:
        sock.settimeout(max(0.05, limite - time.time()))
        try:
            datos = sock.recv(4096)
        except socket.timeout:
            return None
        r = analizar(datos, mac, xid)
        if r and r.get("tipo") in tipos:
            return r
    return None


def main():
    global parar
    a = argparse.ArgumentParser()
    a.add_argument("--iface", required=True)
    a.add_argument("--mac", required=True)
    a.add_argument("--salida", required=True)
    a.add_argument("--mantener", type=int, default=0, help="segundos que se mantiene la concesion antes de enviar RELEASE (0 = no se libera)")
    a.add_argument("--parar", default="/tmp/dhcp_prueba_parar", help="si este archivo existe mientras se mantiene la concesion, se envia el RELEASE ya (parada ordenada, sin senales)")
    arg = a.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("parar", True))
    mac = mac_bytes(arg.mac)
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.bind((arg.iface, 0))
    difusion = b"\xff" * 6
    resultado = {"mac": arg.mac, "estado": "SIN_OFERTA"}
    oferta = None
    for intento in range(5):
        xid = random.getrandbits(32)
        sock.send(trama(mac, xid, DISCOVER, b"\0" * 4, b"\xff" * 4, difusion, b"\0" * 4))
        oferta = esperar(sock, mac, xid, {OFFER}, 2.5)
        if oferta:
            break
    if oferta:
        ip = socket.inet_aton(oferta["yiaddr"])
        extra = [(50, ip), (54, socket.inet_aton(oferta["servidor"]))]
        sock.send(trama(mac, xid, REQUEST, b"\0" * 4, b"\xff" * 4, difusion, b"\0" * 4, extra))
        final = esperar(sock, mac, xid, {ACK, NAK}, 3.0)
        if final is None:
            resultado = {"mac": arg.mac, "estado": "SIN_ACK", "oferta": oferta["yiaddr"]}
        elif final["tipo"] == NAK:
            resultado = {"mac": arg.mac, "estado": "NAK", "oferta": oferta["yiaddr"]}
        else:
            resultado = {"mac": arg.mac, "estado": "ACK", "ip": final["yiaddr"], "router": final.get("router"), "mascara": final.get("mascara"),
                         "servidor": final.get("servidor"), "lease": final.get("lease"), "rutas": final.get("rutas")}
    print(time.strftime("%H:%M:%S"), "resultado:", json.dumps(resultado), flush=True)
    with open(arg.salida, "w") as f:
        json.dump(resultado, f)
    if resultado["estado"] == "ACK" and arg.mantener:
        fin = time.time() + arg.mantener
        while time.time() < fin and not parar and not os.path.exists(arg.parar):
            time.sleep(0.2)
        print(time.strftime("%H:%M:%S"), "enviando RELEASE de", resultado["ip"], "(parada pedida)" if (parar or os.path.exists(arg.parar)) else "(fin de la concesion de prueba)", flush=True)
        ip = socket.inet_aton(resultado["ip"])
        sock.send(trama(mac, random.getrandbits(32), RELEASE, ip, socket.inet_aton(resultado["servidor"]), oferta["origen_mac"], ip,
                        [(54, socket.inet_aton(resultado["servidor"]))]))
        print(time.strftime("%H:%M:%S"), "RELEASE enviado", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
