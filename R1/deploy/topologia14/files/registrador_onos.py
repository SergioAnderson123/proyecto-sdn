#!/usr/bin/env python3
"""registrador_onos.py: registra, con hora, cuanto tarda ONOS en contestar por cada uno de los tres nodos, para cazar las rachas intermitentes de timeouts de Atomix (23-nov).

Cada `--intervalo` segundos (20 por defecto) y desde el controlador donde corre, pide a CADA nodo de ONOS:
  - GET /devices            (el que falla con `ConsistentMapException$Timeout: onos-pipeconf-table`): codigo, milisegundos y cuantos switches ve disponibles.
  - GET /flows/{switch}     (el que falla con `flow-store ... timed out in 15000 ms`): un switch distinto en cada ronda, rotando por los que lista ONOS.
y anade lo que cuesta ver despues: presion de disco (`/proc/pressure/io`), retransmisiones y timeouts de TCP y la carga de la maquina. Una linea por ronda; si algo tarda mas de
`--umbral-ms` o no da 200, la linea termina en `LENTO` (`grep LENTO medidas.log`).

Es SOLO LECTURA: nunca escribe en ONOS. Carga: 6 peticiones cada 20 s por controlador. Archivo con rotacion (5 MB x 5), sin crecer.
Credenciales y nodos por entorno (ONOS_NODOS=http://ip:8181,..., ONOS_USER, ONOS_PASS), para no ponerlos en la linea de comandos.
"""
import argparse
import base64
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler


def pedir(base, ruta, auth, timeout):
    """(codigo, milisegundos, cuerpo). El codigo es el HTTP, o el nombre del error si no hubo respuesta (timeout, conexion rechazada...)."""
    t0 = time.monotonic()
    cuerpo = b""
    try:
        peticion = urllib.request.Request(base.rstrip("/") + "/onos/v1" + ruta, headers={"Authorization": auth})
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            cuerpo, codigo = r.read(), r.status
    except urllib.error.HTTPError as e:
        codigo = e.code
    except Exception as e:                                      # noqa: BLE001  (cualquier fallo de red es un dato, no un motivo para parar)
        codigo = type(e).__name__
    return codigo, int((time.monotonic() - t0) * 1000), cuerpo


def switches_de(cuerpo):
    """(lista de ids ordenada, cuantos disponibles) de la respuesta de /devices, o ([], None) si no se entiende."""
    import json
    try:
        ds = json.loads(cuerpo)["devices"]
        return sorted(d["id"] for d in ds), sum(1 for d in ds if d.get("available"))
    except (ValueError, KeyError, TypeError):
        return [], None


def _numeros(texto, clave):
    """De una linea tipo `some avg10=10.19 avg60=6.57 ...` devuelve el avg10 de la que empieza por `clave`."""
    for linea in texto.splitlines():
        if linea.startswith(clave + " "):
            for trozo in linea.split():
                if trozo.startswith("avg10="):
                    return float(trozo[6:])
    return None


def _columna(texto, prefijo, nombre):
    """Valor de la columna `nombre` en /proc/net/snmp o /proc/net/netstat (una linea de cabecera y otra de valores, ambas con el mismo prefijo)."""
    lineas = [l.split() for l in texto.splitlines() if l.startswith(prefijo)]
    for cab, val in zip(lineas[0::2], lineas[1::2]):
        if nombre in cab:
            return int(val[cab.index(nombre)])
    return None


def contadores(raiz="/proc"):
    """Presion de disco, retransmisiones y timeouts de TCP y carga: lo que se mira junto a un atasco. Lo que no se pueda leer queda en None."""
    def leer(ruta):
        try:
            with open(os.path.join(raiz, ruta), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""
    io, snmp, netstat, carga = leer("pressure/io"), leer("net/snmp"), leer("net/netstat"), leer("loadavg")
    return {
        "io_full10": _numeros(io, "full"),
        "io_some10": _numeros(io, "some"),
        "retrans": _columna(snmp, "Tcp:", "RetransSegs"),
        "tcp_to": _columna(netstat, "TcpExt:", "TCPTimeouts"),
        "carga": float(carga.split()[0]) if carga.split() else None,
    }


def corto(base):
    """`http://192.168.201.2:8181` -> `ctrl2` (el ultimo octeto, que en esta red es el numero del controlador)."""
    host = base.split("//")[-1].split(":")[0]
    return "ctrl" + host.rsplit(".", 1)[-1]


def linea(local, ronda, por_nodo, extras, ahora, umbral_ms):
    """Una linea de registro. `por_nodo` = [(base, (cod, ms, disp, total), (dpid, cod, ms))]. Devuelve (texto, lento)."""
    lento = False
    partes = []
    for base, dev, fl in por_nodo:
        cod, ms, disp, total = dev
        s = f"{corto(base)} dev={cod}/{ms}ms/{'?' if disp is None else disp}de{total}"
        lento |= cod != 200 or ms >= umbral_ms
        if fl is not None:
            dpid, fcod, fms = fl
            s += f" flujos[{dpid[-4:]}]={fcod}/{fms}ms"
            lento |= fcod != 200 or fms >= umbral_ms
        partes.append(s)
    fmt = lambda v: "?" if v is None else v      # noqa: E731
    cola = " ".join(f"{k}={fmt(extras.get(k))}" for k in ("io_full10", "io_some10", "retrans", "tcp_to", "carga"))
    texto = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ahora))} desde={local} ronda={ronda} | " + " | ".join(partes) + " | " + cola
    return texto + (" LENTO" if lento else ""), lento


def ronda(nodos, auth, timeout, n, ids_conocidos):
    """Una ronda de peticiones. Devuelve (por_nodo, ids de switches vistos)."""
    por_nodo, ids = [], list(ids_conocidos)
    objetivo = ids[n % len(ids)] if ids else None
    for base in nodos:
        cod, ms, cuerpo = pedir(base, "/devices", auth, timeout)
        vistos, disp = switches_de(cuerpo) if cod == 200 else ([], None)
        if vistos:
            ids = vistos
        fl = None
        if objetivo:
            fcod, fms, _ = pedir(base, "/flows/" + objetivo, auth, timeout)
            fl = (objetivo, fcod, fms)
        por_nodo.append((base, (cod, ms, disp, len(vistos) or None), fl))
    return por_nodo, ids


def main(argv=None):
    a = argparse.ArgumentParser(description="Registra la latencia de ONOS por cada nodo (solo lectura).")
    a.add_argument("--salida", default="/var/log/registrador-onos/medidas.log")
    a.add_argument("--intervalo", type=float, default=20)
    a.add_argument("--timeout", type=float, default=20, help="segundos que se espera cada respuesta (ONOS se rinde a los 15)")
    a.add_argument("--umbral-ms", type=int, default=1000, help="a partir de cuantos ms una peticion se marca LENTO")
    a.add_argument("--local", default=os.uname().nodename if hasattr(os, "uname") else "local")
    arg = a.parse_args(argv)
    nodos = [u.strip() for u in os.environ.get("ONOS_NODOS", "").split(",") if u.strip()]
    if not nodos:
        print("Falta ONOS_NODOS (por ejemplo http://192.168.201.1:8181,http://192.168.201.2:8181)", file=sys.stderr)
        return 2
    auth = "Basic " + base64.b64encode(f"{os.environ.get('ONOS_USER', 'onos')}:{os.environ.get('ONOS_PASS', 'rocks')}".encode()).decode()
    os.makedirs(os.path.dirname(arg.salida), exist_ok=True)
    log = logging.getLogger("registrador")
    log.setLevel(logging.INFO)
    log.addHandler(RotatingFileHandler(arg.salida, maxBytes=5_000_000, backupCount=4, encoding="utf-8"))
    ids, n = [], 0
    log.info(f"# registrador iniciado desde={arg.local} nodos={','.join(corto(b) for b in nodos)} intervalo={arg.intervalo}s umbral={arg.umbral_ms}ms")
    while True:
        t0 = time.monotonic()
        por_nodo, ids = ronda(nodos, auth, arg.timeout, n, ids)
        texto, _ = linea(arg.local, n, por_nodo, contadores(), time.time(), arg.umbral_ms)
        log.info(texto)
        n += 1
        time.sleep(max(1.0, arg.intervalo - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main())
