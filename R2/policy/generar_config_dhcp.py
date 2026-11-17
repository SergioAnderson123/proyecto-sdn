"""generar_config_dhcp.py: del plan de subredes (capa3.cargar_plan) saca la configuracion de la app de ONOS "DHCP por subred" (R2/onos-app/campus-dhcp).

    python3 generar_config_dhcp.py --plan planes/vnrt_topologia14.json                       # imprime el JSON (las subredes de acceso: nombre que empieza por "acc")
    python3 generar_config_dhcp.py --plan ... --subredes acc-1,acc-2 --salida config.json
    python3 generar_config_dhcp.py --plan ... --max-por-puerto 5 --dns 8.8.8.8,1.1.1.1

Por cada subred /24 elegida: ambito dinamico .100 a .199 (el resto queda para IP fijas: .1 a .99 y .200 a .249), servidor DHCP virtual en .250 y gateway el del plan.
Cada subred del PLAN puede traer su propio bloque "dhcp" que cambia esos valores (inicio, fin, servidor, dns y leaseSeg = segundos de concesion), por ejemplo:
    {"nombre": "acc-1", "dispositivo": "acc-1", "prefijo": "10.0.1.0/24", "gateway": "10.0.1.254", "puertos": [1], "dhcp": {"leaseSeg": 900}}
Las subredes de servicios y externa NO llevan DHCP por defecto (sus equipos tienen IP fija). Ver docs/DISENO_DHCP_POR_SUBRED.md.
"""
import argparse
import ipaddress
import json
import os
import sys

import capa3 as l3
from plan_arboles import NOMBRES

SERVIDOR_MAC = "02:00:5E:00:00:FD"


def _ambito(s: l3.Subred, dns, lease: int, extra: dict = None) -> dict:
    """El ambito de una subred: por defecto .100 a .199 en una /24; el bloque `dhcp` del plan (`extra`) puede cambiar inicio, fin, servidor, dns y leaseSeg."""
    extra = extra or {}
    desconocidos = sorted(set(extra) - {"inicio", "fin", "servidor", "dns", "leaseSeg"})
    if desconocidos:
        raise l3.PlanInvalido(f"{s.nombre}: campos desconocidos en el bloque dhcp: {', '.join(desconocidos)}")
    if s.prefijo.prefixlen != 24 and not {"inicio", "fin", "servidor"} <= set(extra):
        raise l3.PlanInvalido(f"{s.nombre}: el ambito por defecto solo esta definido para /24 (es {s.prefijo}); indica inicio, fin y servidor en su bloque dhcp")
    red = s.prefijo.network_address
    a = {"nombre": s.nombre, "device": s.dispositivo, "puertos": list(s.puertos), "prefijo": str(s.prefijo), "gateway": str(s.gateway),
         "inicio": str(extra.get("inicio", red + 100)), "fin": str(extra.get("fin", red + 199)), "servidor": str(extra.get("servidor", red + 250)),
         "dns": list(extra.get("dns", dns)), "leaseSeg": int(extra.get("leaseSeg", lease))}
    ini, fin, srv = (ipaddress.ip_address(a[k]) for k in ("inicio", "fin", "servidor"))
    if not all(x in s.prefijo for x in (ini, fin, srv)) or ini > fin or ini <= s.prefijo.network_address or fin >= s.prefijo.broadcast_address:
        raise l3.PlanInvalido(f"{s.nombre}: el ambito {ini}-{fin} y el servidor {srv} deben estar dentro de {s.prefijo}, sin la direccion de red ni la de difusion")
    if ini <= s.gateway <= fin or ini <= srv <= fin or srv == s.gateway:
        raise l3.PlanInvalido(f"{s.nombre}: el gateway y el servidor DHCP no pueden caer en el ambito dinamico ni coincidir")
    if a["leaseSeg"] < 30:
        raise l3.PlanInvalido(f"{s.nombre}: la concesion debe durar al menos 30 s")
    return a


def construir(plan: l3.Plan, subredes=None, dns=("8.8.8.8",), lease: int = 3600, max_por_puerto: int = 3, servidor_mac: str = SERVIDOR_MAC, politica: dict = None, extras: dict = None,
              presencia: dict = None) -> dict:
    """La configuracion (clave `campusdhcp`) que lee la app. `subredes` = nombres a servir (por defecto, las que empiezan por "acc")."""
    elegidas = [s for s in plan.subredes if (s.nombre in subredes if subredes else s.nombre.startswith("acc"))]
    if subredes:
        faltan = sorted(set(subredes) - {s.nombre for s in plan.subredes})
        if faltan:
            raise l3.PlanInvalido("subredes que no estan en el plan: " + ", ".join(faltan))
    if not elegidas:
        raise l3.PlanInvalido("ninguna subred elegida para DHCP")
    pol = {"maxPorPuerto": max_por_puerto, "ofertaSeg": 15, "solicitudesPorSeg": 2.0, "rafaga": 8, "declinadaSeg": 300}
    pol.update(politica or {})
    pres = {"intervaloSeg": 5, "intentos": 3, "sondeoLentoSeg": 120}                  # sondeo de presencia: 3 ARP cada 5 s tras una sospecha (15 s) y una comprobacion cada 120 s
    pres.update(presencia or {})
    return {"campusdhcp": {"servidorMac": servidor_mac, "gwMac": plan.gw_mac, "politica": pol, "presencia": pres,
                           "subredes": [_ambito(s, dns, lease, (extras or {}).get(s.nombre)) for s in elegidas]}}


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--plan", required=True, help="JSON del plan (con nombres de switch como core-1, acc-1...)")
    a.add_argument("--subredes", help="nombres separados por comas (por defecto, las de acceso)")
    a.add_argument("--dns", default="8.8.8.8", help="servidores DNS separados por comas")
    a.add_argument("--lease", type=int, default=3600, help="segundos de concesion por defecto (cada subred del plan puede traer el suyo en su bloque dhcp)")
    a.add_argument("--max-por-puerto", type=int, default=3, help="concesiones y ofertas simultaneas por puerto de acceso (defensa contra el agotamiento)")
    a.add_argument("--pm-url", default="", help="URL del Policy Mapper (http://IP:5000) al que se avisa de un equipo ausente; sin ella solo se libera la IP")
    a.add_argument("--sondeo-lento", type=int, default=120, help="segundos entre comprobaciones de presencia de cada equipo con concesion (0 = solo tras una sospecha)")
    a.add_argument("--salida")
    arg = a.parse_args(argv)
    try:
        with open(arg.plan, encoding="utf-8") as f:
            datos = json.load(f)
        plan = l3.cargar_plan(datos, nombres=NOMBRES)
        extras = {s.get("nombre"): s["dhcp"] for s in datos.get("subredes", []) if isinstance(s.get("dhcp"), dict)}
        cfg = construir(plan, [x for x in arg.subredes.split(",") if x] if arg.subredes else None, [x for x in arg.dns.split(",") if x], arg.lease, arg.max_por_puerto, extras=extras,
                        presencia={"sondeoLentoSeg": arg.sondeo_lento, **({"policyMapperUrl": arg.pm_url, "policyMapperToken": os.environ.get("PRESENCIA_TOKEN", "")} if arg.pm_url else {})})
    except (OSError, ValueError) as e:
        print("DETENIDO:", e)
        return 2
    texto = json.dumps(cfg, indent=1)
    if arg.salida:
        with open(arg.salida, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto + "\n")
        print(f"Escrito {arg.salida} ({len(cfg['campusdhcp']['subredes'])} ambitos)")
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
