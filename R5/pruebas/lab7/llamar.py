#!/usr/bin/env python3
"""Ayudante de los scripts bash del Lab 7 (r5_01.sh ... r5_06.sh): llama a UNA funcion del integrador con la entrada recibida
por la linea de comandos y muestra el resultado, sin ONOS ni Suricata (ONOS se sustituye por un doble que registra las peticiones).

Subcomandos (desde la raiz del repositorio):
  llamar.py parse LINEA                 parse_ids_alert(LINEA): imprime los campos extraidos o None
  llamar.py parse-sin-texto             parse_ids_alert(None): una entrada que ni siquiera es texto
  llamar.py procesar LINEA_JSON         parse_ids_alert y luego procesar(evento): no debe lanzar excepcion ni bloquear nada
  llamar.py consumir ARCHIVO ESTADO     consumir_eve(estado) sobre ARCHIVO; el estado (offset e IPs bloqueadas) se guarda en ESTADO
  llamar.py classify SEVERIDAD          classify_alert(alerta con esa severidad): bloquear | registrar | ignorar (MAX_SEVERITY del entorno, 2 por defecto)
  llamar.py decidir SRC DST [DIRECCION] decidir(evento): a quien se bloquea y donde (core | borde), o None
  llamar.py bloqueo SRC DST SEVERIDAD [DIRECCION] [IP=DEVICE ...]
                                        procesar(alerta) completo con un ONOS falso; IP=DEVICE indica donde ve ONOS a un host interno;
                                        la palabra sin-flowid hace que ONOS responda sin flowId. Con DRY_RUN=1 no se contacta con ONOS.
  llamar.py regla IP [DEVICE]           build_perimeter_rule(IP, DEVICE): la regla DROP que se enviaria a ONOS (P_R5 y BLOCK_SEC del entorno)
  llamar.py incidente SRC DST SEVERIDAD procesar(alerta) y muestra el registro del incidente que queda guardado para SRC
  llamar.py lista-blanca IP             en_lista_blanca(IP) con LISTA_BLANCA del entorno (10.0.0.99/32 y 10.0.0.10/32 por defecto)
  llamar.py redes VALOR                 redes(VALOR): como se leen los prefijos de una variable (HOME_NET, LISTA_BLANCA); ValueError si es invalido
Codigo de salida: 0 si la funcion termino sin excepcion; 1 si lanzo una.
"""
import importlib
import json
import logging
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))          # .../R5
sys.path.insert(0, os.path.join(RAIZ, "codigo"))
for _k, _v in {"ONOS_URL": "http://onos.test", "ONOS_USER": "u", "ONOS_PASS": "p", "HOME_NET": "10.0.0.0/24",
               "LISTA_BLANCA": "10.0.0.99/32,10.0.0.10/32", "CORE_DEVICE": "of:0000000000000001", "MAX_SEVERITY": "2",
               "P_R5": "42000", "BLOCK_SEC": "300"}.items():
    os.environ.setdefault(_k, _v)                              # valores del laboratorio, salvo que el entorno ya defina otro (p. ej. MAX_SEVERITY=1)
integ = importlib.import_module("integrador")
logging.disable(logging.CRITICAL)


class Resp:
    def __init__(self, cuerpo=None, codigo=200):
        self._c, self.status_code, self.text = cuerpo or {}, codigo, json.dumps(cuerpo or {})

    def json(self):
        return self._c


class OnosFalso:
    """Doble de ONOS: registra las peticiones y responde lo minimo para que el integrador funcione."""

    def __init__(self, hosts=None, flowid=True):
        self.llamadas, self.hosts, self.flowid, self.flujos = [], hosts or [], flowid, []

    def __call__(self, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta))
        if metodo == "GET" and ruta == "/hosts":
            return Resp({"hosts": self.hosts})
        if metodo == "POST" and ruta == "/flows":
            flujos = kw["json"]["flows"]
            self.flujos.extend(flujos)
            return Resp({"flows": [dict({"deviceId": f["deviceId"]}, **({"flowId": str(777 + i)} if self.flowid else {}))
                                   for i, f in enumerate(flujos)]})
        return Resp()

    def posts(self):
        return [c for c in self.llamadas if c[0] == "POST"]


def mostrar_alerta(r):
    if r is None:
        print("None")
    else:
        print(json.dumps({k: v for k, v in r.items() if k != "evento"}, indent=2, ensure_ascii=False))


def cmd_parse(linea):
    mostrar_alerta(integ.parse_ids_alert(linea))


def cmd_parse_sin_texto():
    mostrar_alerta(integ.parse_ids_alert(None))


def cmd_procesar(linea):
    onos = OnosFalso()
    integ.onos = onos
    r = integ.parse_ids_alert(linea)
    print(f"parse_ids_alert -> {r if r is None else 'alerta valida'}")
    st = {"bloqueadas": {}, "inode": None, "offset": 0}
    try:
        integ.procesar(json.loads(linea), st)
    except Exception as e:                                       # noqa: BLE001  (la prueba es justamente que no lance)
        print(f"procesar -> EXCEPCION {e!r}")
        return 1
    print(f"procesar -> sin excepcion; bloqueadas={sorted(st['bloqueadas'])}; POST a ONOS={len(onos.posts())}")
    return 0


def cmd_consumir(archivo, estado):
    try:
        with open(estado, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("bloqueadas", {})
    st.setdefault("inode", None)
    st.setdefault("offset", 0)
    integ.EVE_PATH = archivo
    integ.onos = OnosFalso()
    fallo = integ.consumir_eve(st)
    with open(estado, "w", encoding="utf-8") as f:
        json.dump(st, f)
    print(f"offset={st['offset']}")
    print(f"bloqueadas={','.join(sorted(st['bloqueadas'])) or '(ninguna)'}")
    print(f"fallo_onos={fallo}")
    for ip, v in sorted(st["bloqueadas"].items()):
        print(f"registro: {ip} -> device={v['device']}, flow_id={v['flow_id']}, sid={v['sid']}, firma={v['firma']!r}")
    return 0


def evento(src, dst, severidad, direccion="to_server"):
    return {"event_type": "alert", "timestamp": "2026-11-24T15:22:00.000000+0000", "src_ip": src, "dest_ip": dst, "direction": direccion,
            "proto": "TCP", "alert": {"severity": severidad, "signature_id": 9000002, "signature": "LOCAL Port scan SYN"}}


def cmd_classify(severidad):
    alerta = integ.normalizar_alerta(evento("203.0.113.7", "10.0.0.10", int(severidad)))
    print(f"severidad={severidad} maximo_para_bloquear={integ.MAX_SEVERITY} accion={integ.classify_alert(alerta)}")


def cmd_decidir(src, dst, direccion="to_server"):
    r = integ.decidir(evento(src, dst, 1, direccion))
    print("resultado=None" if r is None else f"resultado={r[0]},{r[1]}")


def cmd_bloqueo(src, dst, severidad, *resto):
    direccion = "to_server"
    hosts = []
    flowid = True
    for x in resto:
        if x == "sin-flowid":
            flowid = False
        elif "=" in x:                                              # IP=DEVICE: ONOS conoce a ese host en ese switch de borde
            ip, dev = x.split("=", 1)
            hosts.append({"ipAddresses": [ip], "locations": [{"elementId": dev}]})
        else:
            direccion = x
    onos = OnosFalso(hosts, flowid)
    integ.onos = onos
    st = {"bloqueadas": {}, "inode": None, "offset": 0}
    ev = evento(src, dst, int(severidad), direccion)
    accion = integ.classify_alert(integ.normalizar_alerta(ev))
    integ.procesar(ev, st)
    print(f"accion={accion}")
    print(f"bloqueadas={','.join(sorted(st['bloqueadas'])) or '(ninguna)'}")
    print(f"POST a ONOS={len(onos.posts())}")
    print(f"flows_en_el_POST={len(onos.flujos)} dispositivos={','.join(f['deviceId'] for f in onos.flujos) or '(ninguno)'}")
    for ip, v in sorted(st["bloqueadas"].items()):
        print(f"registro: {ip} -> device={v['device']}, donde={v['donde']}, sid={v['sid']}, flow_id={v['flow_id']}")


def cmd_regla(ip, device="of:0000000000000001"):
    try:
        regla = integ.build_perimeter_rule(ip, device)
    except ValueError as e:                                       # una IP invalida no debe producir una regla: aqui es lo esperado
        print(f"ValueError: {e}")
        return 0
    print(json.dumps(regla, indent=2))
    print(f"criterios={','.join(c['type'] for c in regla['selector']['criteria'])}")
    return 0


def cmd_incidente(src, dst, severidad):
    integ.onos = OnosFalso()
    st = {"bloqueadas": {}, "inode": None, "offset": 0}
    integ.procesar(evento(src, dst, int(severidad)), st)
    v = st["bloqueadas"].get(src)
    if v is None:
        print("incidente=(ninguno)")
        return 0
    tiempos = ("vence", "t_lectura", "t_post", "t_201")
    print(f"ip_bloqueada={src}")
    print(json.dumps({k: x for k, x in v.items() if k not in tiempos}, indent=2, ensure_ascii=False))
    print(f"tiempos_ordenados={v['t_lectura'] <= v['t_post'] <= v['t_201']}")
    print(f"vence_despues_de_t_201={v['vence'] > v['t_201']}")
    return 0


def cmd_lista_blanca(ip):
    print(f"ip={ip} en_lista_blanca={integ.en_lista_blanca(ip)} lista={','.join(str(r) for r in integ.LISTA_BLANCA)}")


def cmd_redes(valor):
    os.environ["PRUEBA_LISTA"] = valor
    try:
        r = integ.redes("PRUEBA_LISTA", "")
    except ValueError as e:                                       # un prefijo invalido debe detectarse al arrancar: aqui es lo esperado
        print(f"ValueError: {e}")
        return 0
    print(f"redes={','.join(str(x) for x in r) or '(ninguna)'}")
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "parse" and len(args) == 1:
            cmd_parse(args[0])
        elif cmd == "parse-sin-texto":
            cmd_parse_sin_texto()
        elif cmd == "procesar" and len(args) == 1:
            return cmd_procesar(args[0])
        elif cmd == "consumir" and len(args) == 2:
            return cmd_consumir(*args)
        elif cmd == "classify" and len(args) == 1:
            cmd_classify(*args)
        elif cmd == "decidir" and len(args) in (2, 3):
            cmd_decidir(*args)
        elif cmd == "bloqueo" and len(args) >= 3:
            cmd_bloqueo(*args)
        elif cmd == "regla" and len(args) in (1, 2):
            return cmd_regla(*args)
        elif cmd == "incidente" and len(args) == 3:
            return cmd_incidente(*args)
        elif cmd == "lista-blanca" and len(args) == 1:
            cmd_lista_blanca(*args)
        elif cmd == "redes" and len(args) == 1:
            return cmd_redes(*args)
        else:
            print(__doc__)
            return 2
    except Exception as e:                                       # noqa: BLE001
        print(f"EXCEPCION {e!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
