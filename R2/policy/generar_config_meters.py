"""generar_config_meters.py: del plan de subredes (capa3.cargar_plan) saca la configuracion de la app de ONOS "Meters por puerto" (R2/onos-app/campus-meters).

    python3 generar_config_meters.py --plan planes/vnrt_topologia14.json                          # todos los switches de acceso, EAPOL y DHCP
    python3 generar_config_meters.py --plan ... --solo acc-2 --clases dhcp                         # empezar por un switch y una clase
    python3 generar_config_meters.py --plan ... --eapol 20/20 --dhcp 10/10 --salida config.json    # pps/rafaga (los valores del diseño de R1)

Los puertos de acceso son los `puertos` de cada subred del plan cuyo nombre empieza por "acc" (los mismos que usan el vigilante, el Gateway ARP y la app DHCP): un switch nuevo del plan
queda cubierto sin tocar nada mas. Con 20 pps de rafaga 20 para EAPOL y 10 pps de rafaga 10 para DHCP son 2 medidores y 2 reglas por puerto de acceso. Ver docs/DISENO_METERS_POR_PUERTO.md.
"""
import argparse
import json
import sys

import capa3 as l3
from plan_arboles import NOMBRES

CLASES = {"eapol": (20, 20, 50010), "dhcp": (10, 10, 40510)}     # rate (pps), rafaga, prioridad; las prioridades estan en R5/deploy/prioridades.yaml


def construir(plan: l3.Plan, solo=None, clases=None, valores: dict = None) -> dict:
    """La configuracion (clave `campusmeters`) que lee la app. `solo`: nombres de subred a cubrir (por defecto, las que empiezan por "acc"); `clases`: nombres de clase (por defecto, todas);
    `valores`: {clase: (rate, rafaga)} que sustituyen a los del diseño."""
    elegidas = [s for s in plan.subredes if (s.nombre in solo if solo else s.nombre.startswith("acc"))]
    if solo:
        faltan = sorted(set(solo) - {s.nombre for s in plan.subredes})
        if faltan:
            raise l3.PlanInvalido("subredes que no estan en el plan: " + ", ".join(faltan))
    if not elegidas:
        raise l3.PlanInvalido("ninguna subred elegida para los meters")
    nombres = list(clases) if clases else list(CLASES)
    desconocidas = sorted(set(nombres) - set(CLASES))
    if desconocidas:
        raise l3.PlanInvalido("clases desconocidas: " + ", ".join(desconocidas) + " (validas: " + ", ".join(CLASES) + ")")
    salida = {}
    for n in nombres:
        rate, rafaga, prio = CLASES[n]
        if valores and n in valores:
            rate, rafaga = valores[n]
        if rate < 1 or rafaga < 1:
            raise l3.PlanInvalido(f"{n}: rate y rafaga deben ser al menos 1")
        salida[n] = {"rate": int(rate), "burst": int(rafaga), "prioridad": prio}
    por_switch = {}
    for s in elegidas:                                                   # varios tramos del mismo switch se juntan
        d = por_switch.setdefault(s.dispositivo, {"nombre": s.nombre, "device": s.dispositivo, "puertos": []})
        d["puertos"] = sorted(set(d["puertos"]) | set(s.puertos))
    return {"campusmeters": {"tabla": 0, "clases": salida, "dispositivos": list(por_switch.values())}}


def _par(texto: str):
    try:
        a, b = texto.split("/")
        return int(a), int(b)
    except ValueError:
        raise argparse.ArgumentTypeError("use PPS/RAFAGA, por ejemplo 20/20")


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--plan", required=True, help="JSON del plan (con nombres de switch como core-1, acc-1...)")
    a.add_argument("--solo", help="subredes a cubrir, separadas por comas (por defecto, las de acceso)")
    a.add_argument("--clases", help="clases a aplicar, separadas por comas: eapol, dhcp (por defecto, las dos)")
    a.add_argument("--eapol", type=_par, help="PPS/RAFAGA de EAPOL (por defecto 20/20)")
    a.add_argument("--dhcp", type=_par, help="PPS/RAFAGA de DHCP (por defecto 10/10)")
    a.add_argument("--salida")
    arg = a.parse_args(argv)
    valores = {k: v for k, v in (("eapol", arg.eapol), ("dhcp", arg.dhcp)) if v}
    try:
        with open(arg.plan, encoding="utf-8") as f:
            plan = l3.cargar_plan(json.load(f), nombres=NOMBRES)
        cfg = construir(plan, [x for x in arg.solo.split(",") if x] if arg.solo else None, [x for x in arg.clases.split(",") if x] if arg.clases else None, valores)
    except (OSError, ValueError) as e:
        print("DETENIDO:", e)
        return 2
    texto = json.dumps(cfg, indent=1)
    if arg.salida:
        with open(arg.salida, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto + "\n")
        c = cfg["campusmeters"]
        print(f"Escrito {arg.salida} ({len(c['dispositivos'])} switches, {sum(len(d['puertos']) for d in c['dispositivos']) * len(c['clases'])} medidores)")
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
