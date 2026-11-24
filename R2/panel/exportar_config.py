#!/usr/bin/env python3
"""exportar_config.py: foto de la CONFIGURACIÓN ESTÁTICA de la red para el panel (docs/DISENO_PANEL_ADMIN.md §12). Se ejecuta en el borde (que tiene PyYAML por Ansible), NO en el panel: así el panel
sigue usando solo la biblioteca estándar y no necesita leer YAML.

Entrada: una carpeta con una subcarpeta por FUENTE (`repo`, `ctrl-1`, `ctrl-2`, `ctrl-3`) y, dentro, cada archivo con su nombre lógico (lo deja `R1/deploy/topologia14/panel_config.yml`).
Salida: un JSON con
  - `archivos`: por cada archivo fijo (policy.yaml, policy.local.yaml, plan de subredes, hosts estáticos, unidad del vigilante, prioridades), sus copias (qué fuente la tiene, huella y tamaño),
    si son iguales o DIVERGEN entre sí (por ejemplo, el repositorio frente a lo desplegado en cada réplica) y el diff;
  - `politica`: lo que dice policy.yaml (+ policy.local.yaml) ya interpretado: roles, matriz rol/servicio, catálogo, portal, ascenso, cuarentena, los flujos que R2 instala en la tabla de cada rol y los
    que espera en cada switch (para compararlos con los instalados). Usa el MISMO código que el mapper (`policy_tables`), no una copia.
No escribe en la red ni en ningún servicio."""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from typing import Dict, List, Optional

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "..", "policy"))
import flujos  # noqa: E402

FUENTES = ["repo", "ctrl-1", "ctrl-2", "ctrl-3"]
MAX_TEXTO = 200_000
APP_R2 = "org.campus.r2"
ARCHIVOS = [
    {"id": "policy.yaml", "titulo": "Política de acceso (policy.yaml)", "formato": "yaml",
     "descripcion": "Roles (grupo de LDAP -> matriz -> tabla/VLAN), la matriz de permisos rol/servicio, el catálogo de servicios, el portal, el ascenso y la cuarentena. Lo lee el Policy Mapper al arrancar: un cambio exige reiniciar cada réplica."},
    {"id": "policy.local.yaml", "titulo": "Política propia de la slice (policy.local.yaml)", "formato": "yaml",
     "descripcion": "Lo propio de este despliegue: puertos de confianza, IP y MAC del portal, reenvío proactivo. No se versiona: lo genera 70_fase_e.yml en cada nodo del mapper y pisa lo de policy.yaml."},
    {"id": "plan_capa3.json", "titulo": "Plan de subredes de la capa 3", "formato": "json",
     "descripcion": "Subredes, gateways, ámbitos DHCP y la ruta por defecto al exterior. Lo leen el vigilante (--subredes) y los generadores de las apps de ONOS. En el repo se llama vnrt_topologia14.json."},
    {"id": "hosts_fijos_capa3.json", "titulo": "Hosts estáticos del vigilante", "formato": "json",
     "descripcion": "Equipos con IP fija que ONOS no aprende solo (hoy, el server). Mandan sobre lo que diga ONOS."},
    {"id": "vigilante-capa3.service", "titulo": "Parámetros del vigilante (unidad systemd)", "formato": "texto",
     "descripcion": "Con qué argumentos corre el vigilante en cada nodo: intervalos, esperas de estabilidad, si aplica cambios, quién es el principal y quién el respaldo."},
    {"id": "prioridades.yaml", "titulo": "Mapa de prioridades OpenFlow (R1, R2, R5)", "formato": "yaml",
     "descripcion": "Qué banda de prioridades usa cada requerimiento; de aquí sale la explicación de cada flujo en la pestaña Flujos."},
]


def _huella(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def leer_copias(carpeta: str) -> Dict[str, List[dict]]:
    """{id de archivo: [copia]} con la copia de cada fuente; una fuente sin ese archivo figura con `existe: False`."""
    salida = {}
    for a in ARCHIVOS:
        copias = []
        for fuente in FUENTES:
            ruta = os.path.join(carpeta, fuente, a["id"])
            if os.path.isfile(ruta):
                with open(ruta, "rb") as f:
                    datos = f.read()
                copias.append({"fuente": fuente, "existe": True, "sha256": _huella(datos), "bytes": len(datos), "_datos": datos})
            else:
                copias.append({"fuente": fuente, "existe": False})
        salida[a["id"]] = copias
    return salida


def _texto(datos: bytes) -> str:
    t = datos.decode("utf-8", errors="replace")
    return t if len(t) <= MAX_TEXTO else t[:MAX_TEXTO] + "\n… (recortado: el archivo es más largo de lo que muestra el panel)"


def describir(a: dict, copias: List[dict]) -> dict:
    """Una entrada de `archivos`: copias, estado y diferencias. `igual` = dos o más copias, todas idénticas; `diverge` = hay dos o más versiones distintas; `una_copia` = solo una fuente lo tiene (no hay
    con qué compararlo: por ejemplo, un archivo que está en el repo y no se ha desplegado); `sin_datos` = ninguna fuente lo tiene."""
    existentes = [c for c in copias if c["existe"]]
    contenidos = {c["sha256"]: _texto(c["_datos"]) for c in existentes}
    for c in existentes:                                  # se compara el CONTENIDO LÓGICO, no los bytes: el mismo JSON con otra sangría, o la unidad del respaldo, no son una divergencia
        c["logico"] = _logico(a, c["_datos"])
    logicos = sorted({c["logico"] for c in existentes})
    estado = "sin_datos" if not existentes else ("diverge" if len(logicos) > 1 else ("igual" if len(existentes) > 1 else "una_copia"))
    nota = None
    if estado == "igual" and len({c["sha256"] for c in existentes}) > 1:
        nota = ("mismo contenido, distinto formato (por ejemplo, el repo con sangría y lo desplegado compacto)" if a["formato"] == "json"
                else "difieren solo en --respaldo-de: es lo que distingue al respaldo del principal")
    diferencias = []
    if estado == "diverge":
        ref = next((c for c in existentes if c["fuente"] == "repo"), existentes[0])
        vistos = {ref["logico"]}
        for c in existentes:
            if c["logico"] in vistos:
                continue
            vistos.add(c["logico"])
            iguales = [x["fuente"] for x in existentes if x["logico"] == c["logico"]]
            diff = list(difflib.unified_diff(contenidos[ref["sha256"]].splitlines(), contenidos[c["sha256"]].splitlines(), fromfile=ref["fuente"], tofile="+".join(iguales), lineterm="", n=2))
            diferencias.append({"base": ref["fuente"], "con": iguales, "diff": "\n".join(diff[:400]) + ("\n… (diff recortado)" if len(diff) > 400 else "")})
    return {"id": a["id"], "titulo": a["titulo"], "formato": a["formato"], "descripcion": a["descripcion"], "estado": estado, "nota": nota, "faltan": [c["fuente"] for c in copias if not c["existe"]],
            "copias": [{k: v for k, v in c.items() if k not in ("_datos", "logico")} for c in copias], "contenidos": contenidos, "diferencias": diferencias}


def _logico(a: dict, datos: bytes) -> str:
    """Huella del contenido que importa: un JSON se compara ya parseado y ordenado (da igual la sangría); la unidad del vigilante, sin `--respaldo-de URL` (el principal y el respaldo se distinguen
    solo por eso, por diseño); el resto, byte a byte."""
    if a["formato"] == "json":
        try:
            return _huella(json.dumps(json.loads(datos.decode("utf-8")), sort_keys=True, separators=(",", ":")).encode())
        except (ValueError, UnicodeDecodeError):
            pass
    if a["id"] == "vigilante-capa3.service":
        return _huella(re.sub(r"\s+--respaldo-de\s+\S+", "", datos.decode("utf-8", errors="replace")).strip().encode())
    return _huella(datos)


# ---------- política interpretada ----------
def _flujo_para_mostrar(f: dict) -> dict:
    return flujos.normalizar(dict(f, appId=APP_R2, state="(esperado)"))


def interpretar(ruta_policy: str) -> dict:
    """Lo que dice la política (con el mismo código del mapper) y lo que R2 instalaría. `ruta_policy` es el policy.yaml (junto a él, el policy.local.yaml si existe)."""
    import plan_arboles
    import policy_tables as pt
    p = pt.cargar_politica(ruta_policy)
    roles = [{"rol": r, "matriz": d["matriz"], "tabla": int(d["tabla"])} for r, d in p["roles"].items()]
    matrices = sorted({r["matriz"] for r in roles})
    servicios = {n: {"ip": s.get("ip"), "tcp": s.get("tcp")} for n, s in p["services"].items()}
    usados = sorted({r["dst"] for r in p["rules"]} | set(servicios))
    matriz = {m: {s: pt.servicios_permitidos(p, m).get(s) for s in usados} for m in matrices}       # True permitido, False denegado, None sin regla (denegado por defecto)
    switches = {d: n for d, n in plan_arboles.NOMBRES.items()}
    por_rol = {r["rol"]: [_flujo_para_mostrar(f) for f in pt.flujos_tabla_rol(p, "of:0000000000000000", r["rol"], r["tabla"])] for r in roles}
    esperados = {}
    for dpid, nombre in sorted(switches.items(), key=lambda x: x[1]):
        esperados[dpid] = {"nombre": nombre, "flujos": [_flujo_para_mostrar(f) for f in pt.flujos_dispositivo(p, dpid)]}
    portal = p.get("portal") or {}
    return {
        "roles": roles, "matriz": matriz, "servicios": servicios, "sin_host": sorted(s for s in usados if s not in servicios), "reenvio": p.get("reenvio", "normal"),
        "ascenso": p.get("ascenso") or {}, "ascenso_exigir_ubicacion": bool(p.get("ascenso_exigir_ubicacion")), "cuarentena": p.get("cuarentena"),
        "portal": {k: portal.get(k) for k in ("ip", "mac", "puerto", "switches", "grupos", "login_directo", "duracion_s", "desvios") if k in portal},
        "trusted": p.get("trusted") or {}, "sin_usuarios": p.get("sin_usuarios") or [], "flujos_por_rol": por_rol, "esperados": esperados,
        "nombres_switch": switches, "n_reglas_matriz": len(p["rules"]),
    }


def _politica(carpeta: str) -> dict:
    """Interpreta la política que corre el mapper (la copia de ctrl-1, o la de la primera réplica que la tenga); si no hay ninguna desplegada, la del repositorio."""
    for fuente in ("ctrl-1", "ctrl-2", "ctrl-3", "repo"):
        base = os.path.join(carpeta, fuente, "policy.yaml")
        if not os.path.isfile(base):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            for nombre in ("policy.yaml", "policy.local.yaml"):
                origen = os.path.join(carpeta, fuente, nombre)
                if os.path.isfile(origen):
                    with open(origen, "rb") as i, open(os.path.join(tmp, nombre), "wb") as o:
                        o.write(i.read())
            try:
                return dict(interpretar(os.path.join(tmp, "policy.yaml")), origen=fuente)
            except Exception as e:  # noqa: BLE001  - una política mal escrita no debe impedir ver los archivos
                return {"origen": fuente, "error": f"{type(e).__name__}: {e}"}
    return {"origen": None, "error": "no hay ningún policy.yaml entre las copias recogidas"}


def exportar(carpeta: str, ahora: Optional[float] = None) -> dict:
    copias = leer_copias(carpeta)
    return {"version": 1, "generado": ahora if ahora is not None else time.time(), "fuentes": FUENTES, "archivos": [describir(a, copias[a["id"]]) for a in ARCHIVOS], "politica": _politica(carpeta)}


def main(argv=None) -> int:
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--carpeta", required=True, help="carpeta con una subcarpeta por fuente (repo, ctrl-1, ctrl-2, ctrl-3)")
    a.add_argument("--salida", required=True, help="JSON de salida")
    arg = a.parse_args(argv)
    res = exportar(arg.carpeta)
    tmp = arg.salida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, arg.salida)                                  # el panel nunca lee un archivo a medias
    pol = res["politica"]
    print(f"{len(res['archivos'])} archivos ({', '.join(x['id'] + ': ' + x['estado'] for x in res['archivos'])}); política de {pol.get('origen')}: "
          f"{'ERROR ' + pol['error'] if 'error' in pol else str(len(pol['roles'])) + ' roles, ' + str(pol['n_reglas_matriz']) + ' reglas'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
