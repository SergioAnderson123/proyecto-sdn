"""Pruebas de las tramas del generador de estrés (files/estres_difusion.py): longitudes, tipos y sumas de verificación."""
import importlib.util
import itertools
import os
import socket
import struct

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("estres_difusion", os.path.join(AQUI, "files", "estres_difusion.py"))
ed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ed)

MAC = ed.mac_bytes("fa:16:3e:71:08:66")


def test_la_trama_arp_es_una_difusion_de_60_bytes_con_ethertype_arp_y_los_campos_en_su_sitio():
    t = ed.trama_arp(MAC, "10.0.0.202", "10.0.0.77")
    assert len(t) == 60 and t[:6] == b"\xff" * 6 and t[6:12] == MAC and t[12:14] == b"\x08\x06"
    assert struct.unpack("!HHBBH", t[14:22]) == (1, 0x0800, 6, 4, 1)                         # petición ARP de Ethernet/IPv4
    assert t[22:28] == MAC and t[28:32] == socket.inet_aton("10.0.0.202") and t[38:42] == socket.inet_aton("10.0.0.77")


def test_la_difusion_de_tipo_desconocido_no_usa_arp_ipv4_eapol_ni_lldp():
    t = ed.trama_eth_difusion(MAC)
    assert len(t) == 60 and t[:6] == b"\xff" * 6 and struct.unpack("!H", t[12:14])[0] == 0x88B5
    assert struct.unpack("!H", t[12:14])[0] not in (0x0806, 0x0800, 0x888E, 0x88CC, 0x8942)


def test_el_tcp_tiene_las_sumas_de_verificacion_correctas_y_la_mac_destino_pedida():
    destino = bytes([0x02, 0xDE, 0xAD, 0, 0, 1])
    t = ed.trama_tcp(MAC, destino, "10.0.0.202", "10.0.0.10", 8001)
    assert t[:6] == destino and t[6:12] == MAC and t[12:14] == b"\x08\x00"
    ip = t[14:34]
    assert ed.checksum(ip) == 0                                                              # un encabezado IPv4 válido suma 0
    assert ip[9] == 6 and ip[12:16] == socket.inet_aton("10.0.0.202") and ip[16:20] == socket.inet_aton("10.0.0.10")
    tcp = t[34:54]
    assert struct.unpack("!H", tcp[2:4])[0] == 8001 and tcp[13] == 0x02                      # puerto destino y bandera SYN
    pseudo = ip[12:20] + struct.pack("!BBH", 0, 6, len(tcp))
    assert ed.checksum(pseudo + tcp) == 0


def test_el_generador_cambia_de_destino_en_cada_trama_y_no_repite_la_mac_falsa_en_las_primeras():
    g = ed.generador("desconocido", MAC, "10.0.0.202")
    macs = {next(g)[:6] for _ in range(200)}
    assert len(macs) == 200 and all(m[:2] == bytes([0x02, 0xDE]) for m in macs)
    arp = [next(ed.generador("arp", MAC, "10.0.0.202"))[38:42] for _ in range(1)]
    assert arp[0] == socket.inet_aton("10.0.0.2")
    with pytest.raises(ValueError):
        next(ed.generador("otro", MAC, "10.0.0.202"))


def test_los_limites_de_seguridad_rechazan_tasas_y_duraciones_excesivas():
    ed.validar(500, 20)
    for pps, seg in ((0, 10), (2001, 10), (500, 0), (500, 121)):
        with pytest.raises(SystemExit):
            ed.validar(pps, seg)
