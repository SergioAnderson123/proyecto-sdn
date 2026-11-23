#!/usr/bin/env python3
"""registrador_onos.py: registra, con hora, cuanto tarda ONOS en contestar por cada uno de los tres nodos, para cazar las rachas intermitentes de timeouts de Atomix (23-nov).

Cada `--intervalo` segundos (20 por defecto) y desde el controlador donde corre, pide a CADA nodo de ONOS:
  - GET /devices            (el que falla con `ConsistentMapException$Timeout: onos-pipeconf-table`): codigo, milisegundos y cuantos switches ve disponibles.
  - GET /flows/{switch}     (el que falla con `flow-store ... timed out in 15000 ms`): un switch distinto en cada ronda, rotando por los que lista ONOS.
y anade lo que cuesta ver despues: presion de disco (`/proc/pressure/io`), retransmisiones y timeouts de TCP y la carga de la maquina. Una linea por ronda; si algo tarda mas de
`--umbral-ms` o no da 200, la linea termina en `LENTO` (`grep LENTO medidas.log`).

VISTAS DESIGUALES. Compara, switch por switch, lo que dice cada nodo sobre si esta disponible. El 23-nov, tras parar un ONOS, dos nodos en espera se quedaron con los cinco switches como
`available=false` durante horas mientras el maestro los veia bien (docs/DISENO_CAPA3.md, V9 quinquies). Si la discrepancia dura mas de `--gracia-s` (60 s por defecto, para no avisar de los
cambios de mando normales) se escribe una linea `VISTA_DESIGUAL sw=of:... ven=ctrl2 no_ven=ctrl1,ctrl3 dura=75s` (y otra cada `--repetir-s`, 600 s, mientras siga), y al volver a coincidir
`VISTA_IGUAL ... duro=1830s`. Mientras haya alguna, las lineas de ronda terminan ademas en `DESIGUAL` (`grep -E 'VISTA_|DESIGUAL' medidas.log`). Un nodo que no responde no cuenta como «no ve».
Arreglo manual (probado el 23-nov): dar el mando de ese switch a otro nodo y devolverlo (`device-role`; ver consulta_cli_onos.yml).

Es SOLO LECTURA: nunca escribe en ONOS. Carga: 6 peticiones cada 20 s por controlador. Archivo con rotacion (5 MB x 5), sin crecer.
Credenciales y nodos por entorno (ONOS_NODOS=http://ip:8181,..., ONOS_USER, ONOS_PASS), para no ponerlos en la linea de comandos.
"""
import argparse
import base64
import json
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


def disponibilidad_de(cuerpo):
    """{id del switch: disponible?} de la respuesta de /devices, o None si no se entiende."""
    try:
        return {d["id"]: bool(d.get("available")) for d in json.loads(cuerpo)["devices"]}
    except (ValueError, KeyError, TypeError):
        return None


def switches_de(cuerpo):
    """(lista de ids ordenada, cuantos disponibles) de la respuesta de /devices, o ([], None) si no se entiende."""
    mapa = disponibilidad_de(cuerpo)
    if mapa is None:
        return [], None
    return sorted(mapa), sum(mapa.values())


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


class Desigualdad:
    """Vigila los switches sobre los que los nodos NO coinciden en si estan disponibles, y avisa solo de las discrepancias que duran.

    `actualizar({nodo: {switch: disponible}}, ahora)` -> (eventos de texto, hay alguna discrepancia que ya se aviso). Solo cuentan los nodos que contestaron.
    """
    def __init__(self, gracia_s=60, repetir_s=600):
        self.gracia_s, self.repetir_s = gracia_s, repetir_s
        self._est = {}                    # switch -> {"ven": frozenset, "no": frozenset, "desde": t, "avisado": t del ultimo aviso o None}

    @staticmethod
    def _discrepancias(vistas):
        sws = set()
        for v in vistas.values():
            sws.update(v)
        out = {}
        for sw in sws:
            ven = frozenset(n for n, v in vistas.items() if v.get(sw) is True)
            no = frozenset(n for n, v in vistas.items() if v.get(sw) is False)
            if ven and no:
                out[sw] = (ven, no)
        return out

    @staticmethod
    def _txt(sw, ven, no):
        return f"sw={sw} ven={','.join(sorted(ven))} no_ven={','.join(sorted(no))}"

    def actualizar(self, vistas, ahora):
        eventos = []
        actuales = self._discrepancias(vistas) if len(vistas) > 1 else {}
        for sw, (ven, no) in actuales.items():
            e = self._est.get(sw)
            if e is None or e["ven"] != ven or e["no"] != no:
                if e is not None and e["avisado"] is not None:                         # cambio el reparto: lo anterior se cierra
                    eventos.append(f"VISTA_IGUAL {self._txt(sw, e['ven'], e['no'])} (cambio de reparto) duro={int(ahora - e['desde'])}s")
                e = self._est[sw] = {"ven": ven, "no": no, "desde": ahora, "avisado": None}
            dura = ahora - e["desde"]
            if dura >= self.gracia_s and (e["avisado"] is None or ahora - e["avisado"] >= self.repetir_s):
                e["avisado"] = ahora
                eventos.append(f"VISTA_DESIGUAL {self._txt(sw, ven, no)} dura={int(dura)}s")
        for sw in [s for s in self._est if s not in actuales]:
            e = self._est.pop(sw)
            if e["avisado"] is not None:
                eventos.append(f"VISTA_IGUAL sw={sw} duro={int(ahora - e['desde'])}s")
        return eventos, any(e["avisado"] is not None for e in self._est.values())


def linea(local, ronda, por_nodo, extras, ahora, umbral_ms, desigual=False):
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
    return texto + (" LENTO" if lento else "") + (" DESIGUAL" if desigual else ""), lento


def ronda(nodos, auth, timeout, n, ids_conocidos):
    """Una ronda de peticiones. Devuelve (por_nodo, ids de switches vistos, {nodo: {switch: disponible}} de los que contestaron)."""
    por_nodo, ids, vistas = [], list(ids_conocidos), {}
    objetivo = ids[n % len(ids)] if ids else None
    for base in nodos:
        cod, ms, cuerpo = pedir(base, "/devices", auth, timeout)
        mapa = disponibilidad_de(cuerpo) if cod == 200 else None
        vistos, disp = (sorted(mapa), sum(mapa.values())) if mapa is not None else ([], None)
        if mapa is not None:
            vistas[corto(base)] = mapa
        if vistos:
            ids = vistos
        fl = None
        if objetivo:
            fcod, fms, _ = pedir(base, "/flows/" + objetivo, auth, timeout)
            fl = (objetivo, fcod, fms)
        por_nodo.append((base, (cod, ms, disp, len(vistos) or None), fl))
    return por_nodo, ids, vistas


def main(argv=None):
    a = argparse.ArgumentParser(description="Registra la latencia de ONOS por cada nodo y las vistas desiguales de disponibilidad (solo lectura).")
    a.add_argument("--salida", default="/var/log/registrador-onos/medidas.log")
    a.add_argument("--intervalo", type=float, default=20)
    a.add_argument("--timeout", type=float, default=20, help="segundos que se espera cada respuesta (ONOS se rinde a los 15)")
    a.add_argument("--umbral-ms", type=int, default=1000, help="a partir de cuantos ms una peticion se marca LENTO")
    a.add_argument("--gracia-s", type=float, default=60, help="segundos que debe durar una discrepancia de disponibilidad antes de avisar")
    a.add_argument("--repetir-s", type=float, default=600, help="cada cuantos segundos se repite el aviso mientras la discrepancia siga")
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
    ids, n, desig = [], 0, Desigualdad(arg.gracia_s, arg.repetir_s)
    log.info(f"# registrador iniciado desde={arg.local} nodos={','.join(corto(b) for b in nodos)} intervalo={arg.intervalo}s umbral={arg.umbral_ms}ms gracia={arg.gracia_s}s")
    while True:
        t0 = time.monotonic()
        por_nodo, ids, vistas = ronda(nodos, auth, arg.timeout, n, ids)
        ahora = time.time()
        eventos, hay = desig.actualizar(vistas, ahora)
        for e in eventos:
            log.info(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ahora))} desde={arg.local} {e}")
        texto, _ = linea(arg.local, n, por_nodo, contadores(), ahora, arg.umbral_ms, hay)
        log.info(texto)
        n += 1
        time.sleep(max(1.0, arg.intervalo - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main())
