"""ovs_formato.py: convierte un flujo en el formato que usa ONOS (selector/treatment) a una línea de `ovs-ofctl add-flows`.

Sirve para la PRUEBA DE VIABILIDAD de la capa 3: instalar flujos directamente en Open vSwitch (sin pasar por el REST de ONOS, que no acepta ARP_TPA ni las extensiones de copia de campos)
y comprobar que el diseño funciona con tráfico real. Soporta solo lo que usa `capa3.py`; cualquier otra cosa lanza ValueError (mejor fallar que instalar algo distinto de lo previsto).

    linea = a_ovs(flujo, cookie=0x99c3000000000001)
    -> 'cookie=0x99c3000000000001,table=62,priority=300,in_port=1,arp,arp_op=1,arp_tpa=10.99.1.254 actions=move:NXM_OF_ETH_SRC[]->NXM_OF_ETH_DST[],...'
"""
import ipaddress
from typing import List


def _mac_hex(mac: str) -> str:
    return "0x" + mac.replace(":", "").lower()


def _ip_hex(ip: str) -> str:
    return "0x%08x" % int(ipaddress.ip_address(ip))


_MOVE = {
    "NICIRA_MOVE_ETH_SRC_TO_DST": "move:NXM_OF_ETH_SRC[]->NXM_OF_ETH_DST[]",
    "NICIRA_MOVE_ARP_SHA_TO_THA": "move:NXM_NX_ARP_SHA[]->NXM_NX_ARP_THA[]",
    "NICIRA_MOVE_ARP_SPA_TO_TPA": "move:NXM_OF_ARP_SPA[]->NXM_OF_ARP_TPA[]",
}


def _criterio(c: dict) -> List[str]:
    t = c["type"]
    if t == "IN_PORT":
        return [f"in_port={int(c['port'])}"]
    if t == "ETH_TYPE":
        et = int(str(c["ethType"]), 16)
        return {0x0800: ["ip"], 0x0806: ["arp"]}.get(et, [f"dl_type=0x{et:04x}"])
    if t == "ETH_DST":
        return [f"dl_dst={c['mac'].lower()}"]
    if t == "ETH_SRC":
        return [f"dl_src={c['mac'].lower()}"]
    if t == "IPV4_DST":
        return [f"nw_dst={c['ip']}"]
    if t == "IPV4_SRC":
        return [f"nw_src={c['ip']}"]
    if t == "ARP_OP":
        return [f"arp_op={int(c['op'])}"]
    if t == "ARP_TPA":
        return [f"arp_tpa={c['ip']}"]
    raise ValueError("criterio no soportado: " + t)


def _instruccion(i: dict) -> str:
    t = i["type"]
    if t == "OUTPUT":
        p = str(i["port"]).upper()
        return {"IN_PORT": "in_port", "CONTROLLER": "controller"}.get(p, f"output:{int(p)}" if p.isdigit() else "")
    if t == "TABLE":
        return f"goto_table:{int(i['tableId'])}"
    if t == "L2MODIFICATION":
        if i["subtype"] == "ETH_SRC":
            return f"mod_dl_src:{i['mac'].lower()}"
        if i["subtype"] == "ETH_DST":
            return f"mod_dl_dst:{i['mac'].lower()}"
    if t == "L3MODIFICATION":
        s = i["subtype"]
        if s == "DEC_TTL":
            return "dec_ttl"
        if s == "ARP_OP":
            return f"load:0x{int(i['op']):x}->NXM_OF_ARP_OP[]"
        if s == "ARP_SHA":
            return f"load:{_mac_hex(i['mac'])}->NXM_NX_ARP_SHA[]"
        if s == "ARP_SPA":
            return f"load:{_ip_hex(i['ip'])}->NXM_OF_ARP_SPA[]"
    if t == "EXTENSION" and i["extensionType"] in _MOVE:
        return _MOVE[i["extensionType"]]
    raise ValueError("instrucción no soportada: " + str(i))


def a_ovs(f: dict, cookie: int) -> str:
    """Una línea de `ovs-ofctl add-flows` (OpenFlow 1.3)."""
    ins = [_instruccion(i) for i in f["treatment"]["instructions"]]
    if any(not x for x in ins):
        raise ValueError(f"salida no soportada en {f['treatment']['instructions']}")
    acciones = ",".join(ins) if ins else "drop"
    match = []
    for c in f["selector"]["criteria"]:
        match += _criterio(c)
    cuerpo = ",".join([f"cookie=0x{cookie:x}", f"table={int(f['tableId'])}", f"priority={int(f['priority'])}"] + match)
    return f"{cuerpo} actions={acciones}"
