"""consultar_hosts.py: SOLO LECTURA. ¿Qué hosts conoce ONOS y cuáles de los que tienen sesión 802.1X están entre ellos?

    python3 consultar_hosts.py                      # nodo 1 de ONOS (con respaldo en 2 y 3)
    python3 consultar_hosts.py --json               # lo mismo en JSON (para guardar y comparar antes/después)

Sirve para decidir si hace falta `--copiar-arp` en el vigilante (ver docs/DISENO_FASE_E.md): el vigilante arma las reglas de la tabla 60 con los hosts
de /hosts. Un host que ONOS no lista no recibe reglas y su tráfico se descartaría. Hace falta la copia del ARP solo para los que NO aparezcan aquí.

Cómo usarla (cada paso, con la consulta al final):
  1) Sin nadie conectado todavía: el resultado base.
  2) Un usuario con 802.1X inicia sesión (h2-alumno)           -> ¿aparece su MAC en /hosts?
  3) Un invitado pide DHCP (equipo sin 802.1X, con portal)       -> ¿aparece?
  4) Un equipo con IP fija y sin DHCP (host-jhon) hace un ping   -> ¿aparece? (con la copia del ARP apagada solo lo verás si ONOS lo aprende por otro camino)
"""
import argparse
import base64
import json
import re
import sys
import urllib.request

from plan_arboles import NOMBRES

_MAC = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", re.I)
NODOS = ["http://192.168.201.1:8181", "http://192.168.201.2:8181", "http://192.168.201.3:8181"]


def pedir(base, ruta, usuario, timeout=5):
    req = urllib.request.Request(base.rstrip("/") + ruta, headers={"Authorization": "Basic " + base64.b64encode(usuario.encode()).decode(),
                                                                    "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def pedir_con_respaldo(nodos, ruta, usuario):
    errores = []
    for n in nodos:
        try:
            return pedir(n, ruta, usuario), n
        except Exception as e:  # noqa: BLE001 - solo lectura: se prueba el siguiente nodo
            errores.append(f"{n}: {e}")
    raise SystemExit("Ningún ONOS respondió: " + "; ".join(errores))


def hosts_de(datos):
    """[{mac, switch, puerto, ips}] desde GET /onos/v1/hosts."""
    salida = []
    for h in datos.get("hosts", []):
        for loc in h.get("locations", [{}]):
            salida.append({"mac": h.get("mac", "").upper(), "switch": loc.get("elementId", "?"), "puerto": loc.get("port", "?"),
                           "ips": h.get("ipAddresses", [])})
    return salida


def sesiones_de(datos):
    """Sesiones 802.1X de /onos/aaa/app/users: [{mac, estado, ubicacion, usuario}]. El formato exacto depende de la versión: se leen varios nombres de campo."""
    lista = datos.get("users", datos.get("entries", [])) if isinstance(datos, dict) else datos
    salida = []
    for e in lista if isinstance(lista, list) else []:
        crudo = json.dumps(e)
        mac = next((str(e[k]).upper() for k in ("macAddress", "mac", "macAddr") if k in e), None) or (_MAC.search(crudo).group(0).upper() if _MAC.search(crudo) else None)
        salida.append({"mac": mac, "estado": e.get("state", e.get("status", "?")), "ubicacion": e.get("subscriberId", e.get("connectPoint", "?")),
                       "usuario": e.get("username", e.get("user", "?"))})
    return salida


def cruzar(hosts, sesiones):
    """Las sesiones AUTORIZADAS cuya MAC ONOS no tiene como host (esas necesitarían otro camino: la copia del ARP, o aprenderlas de la sesión)."""
    conocidas = {h["mac"] for h in hosts}
    autorizadas = [s for s in sesiones if str(s["estado"]).upper() == "AUTHORIZED" and s["mac"]]
    return {"con_sesion_y_host": [s for s in autorizadas if s["mac"] in conocidas],
            "con_sesion_sin_host": [s for s in autorizadas if s["mac"] not in conocidas],
            "hosts_sin_sesion": [h for h in hosts if h["mac"] not in {s["mac"] for s in autorizadas}]}


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--onos", default=",".join(NODOS))
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--json", action="store_true")
    arg = a.parse_args(argv)
    nodos = [n.strip() for n in arg.onos.split(",") if n.strip()]
    hosts_json, nodo = pedir_con_respaldo(nodos, "/onos/v1/hosts", arg.usuario)
    try:
        aaa_json = pedir(nodo, "/onos/aaa/app/users", arg.usuario)
    except Exception as e:  # noqa: BLE001
        aaa_json, aviso = {}, f"AAA no respondió ({e}): se muestran solo los hosts."
    else:
        aviso = ""
    hosts, sesiones = hosts_de(hosts_json), sesiones_de(aaa_json)
    r = cruzar(hosts, sesiones)
    if arg.json:
        print(json.dumps({"hosts": hosts, "sesiones": sesiones, "cruce": r}, indent=1, sort_keys=True))
        return 0
    print(f"ONOS consultado: {nodo}  (solo lectura)")
    print(f"\nHOSTS que ONOS conoce ({len(hosts)}):")
    for h in sorted(hosts, key=lambda x: (x["switch"], str(x["puerto"]))):
        print(f"  {h['mac']}  {NOMBRES.get(h['switch'], h['switch'])}/{h['puerto']}  IP: {', '.join(h['ips']) or '(ninguna)'}")
    print(f"\nSESIONES 802.1X ({len(sesiones)}){'  ' + aviso if aviso else ''}:")
    for s in sesiones:
        print(f"  {s['mac'] or '(sin MAC)'}  {s['estado']}  usuario {s['usuario']}  en {s['ubicacion']}")
    print("\nCRUCE:")
    print(f"  con sesión y como host : {len(r['con_sesion_y_host'])}")
    print(f"  con sesión SIN host    : {len(r['con_sesion_sin_host'])}  {[s['mac'] for s in r['con_sesion_sin_host']] or ''}")
    print(f"  hosts sin sesión       : {len(r['hosts_sin_sesion'])}  (invitados, equipos con IP fija o el server)")
    if r["con_sesion_sin_host"]:
        print("\n  => ONOS NO aprende por EAPOL a: " + ", ".join(s["mac"] for s in r["con_sesion_sin_host"]) + ". Hace falta otro camino (--copiar-arp o aprender de la sesión).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
