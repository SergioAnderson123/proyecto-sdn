"""generar_politica_local.py: escribe R2/policy/policy.local.yaml de la topología de 14 VM (fase E: tráfico IP de R2 por la tabla 60).

    python3 generar_politica_local.py --mac-portal 52:54:00:xx:xx:xx                     # imprime el YAML, no escribe nada
    python3 generar_politica_local.py --mac-portal ... --salida ../policy/policy.local.yaml
    python3 generar_politica_local.py --mac-portal ... --reenvio core-1,core-2,borde      # reenvío proactivo solo en esos switches

Qué decide (ver docs/DISENO_FASE_E.md):
  - trusted: por switch, sus puertos troncales (los que tienen un enlace en ONOS) y, en los cores, el puerto del server. El resto de puertos
    pasan por la sesión, el portal y la cuarentena.
  - sin_usuarios: los cores (no llevan tablas de rol, DHCP ni cuarentena).
  - puertos_externos: puertos cuyo equipo NO recibe IP por DHCP (host-jhon en el borde).
  - services: los de policy.yaml, todos con la IP única del server; portal: esa misma IP, su MAC y el puerto HTTPS.
  - reenvio: normal (nada cambia), proactivo (todos los switches) o una lista de nombres (solo esos): sirve para activar de a un switch.
Exige los 14 enlaces vivos: con un cable caído sus puertos parecerían de acceso.
"""
import argparse
import os
import sys

import yaml

import caminos as c
from plan_arboles import NOMBRES

AQUI = os.path.dirname(os.path.abspath(__file__))
ENLACES_ESPERADOS = 14
CORES = ("core-1", "core-2")
SERVER_POR_DEFECTO = "core-1:5,core-2:5"
EXTERNOS_POR_DEFECTO = "borde:1"
PORTAL_EN = ("acc-1", "acc-2", "borde")


def _por_nombre(nombres):
    return {v: k for k, v in nombres.items()}


def _dpid(texto, nombres):
    d = _por_nombre(nombres).get(texto, texto)
    if d not in nombres:
        raise ValueError(f"switch desconocido: {texto}")
    return d


def parsear_puertos(texto, nombres=NOMBRES):
    """'core-1:5,core-2:5' -> {dpid: [5]}. Un switch puede repetirse (core-1:5,core-1:6)."""
    salida = {}
    for trozo in [t.strip() for t in (texto or "").split(",") if t.strip()]:
        try:
            nombre, puerto = trozo.rsplit(":", 1)
            salida.setdefault(_dpid(nombre, nombres), []).append(int(puerto))
        except ValueError as e:
            raise ValueError(f"'{trozo}' no es switch:puerto ({e})")
    return {d: sorted(set(p)) for d, p in salida.items()}


def parsear_reenvio(texto, nombres=NOMBRES):
    """normal | proactivo | lista de nombres de switches separados por comas (proactivo solo en esos)."""
    t = (texto or "normal").strip().lower()
    if t in ("normal", "proactivo"):
        return t
    return sorted(_dpid(x.strip(), nombres) for x in t.split(",") if x.strip())


def construir(links, base, ip_server, mac_portal, puerto_portal=8443, server=None, externos=None, reenvio="normal", nombres=NOMBRES,
              enlaces_esperados=ENLACES_ESPERADOS):
    """El diccionario de policy.local.yaml. `links` = GET /links de ONOS; `base` = policy.yaml (para el catálogo de servicios)."""
    grafo = c.grafo_desde_links(links)
    dirigidos = sum(len(v) for v in grafo.values())
    if dirigidos != enlaces_esperados:
        raise ValueError(f"ONOS ve {dirigidos} enlaces y se esperaban {enlaces_esperados}: con un cable caído sus puertos parecerían de acceso.")
    faltan = sorted(nombres[d] for d in nombres if d not in grafo)
    if faltan:
        raise ValueError(f"ONOS no ve estos switches: {', '.join(faltan)}")
    server = parsear_puertos(SERVER_POR_DEFECTO, nombres) if server is None else server
    externos = parsear_puertos(EXTERNOS_POR_DEFECTO, nombres) if externos is None else externos
    trusted = {}
    for d in sorted(grafo):
        troncales = {p for _, p in grafo[d]}
        extra = set(server.get(d, []))
        if extra & troncales:
            raise ValueError(f"{nombres.get(d, d)}: el puerto del server {sorted(extra & troncales)} es un cable entre switches")
        trusted[d] = sorted(troncales | extra)
    for d, ps in externos.items():
        if set(ps) & set(trusted.get(d, [])):
            raise ValueError(f"{nombres.get(d, d)}: un puerto externo no puede ser de confianza ({sorted(set(ps) & set(trusted[d]))})")
    servicios = {}
    for nombre, s in (base.get("services") or {}).items():
        servicios[nombre] = dict(s, ip=ip_server)
    por_nombre = _por_nombre(nombres)
    return {
        "reenvio": reenvio,
        "sin_usuarios": sorted(por_nombre[n] for n in CORES if n in por_nombre),
        "puertos_externos": {d: sorted(ps) for d, ps in sorted(externos.items())},
        "trusted": trusted,
        "portal": {"ip": ip_server, "mac": mac_portal, "puerto": int(puerto_portal),
                   "switches": sorted(por_nombre[n] for n in PORTAL_EN if n in por_nombre), "desvios": []},
        "services": servicios,
    }


def a_yaml(datos):
    cab = ("# Generado por R2/policy/generar_politica_local.py para la topologia de 14 VM (fase E). No se versiona: pisa lo de policy.yaml.\n"
           "# Para volver a la entrega con NORMAL: reenvio: normal (o borrar este archivo) y reiniciar el policy_mapper.\n")
    return cab + yaml.safe_dump(datos, sort_keys=False, default_flow_style=False, allow_unicode=True)


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--onos", default="http://192.168.201.1:8181,http://192.168.201.2:8181,http://192.168.201.3:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--politica", default=os.path.join(AQUI, "policy.yaml"), help="policy.yaml del que salen los servicios")
    a.add_argument("--ip-server", default="10.0.0.10")
    a.add_argument("--mac-portal", required=True, help="MAC del server por la que se llega al portal (la de su ens4)")
    a.add_argument("--puerto-portal", type=int, default=8443)
    a.add_argument("--server", default=SERVER_POR_DEFECTO, help="switch:puerto donde conecta el server (confianza)")
    a.add_argument("--externos", default=EXTERNOS_POR_DEFECTO, help="switch:puerto de equipos externos (sin DHCP); vacío = ninguno")
    a.add_argument("--reenvio", default="normal", help="normal | proactivo | lista de switches (core-1,core-2,borde)")
    a.add_argument("--enlaces", type=int, default=ENLACES_ESPERADOS)
    a.add_argument("--salida", help="archivo de destino; sin él se imprime")
    arg = a.parse_args(argv)
    from onos_multi import OnosCluster
    try:
        onos = OnosCluster([u.strip() for u in arg.onos.split(",") if u.strip()], arg.usuario)
        with open(arg.politica, encoding="utf-8") as f:
            base = yaml.safe_load(f) or {}
        datos = construir(onos.get("/links")["links"], base, arg.ip_server, arg.mac_portal, arg.puerto_portal,
                          parsear_puertos(arg.server), parsear_puertos(arg.externos), parsear_reenvio(arg.reenvio), enlaces_esperados=arg.enlaces)
    except (ValueError, OSError) as e:
        print("DETENIDO:", e)
        return 2
    texto = a_yaml(datos)
    if arg.salida:
        with open(arg.salida, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"Escrito {arg.salida} (reenvio: {datos['reenvio']}). Para que el policy_mapper lo lea: docker restart policy_mapper")
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
