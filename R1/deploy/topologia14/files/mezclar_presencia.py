#!/usr/bin/env python3
"""Mete en la configuracion generada del DHCP la seccion `presencia` que YA esta publicada en ONOS (URL y clave del Policy Mapper, sondeo lento), para que reinstalar la app con 82 no la borre.

    curl ... {api}/network/configuration/apps/org.campus.dhcp | python3 mezclar_presencia.py /tmp/campus_dhcp_config.json

Lee de la entrada estandar la configuracion actual de ONOS. Si no tiene `policyMapperUrl` no hay nada que conservar y deja el archivo como esta. Nunca imprime la clave (solo los nombres de los campos).
Motivo: 82 generaba la configuracion sin --pm-url y la publicaba encima de la de 86; la app seguia liberando la IP de un equipo ausente pero ya no avisaba al Policy Mapper y la sesion no se cerraba.
"""
import json
import sys


def main():
    destino = sys.argv[1]
    try:
        vivo = json.load(sys.stdin)["campusdhcp"]["presencia"]
    except Exception:
        vivo = {}
    if not isinstance(vivo, dict) or not vivo.get("policyMapperUrl"):
        print("presencia: no hay datos del Policy Mapper publicados; se deja la configuracion generada")
        return
    with open(destino, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["campusdhcp"].setdefault("presencia", {}).update(vivo)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print("presencia: se conserva lo ya publicado (" + ", ".join(sorted(vivo)) + ")")


if __name__ == "__main__":
    main()
