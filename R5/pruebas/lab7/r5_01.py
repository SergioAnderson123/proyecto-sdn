#!/usr/bin/env python3
"""R5-01  parse_ids_alert(linea): lectura de la alerta del IDS (casos 3.1.x).  Criterio que valida: deteccion del ataque.

Uso, desde la raiz del repositorio:   python3 R5/pruebas/lab7/r5_01.py
Pruebas equivalentes con pytest:      python3 -m pytest R5/tests/test_lab7_r5.py -k R5_01_ -v
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comun import OnosFalso, caso, estado, integ, mostrar, recortar, registro, terminar  # noqa: E402

LINEA_VALIDA = json.dumps({
    "timestamp": "2026-11-23T15:22:00.000000+0000", "event_type": "alert", "src_ip": "203.0.113.103", "src_port": 51234,
    "dest_ip": "10.0.0.10", "dest_port": 80, "proto": "TCP", "direction": "to_server",
    "ether": {"src_mac": "AA:BB:CC:00:00:01", "dest_mac": "AA:BB:CC:00:00:02"},
    "alert": {"severity": 2, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}})


def ev_alerta(**extra):
    base = {"timestamp": "2026-11-24T15:22:00.000000+0000", "event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10",
            "proto": "TCP", "alert": {"severity": 2, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}}
    base.update(extra)
    return base


def main():
    print("R5-01  parse_ids_alert(linea): lectura de la alerta del IDS (3.1.x)")
    print("Criterio que valida: deteccion del ataque. El integrador solo puede reaccionar a lo que entiende del IDS.")

    # 3.1.1 alerta valida
    a = integ.parse_ids_alert(LINEA_VALIDA)
    vista = {k: v for k, v in a.items() if k != "evento"}
    caso("3.1.1", "Alerta valida: devuelve direcciones, puertos, protocolo, firma y severidad",
         "parse_ids_alert(linea)", mostrar(LINEA_VALIDA),
         "diccionario con src_ip=203.0.113.103:51234, dest_ip=10.0.0.10:80, proto=TCP, sid=9000002, severidad=2, MAC en minusculas",
         mostrar(vista),
         [("origen = 203.0.113.103:51234", (a["src_ip"], a["src_port"]) == ("203.0.113.103", 51234)),
          ("destino = 10.0.0.10:80", (a["dest_ip"], a["dest_port"]) == ("10.0.0.10", 80)),
          ("protocolo = TCP", a["proto"] == "TCP"),
          ("firma = 'LOCAL Port scan SYN' (sid 9000002)", (a["sid"], a["firma"]) == (9000002, "LOCAL Port scan SYN")),
          ("severidad = 2", a["severidad"] == 2),
          ("hora del paquete conservada", a["timestamp"] == "2026-11-23T15:22:00.000000+0000"),
          ("MAC origen/destino pasadas a minusculas", (a["mac_src"], a["mac_dst"]) == ("aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02")),
          ("el evento original sigue disponible para el resto del integrador", a["evento"]["alert"]["signature_id"] == 9000002)])

    # 3.1.1b alerta sin campos opcionales
    linea = json.dumps({"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "proto": "ICMP",
                        "alert": {"severity": 1, "signature_id": 9000021, "signature": "LOCAL R4 Inundacion ICMP interna"}})
    a = integ.parse_ids_alert(linea)
    caso("3.1.1b", "Alerta ICMP sin puertos ni MAC: sigue siendo valida (campos opcionales = None)",
         "parse_ids_alert(linea)", mostrar(linea), "diccionario valido con src_port, dest_port, mac_src y mac_dst en None",
         mostrar({k: v for k, v in (a or {}).items() if k != "evento"}),
         [("no es None", a is not None),
          ("puertos y MAC en None", a is not None and (a["src_port"], a["dest_port"], a["mac_src"], a["mac_dst"]) == (None,) * 4)])

    # 3.1.2 alertas incompletas
    incompletas = [
        ("sin bloque 'alert'", {"event_type": "alert"}),
        ("sin src_ip ni dest_ip", {"event_type": "alert", "alert": {"severity": 1}}),
        ("IP invalidas ('x', 'y')", {"event_type": "alert", "src_ip": "x", "dest_ip": "y", "alert": {"severity": 1}}),
        ("sin destino", {"event_type": "alert", "src_ip": "203.0.113.5", "alert": {"severity": 1}}),
        ("sin severidad (no hay bloque alert)", {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10"}),
        ("bloque alert vacio", {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {}}),
        ("severidad no numerica ('alta')", {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {"severity": "alta"}}),
        ("'alert' no es un objeto", {"event_type": "alert", "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": "texto"}),
    ]
    for i, (que, ev) in enumerate(incompletas, 1):
        onos = OnosFalso()
        integ.onos = onos
        r = integ.parse_ids_alert(json.dumps(ev))
        st = estado()
        error = None
        try:
            integ.procesar(ev, st)
        except Exception as e:                                    # noqa: BLE001  (la prueba es justamente que no lance)
            error = repr(e)
        caso(f"3.1.2 [{i}/8]", f"Alerta incompleta o mal tipada: {que}", "parse_ids_alert(linea) y luego procesar(evento)",
             json.dumps(ev, ensure_ascii=False), "None, sin excepcion, y ninguna regla de bloqueo hacia ONOS",
             f"parse_ids_alert -> {r!r}\nprocesar() -> {'excepcion ' + error if error else 'sin excepcion'}, bloqueadas={st['bloqueadas']}, POST a ONOS={len(onos.posts())}",
             [("parse_ids_alert devuelve None", r is None), ("procesar() no lanza excepcion", error is None),
              ("no se bloquea ninguna IP", st["bloqueadas"] == {}), ("no se envia ningun POST a ONOS", onos.posts() == [])])

    # 3.1.3 lineas corruptas
    corruptas = [('{"event_type": "alert", ', "JSON truncado"), ("no es json", "texto cualquiera"), ("", "linea vacia"),
                 ("   ", "solo espacios"), ("[]", "JSON valido pero lista"), ("5", "JSON valido pero numero"),
                 ("null", "JSON null"), ('"texto"', "JSON valido pero cadena"), (None, "no es texto (None)")]
    for i, (linea, que) in enumerate(corruptas, 1):
        error = None
        try:
            r = integ.parse_ids_alert(linea)
        except Exception as e:                                    # noqa: BLE001
            r, error = "(excepcion)", repr(e)
        caso(f"3.1.3 [{i}/9]", f"Linea corrupta o que no es un objeto: {que}", "parse_ids_alert(linea)", repr(linea),
             "None, sin excepcion", repr(r) if not error else f"excepcion {error}",
             [("devuelve None", r is None), ("no lanza excepcion", error is None)])

    # 3.1.4 eventos que no son alerta
    for i, tipo in enumerate(["stats", "flow", "dns", "anomaly", ""], 1):
        ev = {"event_type": tipo, "src_ip": "203.0.113.5", "dest_ip": "10.0.0.10", "alert": {"severity": 1}}
        r = integ.parse_ids_alert(json.dumps(ev))
        caso(f"3.1.4 [{i}/5]", f"Evento que no es una alerta (event_type = {tipo!r})", "parse_ids_alert(linea)",
             json.dumps(ev), "None (solo se procesan eventos 'alert')", repr(r), [("devuelve None", r is None)])

    # 3.1.5 linea a medio escribir
    with tempfile.TemporaryDirectory() as d:
        eve = os.path.join(d, "eve-alerts.json")
        integ.EVE_PATH = eve
        onos = OnosFalso()
        integ.onos = onos
        completa = LINEA_VALIDA + "\n"
        mitad = json.dumps(ev_alerta(src_ip="203.0.113.9", alert={"severity": 1, "signature_id": 9000003, "signature": "LOCAL SYN flood"}))
        st = estado()
        with open(eve, "w", encoding="utf-8", newline="") as f:
            f.write(completa + mitad[:30])
        fallo1 = integ.consumir_eve(st)
        off1, ips1 = st["offset"], sorted(st["bloqueadas"])
        with open(eve, "w", encoding="utf-8", newline="") as f:
            f.write(completa + mitad + "\n")
        fallo2 = integ.consumir_eve(st)
        ips2 = sorted(st["bloqueadas"])
        caso("3.1.5", "Linea a medio escribir: no se procesa ni avanza el offset hasta que Suricata la termine",
             "consumir_eve(estado) sobre un archivo eve-alerts.json",
             f"archivo con 2 lineas; la 2.a incompleta ({len(mitad[:30])} de {len(mitad)} caracteres, sin salto de linea)\n"
             f"1.a linea ({len(completa.encode())} bytes): {recortar(LINEA_VALIDA, 70)}\n2.a linea (parcial): {mitad[:30]}",
             "1.a pasada: solo se bloquea 203.0.113.103 y el offset queda tras la 1.a linea. 2.a pasada (linea completa): tambien 203.0.113.9",
             f"1.a pasada: offset={off1}, bloqueadas={ips1}\n2.a pasada: offset={st['offset']}, bloqueadas={ips2}\nregistro del integrador:\n{registro(st)}",
             [(f"offset tras la 1.a pasada = {len(completa.encode())} (solo la linea completa)", off1 == len(completa.encode())),
              ("1.a pasada: solo 203.0.113.103 bloqueada", ips1 == ["203.0.113.103"]),
              ("2.a pasada: se procesa la linea ya completa", ips2 == ["203.0.113.103", "203.0.113.9"]),
              ("ninguna pasada reporta fallo de ONOS", fallo1 is False and fallo2 is False)])

    # 3.1.6 linea corrupta dentro del archivo
    with tempfile.TemporaryDirectory() as d:
        eve = os.path.join(d, "eve-alerts.json")
        integ.EVE_PATH = eve
        onos = OnosFalso()
        integ.onos = onos
        with open(eve, "w", encoding="utf-8", newline="") as f:
            f.write("basura que no es json\n" + LINEA_VALIDA + "\n")
        st = estado()
        integ.consumir_eve(st)
        tam = os.path.getsize(eve)
        caso("3.1.6", "Linea corrupta dentro del archivo: se salta y las siguientes se procesan",
             "consumir_eve(estado) sobre un archivo eve-alerts.json",
             f"archivo con 2 lineas: 'basura que no es json' y una alerta valida de 203.0.113.103 ({tam} bytes en total)",
             "se salta la 1.a linea, se bloquea 203.0.113.103 y el offset llega al final del archivo",
             f"bloqueadas={sorted(st['bloqueadas'])}, offset={st['offset']} (tamano del archivo = {tam})\nregistro del integrador:\n{registro(st)}",
             [("la alerta valida se procesa", list(st["bloqueadas"]) == ["203.0.113.103"]),
              ("el offset avanza hasta el final", st["offset"] == tam)])

    return terminar("R5-01")


if __name__ == "__main__":
    sys.exit(main())
