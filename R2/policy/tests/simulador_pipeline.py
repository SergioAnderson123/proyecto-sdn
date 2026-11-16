"""Simulador de la tubería OpenFlow de R2 (sin red): ejecuta los flujos REALES que genera policy_tables.py y flujos_proactivos.py.

Un switch: se parte de la tabla 0, gana el flujo de mayor prioridad que casa, se ejecutan sus instrucciones en orden (VLAN, reescrituras, salidas) y, si
hay `TABLE`, se continúa en esa tabla. Sin flujo que case = descarte (como un switch con fail-mode secure). `OUTPUT NORMAL/FLOOD/ALL` se anotan como
inundaciones (en el modo proactivo no debe haber ninguna).
Varios switches: `entregar` sigue el paquete por los cables hasta los hosts, cuenta cuántas veces llega a cada switch y detecta lazos.
"""
import copy
import ipaddress

INUNDAN = ("NORMAL", "FLOOD", "ALL")


class SimError(Exception):
    pass


def casa(f, pkt):
    for cr in f["selector"]["criteria"]:
        t = cr["type"]
        if t == "IN_PORT":
            ok = pkt.get("in_port") == int(cr["port"])
        elif t == "ETH_SRC":
            ok = str(pkt.get("eth_src", "")).lower() == cr["mac"].lower()
        elif t == "ETH_DST":
            ok = str(pkt.get("eth_dst", "")).lower() == cr["mac"].lower()
        elif t == "ETH_TYPE":
            ok = pkt.get("eth_type") == int(str(cr["ethType"]), 16)
        elif t == "VLAN_VID":
            ok = pkt.get("vlan") == int(cr["vlanId"])
        elif t in ("IPV4_DST", "IPV4_SRC"):
            clave = "ip_dst" if t == "IPV4_DST" else "ip_src"
            ok = pkt.get(clave) is not None and ipaddress.ip_address(pkt[clave]) in ipaddress.ip_network(cr["ip"], strict=False)
        elif t == "IP_PROTO":
            ok = pkt.get("ip_proto") == int(cr["protocol"])
        elif t in ("TCP_DST", "TCP_SRC", "UDP_DST", "UDP_SRC"):
            ok = pkt.get(t.lower()) == int(cr.get("tcpPort", cr.get("udpPort")))
        else:
            raise SimError("criterio no soportado por el simulador: " + t)
        if not ok:
            return False
    return True


def ejecutar(flujos, pkt):
    """Un switch. Devuelve {'salidas': [puertos], 'controlador': bool, 'inunda': [...], 'pkt': paquete final, 'goto': [tablas]}."""
    pkt = copy.deepcopy(pkt)
    res = {"salidas": [], "controlador": False, "inunda": [], "goto": [], "pkt": pkt}
    tabla = 0
    for _ in range(10):
        candidatos = [f for f in flujos if f["tableId"] == tabla and casa(f, pkt)]
        if not candidatos:
            break                                              # sin flujo: descarte
        f = max(candidatos, key=lambda x: x["priority"])
        siguiente = None
        for ins in f["treatment"].get("instructions", []):
            t = ins["type"]
            if t == "OUTPUT":
                p = str(ins["port"]).upper()
                if p in INUNDAN:
                    res["inunda"].append(p)
                    res["salidas"].append(p)
                elif p == "CONTROLLER":
                    res["controlador"] = True
                else:
                    res["salidas"].append(int(p))
            elif t == "L2MODIFICATION":
                s = ins["subtype"]
                if s == "VLAN_ID":
                    pkt["vlan"] = int(ins["vlanId"])
                elif s == "VLAN_POP":
                    pkt["vlan"] = None
                elif s == "ETH_DST":
                    pkt["eth_dst"] = ins["mac"]
                elif s == "ETH_SRC":
                    pkt["eth_src"] = ins["mac"]
            elif t == "L3MODIFICATION":
                pkt["ip_dst" if ins["subtype"] == "IPV4_DST" else "ip_src"] = ins["ip"]
            elif t == "L4MODIFICATION":
                pkt["tcp_dst" if ins["subtype"] == "TCP_DST" else "tcp_src"] = int(ins["tcpPort"])
            elif t == "TABLE":
                siguiente = int(ins["tableId"])
        if siguiente is None:
            break
        if siguiente <= tabla:
            raise SimError("goto_table hacia atrás")
        res["goto"].append(siguiente)
        tabla = siguiente
    return res


def entregar(red, dpid, puerto, pkt, max_saltos=40):
    """Sigue un paquete que entra por (dpid, puerto) hasta los hosts.

    red = {"flujos": {dpid: [flujos]}, "fisico": {(dpid, puerto): (dpid2, puerto2)}, "hosts": {(dpid, puerto): nombre}}
    Devuelve {'entregado': {host: veces}, 'llegadas': {dpid: veces}, 'controlador': [dpid...], 'inunda': [...], 'cortado': bool}."""
    out = {"entregado": {}, "llegadas": {}, "controlador": [], "inunda": [], "cortado": False}
    cola = [(dpid, puerto, dict(pkt, in_port=puerto))]
    pasos = 0
    while cola:
        pasos += 1
        if pasos > max_saltos:
            out["cortado"] = True
            return out
        d, p, paquete = cola.pop(0)
        paquete["in_port"] = p
        out["llegadas"][d] = out["llegadas"].get(d, 0) + 1
        r = ejecutar(red["flujos"].get(d, []), paquete)
        out["inunda"] += r["inunda"]
        if r["controlador"]:
            out["controlador"].append(d)
        for s in r["salidas"]:
            if isinstance(s, int):
                if (d, s) in red["hosts"]:
                    h = red["hosts"][(d, s)]
                    out["entregado"][h] = out["entregado"].get(h, 0) + 1
                elif (d, s) in red["fisico"]:
                    d2, p2 = red["fisico"][(d, s)]
                    cola.append((d2, p2, dict(r["pkt"])))
    return out
