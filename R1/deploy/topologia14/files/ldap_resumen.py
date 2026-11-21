#!/usr/bin/env python3
"""Resume el contenido de un LDAP y lo compara con la semilla LDIF del repositorio. SOLO LEE (no modifica nada ni contacta con la red).

    ldapsearch -x -LLL -D cn=admin,dc=example,dc=org -w ... -b dc=example,dc=org | python3 ldap_resumen.py --semilla estructura.ldif

Entrada estandar: la salida de ldapsearch (LDIF, solo atributos de usuario; los operacionales de modificacion y replicacion no salen sin `+`).
Imprime un JSON con: numero de entradas, hash canonico (entradas ordenadas por DN y atributos ordenados), hash sin los atributos que ata cada despliegue a su equipo
(description = MAC, physicalDeliveryOfficeName = switch, roomNumber = puerto: en VNRT los fija ldap-ubicacion.sh y NO coinciden con los valores de Mininet de la semilla),
los DN que faltan o sobran respecto a la semilla (la cuenta de replicacion `cn=sdn-repl` se cuenta aparte, como entrada de servicio esperada), y las diferencias de atributos (excepto esos tres) entre lo vivo y la semilla.
"""
import argparse
import base64
import hashlib
import json
import sys

ENTRADAS_DE_SERVICIO = {"cn=sdn-repl,dc=example,dc=org"}      # cuenta de la replicacion multimaestro entre ctrl-1 y ctrl-2: existe en los nodos y NO en la semilla (su clave no va al repositorio)
ATADO_AL_EQUIPO = {"description", "physicaldeliveryofficename", "roomnumber"}      # en minusculas: leer() baja a minusculas los nombres de atributo


def leer(texto):
    """LDIF -> {dn_en_minusculas: {atributo_en_minusculas: [valores ordenados]}}; une las lineas de continuacion y decodifica los valores en base64 (`attr:: ...`)."""
    entradas, bloque = {}, []
    lineas = texto.replace("\r\n", "\n").split("\n")
    logicas = []
    for linea in lineas:
        if linea.startswith(" ") and logicas:
            logicas[-1] += linea[1:]
        else:
            logicas.append(linea)
    for linea in logicas + [""]:
        if linea.strip() == "":
            if bloque:
                dn, attrs = None, {}
                for l in bloque:
                    if l.startswith("#") or ":" not in l:
                        continue
                    clave, _, valor = l.partition(":")
                    if valor.startswith(":"):
                        valor = base64.b64decode(valor[1:].strip()).decode("utf-8", "replace")
                    else:
                        valor = valor.strip()
                    if clave.lower() == "dn":
                        dn = valor.lower().replace(", ", ",")
                    else:
                        attrs.setdefault(clave.lower(), []).append(valor)
                if dn:
                    entradas[dn] = {k: sorted(v) for k, v in attrs.items()}
                bloque = []
        else:
            bloque.append(linea)
    return entradas


def digest(entradas, quitar=()):
    h = hashlib.sha256()
    for dn in sorted(entradas):
        h.update(dn.encode() + b"\n")
        for k in sorted(entradas[dn]):
            if k in quitar:
                continue
            for v in entradas[dn][k]:
                h.update(f"{k}={v}\n".encode())
    return h.hexdigest()


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--semilla", required=True, help="estructura.ldif del repositorio")
    arg = a.parse_args()
    vivo = leer(sys.stdin.read())
    with open(arg.semilla, encoding="utf-8") as f:
        semilla = leer(f.read())
    base = {dn for dn in vivo if dn in ("dc=example,dc=org", "cn=admin,dc=example,dc=org")}      # los crea la imagen, no la semilla
    faltan = sorted(set(semilla) - set(vivo))
    de_servicio = sorted((set(vivo) - set(semilla)) & ENTRADAS_DE_SERVICIO)
    sobran = sorted(set(vivo) - set(semilla) - base - ENTRADAS_DE_SERVICIO)
    difs = []
    for dn in sorted(set(semilla) & set(vivo)):
        for k in sorted(set(semilla[dn]) | set(vivo[dn])):
            if k in ATADO_AL_EQUIPO or k in ("userpassword", "structuralobjectclass", "entryuuid", "creatorsname", "createtimestamp", "entrycsn", "modifiersname", "modifytimestamp"):
                continue
            if semilla[dn].get(k) != vivo[dn].get(k):
                difs.append({"dn": dn, "atributo": k, "semilla": semilla[dn].get(k), "vivo": vivo[dn].get(k)})
    atado = {}
    for dn, attrs in vivo.items():
        if dn.startswith("uid=") and (ATADO_AL_EQUIPO & set(attrs)):
            atado[dn.split(",")[0][4:]] = {k: attrs.get(k, [None])[0] for k in sorted(ATADO_AL_EQUIPO)}
    print(json.dumps({
        "entradas": len(vivo), "entradas_semilla": len(semilla), "base_de_la_imagen": sorted(base),
        "hash": digest(vivo, quitar={"structuralobjectclass", "entryuuid", "creatorsname", "createtimestamp", "entrycsn", "modifiersname", "modifytimestamp"}),
        "hash_sin_atado_al_equipo": digest(vivo, quitar=ATADO_AL_EQUIPO | {"structuralobjectclass", "entryuuid", "creatorsname", "createtimestamp", "entrycsn", "modifiersname", "modifytimestamp"}),
        "faltan_respecto_a_la_semilla": faltan, "sobran_respecto_a_la_semilla": sobran, "entradas_de_servicio": de_servicio,
        "diferencias_de_atributos": difs, "atado_al_equipo": atado}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
