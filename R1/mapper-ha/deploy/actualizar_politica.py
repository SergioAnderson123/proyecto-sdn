#!/usr/bin/env python3
"""Actualiza la politica que monta una replica del mapper (policy.yaml nuevo + `admin_panel` en policy.local.yaml), con respaldo y validacion previa.

Como root en el controlador, ANTES de recrear el contenedor (la politica se lee al arrancar):
  actualizar_politica.py validar <policy_nuevo.yaml> <paquete.tgz>      # solo en una copia temporal: no cambia nada en la replica
  actualizar_politica.py aplicar <stamp> <policy_nuevo.yaml> <paquete.tgz>
  actualizar_politica.py revertir <stamp>

`validar` carga la politica nueva con el CODIGO NUEVO del paquete (cargar_politica) y comprueba: el rol superadmin (tabla 45), admin_panel con la IP del server y los roles de siempre
con sus tablas de siempre. `aplicar` hace lo mismo y solo entonces reemplaza los archivos (el respaldo va a prod-r2-policy-backups/<stamp>/). No muestra secretos: la politica no los lleva.
No toca ONOS, etcd ni el contenedor: eso lo hace actualizar_replica.py."""
import hashlib, os, re, shutil, subprocess, sys, tarfile, tempfile

BASE = "/home/ubuntu/sdn-mapper-ha"
POL = f"{BASE}/prod-r2-policy/policy"
COPIAS = f"{BASE}/prod-r2-policy-backups"
IP_SERVER, PUERTO_PANEL = "10.0.0.10", 8480
ROLES_DE_SIEMPRE = {"docente": 10, "alumno": 20, "soporte": 40, "invitado": 50}


def huella(ruta):
    return hashlib.sha256(open(ruta, "rb").read()).hexdigest()[:12]


def con_admin_panel(texto):
    """Inserta `admin_panel` al final del bloque `services:` del policy.local.yaml sin reformatear el resto."""
    if re.search(r"^  admin_panel:", texto, flags=re.M):
        return texto
    lineas = texto.split("\n")
    i = next(k for k, l in enumerate(lineas) if l == "services:")
    j = i + 1
    while j < len(lineas) and (lineas[j].startswith(" ") or lineas[j] == ""):
        j += 1
    while lineas[j - 1] == "":
        j -= 1
    lineas[j:j] = ["  admin_panel:", f"    ip: {IP_SERVER}", f"    tcp: {PUERTO_PANEL}"]
    return "\n".join(lineas)


def validar_en_copia(policy_nuevo, paquete):
    """Carga la politica nueva con el codigo nuevo en una carpeta temporal y devuelve el resumen; lanza AssertionError si algo no cuadra."""
    tmp = tempfile.mkdtemp(prefix="pol_val_")
    try:
        cod, pol = f"{tmp}/codigo", f"{tmp}/politica"
        os.makedirs(cod); os.makedirs(pol)
        with tarfile.open(paquete) as t:
            t.extractall(cod, members=[m for m in t.getmembers() if m.isfile() and m.name.endswith(".py") and "/" not in m.name])
        shutil.copy(policy_nuevo, f"{pol}/policy.yaml")
        local = open(f"{POL}/policy.local.yaml", encoding="utf-8").read()
        open(f"{pol}/policy.local.yaml", "w", encoding="utf-8").write(con_admin_panel(local))
        prueba = (
            "import sys, json; sys.path.insert(0, %r)\n"
            "import policy_tables as pt\n"
            "p = pt.cargar_politica(%r)\n"
            "r = {k: int(v['tabla']) for k, v in p['roles'].items()}\n"
            "print(json.dumps({'roles': r, 'admin_panel': p['services'].get('admin_panel'), 'reglas': len(p['rules']), 'excepciones': 'excepciones' in p,"
            " 'servicios': sorted(p['services']), 'ip_unica': sorted({s['ip'] for s in p['services'].values()})}))\n" % (cod, f"{pol}/policy.yaml"))
        out = subprocess.run([sys.executable, "-c", prueba], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": cod})
        assert out.returncode == 0, "no carga: " + out.stderr.strip()[-400:]
        import json
        r = json.loads(out.stdout)
        assert r["roles"].get("superadmin") == 45, r["roles"]
        assert all(r["roles"].get(k) == v for k, v in ROLES_DE_SIEMPRE.items()), ("los roles de siempre cambiaron", r["roles"])
        assert r["admin_panel"] == {"ip": IP_SERVER, "tcp": PUERTO_PANEL}, r["admin_panel"]
        assert r["ip_unica"] == [IP_SERVER], ("todos los servicios deben quedar en la IP del server", r["ip_unica"])
        assert r["excepciones"] is False, "no debe haber excepciones activas"
        return r
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def aplicar(stamp, policy_nuevo, paquete):
    r = validar_en_copia(policy_nuevo, paquete)
    print("validacion OK:", {k: r[k] for k in ("roles", "admin_panel", "reglas", "excepciones")})
    copia = f"{COPIAS}/{stamp}"
    os.makedirs(copia, exist_ok=True)
    for f in ("policy.yaml", "policy.local.yaml"):
        if not os.path.exists(f"{copia}/{f}"):
            shutil.copy2(f"{POL}/{f}", f"{copia}/{f}")
    print("respaldo en", copia, "| antes: policy.yaml", huella(f"{POL}/policy.yaml"), "policy.local.yaml", huella(f"{POL}/policy.local.yaml"))
    st = os.stat(f"{POL}/policy.yaml")
    nuevo_local = con_admin_panel(open(f"{POL}/policy.local.yaml", encoding="utf-8").read())
    for ruta, contenido in ((f"{POL}/policy.yaml", open(policy_nuevo, "rb").read()), (f"{POL}/policy.local.yaml", nuevo_local.encode("utf-8"))):
        tmp = ruta + ".nuevo"
        open(tmp, "wb").write(contenido)
        os.chmod(tmp, st.st_mode & 0o777); os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, ruta)
    print("despues: policy.yaml", huella(f"{POL}/policy.yaml"), "policy.local.yaml", huella(f"{POL}/policy.local.yaml"))


def revertir(stamp):
    copia = f"{COPIAS}/{stamp}"
    for f in ("policy.yaml", "policy.local.yaml"):
        shutil.copy2(f"{copia}/{f}", f"{POL}/{f}")
    print("revertido:", {f: huella(f"{POL}/{f}") for f in ("policy.yaml", "policy.local.yaml")})


if __name__ == "__main__":
    modo = sys.argv[1]
    if modo == "validar":
        print("validacion OK:", validar_en_copia(sys.argv[2], sys.argv[3]))
    elif modo == "aplicar":
        aplicar(*sys.argv[2:5])
    elif modo == "revertir":
        revertir(sys.argv[2])
