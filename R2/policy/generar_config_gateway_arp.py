"""generar_config_gateway_arp.py: del plan de subredes (capa3.cargar_plan) saca la configuración de la app de ONOS "Gateway ARP" (R2/onos-app/gateway-arp).

    python3 generar_config_gateway_arp.py --plan planes/vnrt_topologia14.json                    # imprime el JSON
    python3 generar_config_gateway_arp.py --plan planes/vnrt_topologia14.json --salida config.json
    python3 generar_config_gateway_arp.py --plan ... --copiar-arp                                # la petición original también sube al controlador

Cada subred del plan produce un gateway: su switch (deviceId `of:...`), la IP del gateway y los puertos de acceso. La MAC común del gateway sale del plan.
"""
import argparse
import json
import os
import sys

import capa3 as l3
from plan_arboles import NOMBRES

AQUI = os.path.dirname(os.path.abspath(__file__))


def construir(plan: l3.Plan, copiar_arp: bool = False, tabla: int = 60, prioridad: int = l3.PRIO_ARP_GATEWAY, plan_prueba: l3.Plan = None) -> dict:
    """La configuración (clave `gatewayarp`) que lee la app de ONOS. Con `plan_prueba` se añaden también sus gateways, cada uno con SU MAC (la de la prueba aislada), en la misma tabla."""
    gateways = [{"device": s.dispositivo, "ip": str(s.gateway), "ports": list(s.puertos)} for s in plan.subredes]
    if plan_prueba is not None:
        gateways += [{"device": s.dispositivo, "ip": str(s.gateway), "ports": list(s.puertos), "gwMac": plan_prueba.gw_mac} for s in plan_prueba.subredes]
    return {"gatewayarp": {"gwMac": plan.gw_mac, "tabla": tabla, "prioridad": prioridad, "copiarArp": bool(copiar_arp), "gateways": gateways}}


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--plan", required=True, help="JSON del plan (con nombres de switch como core-1, acc-1...)")
    a.add_argument("--plan-prueba", help="plan de la PRUEBA AISLADA (subredes 10.99.x con su propia MAC de gateway): se añade a la configuración")
    a.add_argument("--salida")
    a.add_argument("--copiar-arp", action="store_true")
    arg = a.parse_args(argv)
    try:
        with open(arg.plan, encoding="utf-8") as f:
            plan = l3.cargar_plan(json.load(f), nombres=NOMBRES)
    except (OSError, ValueError) as e:
        print("DETENIDO:", e)
        return 2
    plan_prueba = None
    if arg.plan_prueba:
        try:
            with open(arg.plan_prueba, encoding="utf-8") as f:
                plan_prueba = l3.cargar_plan(json.load(f), nombres=NOMBRES)
        except (OSError, ValueError) as e:
            print("DETENIDO:", e)
            return 2
    desconocidos = [s.nombre for s in plan.subredes if not s.dispositivo.startswith("of:")]
    if desconocidos:
        print("DETENIDO: estas subredes usan un switch que no es un deviceId de ONOS ni un nombre conocido:", ", ".join(desconocidos))
        return 2
    texto = json.dumps(construir(plan, arg.copiar_arp, plan_prueba=plan_prueba), indent=1)
    if arg.salida:
        with open(arg.salida, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto + "\n")
        print(f"Escrito {arg.salida} ({len(plan.subredes) + (len(plan_prueba.subredes) if plan_prueba else 0)} gateways)")
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
