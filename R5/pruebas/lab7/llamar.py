#!/usr/bin/env python3
"""Ayudante de los scripts bash del Lab 7 (r5_01.sh ... r5_06.sh): llama a UNA funcion del integrador con la entrada recibida
por la linea de comandos y muestra el resultado, sin ONOS ni Suricata (ONOS se sustituye por un doble que registra las peticiones).

Subcomandos (desde la raiz del repositorio):
  llamar.py parse LINEA                 parse_ids_alert(LINEA): imprime los campos extraidos o None
  llamar.py parse-sin-texto             parse_ids_alert(None): una entrada que ni siquiera es texto
  llamar.py procesar LINEA_JSON         parse_ids_alert y luego procesar(evento): no debe lanzar excepcion ni bloquear nada
  llamar.py consumir ARCHIVO ESTADO     consumir_eve(estado) sobre ARCHIVO; el estado (offset e IPs bloqueadas) se guarda en ESTADO
Codigo de salida: 0 si la funcion termino sin excepcion; 1 si lanzo una.
"""
import importlib
import json
import logging
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))          # .../R5
sys.path.insert(0, os.path.join(RAIZ, "codigo"))
os.environ.update({"ONOS_URL": "http://onos.test", "ONOS_USER": "u", "ONOS_PASS": "p", "HOME_NET": "10.0.0.0/24",
                   "LISTA_BLANCA": "10.0.0.99/32,10.0.0.10/32", "CORE_DEVICE": "of:0000000000000001", "MAX_SEVERITY": "2",
                   "P_R5": "42000", "BLOCK_SEC": "300"})
integ = importlib.import_module("integrador")
logging.disable(logging.CRITICAL)


class Resp:
    def __init__(self, cuerpo=None, codigo=200):
        self._c, self.status_code, self.text = cuerpo or {}, codigo, json.dumps(cuerpo or {})

    def json(self):
        return self._c


class OnosFalso:
    """Doble de ONOS: registra las peticiones y responde lo minimo para que el integrador funcione."""

    def __init__(self):
        self.llamadas = []

    def __call__(self, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta))
        if metodo == "GET" and ruta == "/hosts":
            return Resp({"hosts": []})
        if metodo == "POST" and ruta == "/flows":
            dev = kw["json"]["flows"][0]["deviceId"]
            return Resp({"flows": [{"deviceId": dev, "flowId": "777"}]})
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
        else:
            print(__doc__)
            return 2
    except Exception as e:                                       # noqa: BLE001
        print(f"EXCEPCION {e!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
