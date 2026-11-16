#!/usr/bin/env python3
"""Dice a que interfaz del sistema (ens5...) corresponde un puerto OpenFlow de br0. Solo lee. Se niega con la interfaz de gestion (192.168.201.x).

    python3 nic_de_puerto.py 2
Imprime solo el nombre de la interfaz. Codigos de salida: 2 = el puerto no existe en br0; 3 = es la interfaz de gestion (no se debe bajar).
"""
import re
import subprocess
import sys


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("uso: nic_de_puerto.py <numero de puerto>")
    salida = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "show", "br0"], capture_output=True, text=True, check=True).stdout
    mapa = {int(m.group(1)): m.group(2) for m in re.finditer(r"^\s*(\d+)\((\S+?)\):", salida, re.M)}
    nic = mapa.get(int(sys.argv[1]))
    if nic is None:
        sys.exit(2)
    direcciones = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", nic], capture_output=True, text=True).stdout
    if "192.168.201." in direcciones:
        sys.exit(3)
    print(nic)


if __name__ == "__main__":
    main()
