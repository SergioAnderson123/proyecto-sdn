"""limpiar_r2.py: borra los flujos antiguos de R2 (appId org.campus.r2) de ciertos switches, para pasarlos al reenvío proactivo sin restos.

    python3 limpiar_r2.py --dispositivos core-1,core-2,borde            # SOLO LEE: cuenta lo que borraría
    python3 limpiar_r2.py --dispositivos core-1,core-2,borde --aplicar  # borra

Por qué: el Policy Mapper solo AÑADE flujos. Al pasar un switch a la política nueva, lo que esa política ya no genera (por ejemplo la cuarentena, el DHCP y
las tablas de rol de un core, que ahora es `sin_usuarios`) se quedaría en el switch. Aquí se quitan los de R2 de los switches indicados; el Policy Mapper
reinstala enseguida los nuevos al reiniciarlo. En medio no hay flujos de R2 y el tráfico IP se descarta (falla cerrado: nada se inunda).

Solo toca el appId org.campus.r2: no los de ONOS (LLDP, ARP), ni los de la tabla 60 (r2.proactivo), ni los de sesión (R1), ni los de R5.
"""
import argparse
import collections
import sys
import urllib.error

from instalar_proactivo import Onos
from plan_arboles import NOMBRES

APP_R2 = "org.campus.r2"


def _dpid(texto, nombres=NOMBRES):
    d = {v: k for k, v in nombres.items()}.get(texto, texto)
    if d not in nombres:
        raise ValueError(f"switch desconocido: {texto}")
    return d


def flujos_de_r2(onos, dpid):
    """Los flujos del appId de R2 en ese switch (cualquier tabla)."""
    return [f for f in onos.get(f"/flows/{dpid}").get("flows", []) if f.get("appId") == APP_R2]


def resumen(flujos):
    """{tabla: cantidad} y cuántos llevan NORMAL/FLOOD/ALL."""
    tablas = collections.Counter(int(f.get("tableId", 0)) for f in flujos)
    inundan = sum(1 for f in flujos if any(i.get("type") == "OUTPUT" and str(i.get("port")).upper() in ("NORMAL", "FLOOD", "ALL")
                                            for i in f.get("treatment", {}).get("instructions", [])))
    return dict(sorted(tablas.items())), inundan


def limpiar(onos, dispositivos, aplicar=False, log=print, nombres=NOMBRES):
    """Devuelve {dpid: cantidad borrada (o que se borraría)}."""
    hecho = {}
    for d in dispositivos:
        flujos = flujos_de_r2(onos, d)
        tablas, inundan = resumen(flujos)
        log(f"{nombres.get(d, d)}: {len(flujos)} flujos de R2 por tabla {tablas}; {inundan} con NORMAL/FLOOD/ALL")
        if aplicar:
            for f in flujos:
                try:
                    onos.borrar(d, f["id"])
                except urllib.error.HTTPError as e:
                    if e.code != 404:                       # 404: ya no estaba
                        raise
        hecho[d] = len(flujos)
    return hecho


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--dispositivos", required=True, help="nombres separados por comas: core-1,core-2,borde,acc-1,acc-2")
    a.add_argument("--onos", default="http://192.168.201.1:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--aplicar", action="store_true", help="sin esta bandera solo cuenta")
    arg = a.parse_args(argv)
    try:
        dispositivos = [_dpid(x.strip()) for x in arg.dispositivos.split(",") if x.strip()]
    except ValueError as e:
        print("DETENIDO:", e)
        return 2
    if not dispositivos:
        print("DETENIDO: indica al menos un switch.")
        return 2
    onos = Onos(arg.onos, arg.usuario)
    hecho = limpiar(onos, dispositivos, aplicar=arg.aplicar)
    print(f"{'BORRADOS' if arg.aplicar else 'Se borrarían'}: {sum(hecho.values())} flujos de R2 en {len(hecho)} switches."
          + ("" if arg.aplicar else " Repite con --aplicar para borrar."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
