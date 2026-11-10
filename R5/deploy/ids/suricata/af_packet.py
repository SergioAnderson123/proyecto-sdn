"""Reescribe la sección `af-packet:` de suricata.yaml para capturar en varias interfaces a la vez.

Cada sensor (nodo-sec-1 y nodo-sec-2) tiene una NIC de espejo por switch (core-1, core-2...), así que Suricata debe escuchar en todas.
Cada interfaz lleva su propio `cluster-id`: AF_PACKET reparte la carga por flujo dentro de una interfaz y los identificadores deben ser
distintos entre interfaces. Idempotente: se puede aplicar en cada arranque.

Uso (lo llama entrypoint.sh):  python3 af_packet.py /etc/suricata/suricata.yaml ens4,ens5,ens6,ens7
"""
import re
import sys

# la sección va desde `af-packet:` hasta la siguiente clave de primer nivel: se consumen líneas en blanco, sangradas o comentarios
SECCION = re.compile(r"^af-packet:[ \t]*\n(?:(?:[ \t].*|#.*)?\n)*", re.M)


def lista_interfaces(texto):
    out = [x.strip() for x in texto.split(",") if x.strip()]
    if not out:
        raise ValueError("falta IDS_IFACES (interfaces de espejo, separadas por comas)")
    if len(set(out)) != len(out):
        raise ValueError(f"interfaces repetidas en IDS_IFACES: {out}")
    return out


def bloque(interfaces):
    partes = ["af-packet:\n"]
    for n, iface in enumerate(interfaces, 1):
        partes.append(f"  - interface: {iface}\n    cluster-id: {90 + n}\n    cluster-type: cluster_flow\n    defrag: yes\n")
    return "".join(partes) + "\n"


def reemplazar(texto, interfaces):
    nuevo, n = SECCION.subn(lambda _: bloque(interfaces), texto, count=1)
    if n == 0:                                   # el archivo no tenía la sección: se agrega al final
        nuevo = texto.rstrip("\n") + "\n\n" + bloque(interfaces)
    return nuevo


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    ruta = sys.argv[1]
    ifaces = lista_interfaces(sys.argv[2])
    with open(ruta) as f:
        contenido = f.read()
    with open(ruta, "w") as f:
        f.write(reemplazar(contenido, ifaces))
    print(f"[r5] af-packet en: {', '.join(ifaces)}")
