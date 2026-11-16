"""generar_plan_pabellones.py: genera el plan de direccionamiento de un campus con pabellones (tablas en Markdown y el JSON que lee capa3.cargar_plan).

    python3 generar_plan_pabellones.py                       # tablas en Markdown
    python3 generar_plan_pabellones.py --json plan.json      # además, el JSON del plan
    python3 generar_plan_pabellones.py --doc ../../docs/PLAN_DIRECCIONAMIENTO.md     # reescribe la zona generada del documento
    python3 generar_plan_pabellones.py --config campus.json  # con los datos REALES (pabellones, pisos, switches por piso)

Datos dados por el equipo: pisos de cada pabellón y que V tiene varios equipos en el 3.º y A pocos. Los switches por piso son SUPUESTOS (se reemplazan con `--config`).
Esquema: 10.<id del pabellón>.<n del switch de acceso>.0/24 (un /24 por switch de acceso; cada pabellón agrega a un /16), gateway virtual .254, DHCP .100-.199.
"""
import argparse
import ipaddress
import json
import sys

PABELLONES = {                      # id = segundo octeto; `switches` = switches de acceso de cada piso (una entrada por piso)
    "V": {"id": 10, "switches": [1, 1, 3]},         # DATO: 3 pisos, varios equipos en el 3.º. SUPUESTO: 1 switch en los pisos 1 y 2 y 3 en el 3.º
    "CIA": {"id": 20, "switches": [2, 2, 2, 2]},    # DATO: 4 pisos. SUPUESTO: 2 switches por piso
    "A": {"id": 30, "switches": [1] * 8},           # DATO: 8 pisos con pocos equipos. SUPUESTO: 1 switch por piso
    "O": {"id": 40, "switches": [2] * 5},           # DATO: 5 pisos. SUPUESTO: 2 switches por piso
}
ESPECIALES = [      # (nombre, switch, prefijo, descripción)
    ("servicios", "DC-SW1", "10.1.0.0/24", "Centro de datos: portal, LDAP, RADIUS, servicios académicos"),
    ("iot", "IOT-SW1", "10.250.0.0/24", "IoT y equipos sin 802.1X (MAB; tabla de rol 30 reservada en R2)"),
]
PUERTOS_USUARIO = 48                # supuesto: 48 puertos de usuario por switch de acceso
GW_MAC = "02:00:5E:00:00:FE"
INICIO, FIN = "<!-- BEGIN-GENERADO -->", "<!-- END-GENERADO -->"


def construir(pabellones=None, especiales=None, puertos=PUERTOS_USUARIO):
    """[{nombre, dispositivo, prefijo, gateway, puertos, pabellon, piso}] ordenado."""
    pabellones = PABELLONES if pabellones is None else pabellones
    especiales = ESPECIALES if especiales is None else especiales
    filas = []
    for nombre, esp_sw, prefijo, _desc in especiales:
        red = ipaddress.ip_network(prefijo)
        filas.append({"nombre": nombre, "dispositivo": esp_sw, "prefijo": prefijo, "gateway": str(red.network_address + 254), "puertos": list(range(1, puertos + 1)),
                      "pabellon": "—", "piso": "—"})
    for pab, d in pabellones.items():
        n = 0
        for piso, cuantos in enumerate(d["switches"], start=1):
            for k in range(1, cuantos + 1):
                n += 1
                if n > 254:
                    raise ValueError(f"el pabellón {pab} necesita más de 254 subredes de acceso")
                filas.append({"nombre": f"{pab}-P{piso}-SW{k}", "dispositivo": f"{pab}-P{piso}-SW{k}", "prefijo": f"10.{d['id']}.{n}.0/24", "gateway": f"10.{d['id']}.{n}.254",
                              "puertos": list(range(1, puertos + 1)), "pabellon": pab, "piso": piso})
    return filas


def a_json(filas):
    return {"gw_mac": GW_MAC, "subredes": [{k: f[k] for k in ("nombre", "dispositivo", "prefijo", "gateway", "puertos")} for f in filas]}


def tablas(filas, pabellones=None, especiales=None, puertos=PUERTOS_USUARIO):
    pabellones = PABELLONES if pabellones is None else pabellones
    especiales = ESPECIALES if especiales is None else especiales
    t = ["### Resumen por pabellón (pisos: dato; switches por piso: supuesto)", "",
         "| Pabellón | Id | Bloque agregado | Pisos | Switches de acceso por piso | Subredes de acceso | Hosts máx. (48 por switch) |", "|---|---|---|---|---|---|---|"]
    total = 0
    for pab, d in pabellones.items():
        subs = sum(d["switches"])
        total += subs
        t.append(f"| {pab} | {d['id']} | `10.{d['id']}.0.0/16` | {len(d['switches'])} | {', '.join(str(x) for x in d['switches'])} | {subs} | {subs * puertos:,} |")
    t += [f"| **Total** | | `10.0.0.0/8` | | | **{total}** | **{total * puertos:,}** |", "",
          "### Subredes especiales", "", "| Subred | Switch | Prefijo | Gateway | Uso |", "|---|---|---|---|---|"]
    for nombre, sw, prefijo, desc in especiales:
        red = ipaddress.ip_network(prefijo)
        t.append(f"| {nombre} | {sw} | `{prefijo}` | `{red.network_address + 254}` | {desc} |")
    t += ["", "### Todas las subredes de acceso", "", "| Pabellón | Piso | Switch de acceso | Subred | Gateway | DHCP | Estáticas |", "|---|---|---|---|---|---|---|"]
    for f in filas:
        if f["pabellon"] == "—":
            continue
        base = f["prefijo"].rsplit(".", 1)[0]
        t.append(f"| {f['pabellon']} | {f['piso']} | {f['dispositivo']} | `{f['prefijo']}` | `{f['gateway']}` | `{base}.100–.199` | `{base}.10–.99`, `{base}.200–.249` |")
    return "\n".join(t)


def reescribir_doc(ruta, contenido):
    texto = open(ruta, encoding="utf-8").read()
    if INICIO not in texto or FIN not in texto:
        raise SystemExit(f"{ruta}: faltan los marcadores {INICIO} y {FIN}")
    antes, resto = texto.split(INICIO, 1)
    _, despues = resto.split(FIN, 1)
    open(ruta, "w", encoding="utf-8", newline="\n").write(f"{antes}{INICIO}\n{contenido}\n{FIN}{despues}")


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--config", help="JSON con {'pabellones': {...}, 'especiales': [...]} reales")
    a.add_argument("--json", help="escribe el plan para capa3.cargar_plan")
    a.add_argument("--doc", help="reescribe la zona generada de este documento")
    arg = a.parse_args(argv)
    pab, esp = None, None
    if arg.config:
        cfg = json.load(open(arg.config, encoding="utf-8"))
        pab, esp = cfg["pabellones"], [tuple(e) for e in cfg.get("especiales", ESPECIALES)]
    filas = construir(pab, esp)
    texto = tablas(filas, pab, esp)
    if arg.json:
        json.dump(a_json(filas), open(arg.json, "w", encoding="utf-8"), indent=1)
    if arg.doc:
        reescribir_doc(arg.doc, texto)
        print(f"Zona generada de {arg.doc} actualizada ({len(filas)} subredes).")
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
