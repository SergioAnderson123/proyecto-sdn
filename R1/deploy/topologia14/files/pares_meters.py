#!/usr/bin/env python3
"""Empareja cada regla con medidor de la tabla 0 con el medidor que usa, y comprueba que corresponde a su clase. SOLO LEE (ovs-ofctl dump-meters y dump-flows).

    python3 pares_meters.py [bridge]

Esperado (docs/DISENO_METERS_POR_PUERTO.md): la regla de prioridad 50010 (EAPOL) usa un medidor de 20 pps con rafaga 20 y la de 40510 (DHCP) uno de 10 pps con rafaga 10, cada una con SU medidor.
Una linea por regla: `prio=50010 meter:2 rate=20 burst=20 OK` (o `MAL`), y al final `RESULTADO emparejamiento=OK|MAL sin_regla=[ids de medidores sin ninguna regla]`.
Lo escribi tras el 92 en modo real: tras reiniciar Open vSwitch las dos reglas quedaron atadas al medidor 1 (el de DHCP) y el de EAPOL (20 pps) sin uso; el playbook solo comparaba cantidades.
"""
import re
import subprocess
import sys

ESPERADO = {50010: (20, 20), 40510: (10, 10)}


def correr(args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def main():
    puente = sys.argv[1] if len(sys.argv) > 1 else "br0"
    medidores = {}
    for m in re.finditer(r"meter=(\d+)[^\n]*\n\s*type=drop rate=(\d+) burst_size=(\d+)", correr(["ovs-ofctl", "-O", "OpenFlow13", "dump-meters", puente])):
        medidores[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    usados, bien = set(), True
    lineas = []
    for l in correr(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", puente, "table=0"]).splitlines():
        m = re.search(r"priority=(\d+),.*actions=meter:(\d+)", l)
        if not m:
            continue
        prio, mid = int(m.group(1)), int(m.group(2))
        usados.add(mid)
        valores = medidores.get(mid)
        ok = valores is not None and ESPERADO.get(prio) == valores
        bien = bien and ok
        lineas.append(f"prio={prio} meter:{mid} rate={valores[0] if valores else '?'} burst={valores[1] if valores else '?'} {'OK' if ok else 'MAL'}")
    print("\n".join(sorted(lineas)))
    mids = [i for i in medidores if i not in usados]
    reglas = len(lineas)
    unicos = len(usados) == reglas                      # cada regla con su propio medidor
    print(f"RESULTADO emparejamiento={'OK' if bien and unicos and reglas else 'MAL'} reglas={reglas} medidores={len(medidores)} sin_regla={sorted(mids)}")


if __name__ == "__main__":
    main()
