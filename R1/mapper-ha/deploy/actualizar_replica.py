#!/usr/bin/env python3
"""Actualiza UNA réplica mapper-ha-prod (código + PORTAL_TOKEN) con copia de vuelta atrás.
Uso (como root en el controlador):
  actualizar_replica.py desplegar <stamp> <tgz> <archivo_token>
  actualizar_replica.py revertir  <stamp>
No muestra secretos. No toca ONOS, etcd ni el gateway."""
import hashlib, json, os, shutil, subprocess, sys, tarfile, time, urllib.request

NOMBRE = "mapper-ha-prod"


def sh(*a, check=True):
    r = subprocess.run(list(a), capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"FALLO {' '.join(a[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def inspeccion(nombre):
    return json.loads(sh("docker", "inspect", nombre))[0]


def salud(ip):
    try:
        with urllib.request.urlopen(f"http://{ip}:15002/health/ha", timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": f"error {type(e).__name__}"}


def huella(ruta):
    return hashlib.sha256(open(ruta, "rb").read()).hexdigest()[:12]


def args_run(ins, token):
    hc, cfg = ins["HostConfig"], ins["Config"]
    a = ["docker", "run", "-d", "--name", NOMBRE, "--restart", hc["RestartPolicy"]["Name"], "-w", cfg["WorkingDir"]]
    for m in ins["Mounts"]:
        a += ["-v", f"{m['Source']}:{m['Destination']}" + ("" if m.get("RW", True) else ":ro")]
    for cport, binds in (hc.get("PortBindings") or {}).items():
        for b in binds:
            a += ["-p", f"{b['HostIp']}:{b['HostPort']}:{cport.split('/')[0]}"]
    red = hc["NetworkMode"]
    a += ["--network", red]
    for al in (ins["NetworkSettings"]["Networks"][red].get("Aliases") or []):
        if al != ins["Config"]["Hostname"] and al != ins["Id"][:12]:
            a += ["--network-alias", al]
    vistos = set()
    for e in cfg["Env"]:
        k = e.split("=", 1)[0]
        if k in ("PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256"):
            continue  # vienen de la imagen
        vistos.add(k)
        a += ["-e", f"PORTAL_TOKEN={token}" if k == "PORTAL_TOKEN" else e]
    if "PORTAL_TOKEN" not in vistos:
        a += ["-e", f"PORTAL_TOKEN={token}"]
    a += [cfg["Image"]] + (cfg.get("Cmd") or [])
    return a


def desplegar(stamp, tgz, archivo_token):
    ins = inspeccion(NOMBRE)
    ip = ins["HostConfig"]["PortBindings"]["5000/tcp"][0]["HostIp"]
    antes = salud(ip)
    print("antes:", antes)
    if antes.get("status") != "ready":
        raise SystemExit("ABORTO: la réplica no está ready antes del cambio")
    app = next(m["Source"] for m in ins["Mounts"] if m["Destination"] == "/app")
    token = open(archivo_token).read().strip()
    assert len(token) >= 32, "token corto"
    # 1) respaldos: código y contenedor
    copia = f"{app}.rollback-{stamp}"
    shutil.copytree(app, copia, symlinks=True)
    print("respaldo de código:", copia)
    sh("docker", "stop", "-t", "15", NOMBRE)
    sh("docker", "rename", NOMBRE, f"{NOMBRE}-rollback-{stamp}")
    sh("docker", "update", "--restart", "no", f"{NOMBRE}-rollback-{stamp}")
    print("contenedor anterior guardado:", f"{NOMBRE}-rollback-{stamp}")
    # 2) código nuevo (policy.yaml y demás archivos no .py se conservan)
    with tarfile.open(tgz) as t:
        miembros = [m for m in t.getmembers() if m.isfile() and m.name.endswith(".py") and "/" not in m.name]
        t.extractall(app, members=miembros)
    for m in miembros:
        print("  ", huella(os.path.join(app, m.name)), m.name)
    # 3) contenedor nuevo con la misma configuración y el token
    nuevo = sh(*args_run(ins, token))
    print("contenedor nuevo:", nuevo[:12])
    for i in range(60):
        time.sleep(2)
        s = salud(ip)
        if s.get("status") == "ready":
            print(f"ready a los {2 * (i + 1)} s:", s)
            break
    else:
        print("NO quedó ready en 120 s:", salud(ip))
        print(sh("docker", "logs", "--tail", "30", NOMBRE, check=False))
        raise SystemExit(2)
    t = sh("docker", "exec", NOMBRE, "sh", "-c", "printenv PORTAL_TOKEN | tr -d '\\n' | sha256sum | cut -c1-12")
    print("huella del token en el contenedor:", t)


def revertir(stamp):
    viejo = f"{NOMBRE}-rollback-{stamp}"
    ins = inspeccion(viejo)
    app = next(m["Source"] for m in ins["Mounts"] if m["Destination"] == "/app")
    sh("docker", "rm", "-f", NOMBRE, check=False)
    copia = f"{app}.rollback-{stamp}"
    shutil.rmtree(app)
    shutil.copytree(copia, app, symlinks=True)
    sh("docker", "rename", viejo, NOMBRE)
    sh("docker", "update", "--restart", "unless-stopped", NOMBRE)
    sh("docker", "start", NOMBRE)
    print("revertido; salud:", salud(ins["HostConfig"]["PortBindings"]["5000/tcp"][0]["HostIp"]))


def plan():
    ins = inspeccion(NOMBRE)
    a = args_run(ins, "X" * 40)
    print(" ".join(x.split("=", 1)[0] + "=***" if x.count("=") and not x.startswith(("/", "192.")) and "/" not in x.split("=", 1)[0] else x for x in a))
    print("app:", next(m["Source"] for m in ins["Mounts"] if m["Destination"] == "/app"))


if __name__ == "__main__":
    if sys.argv[1] == "plan":
        plan()
    elif sys.argv[1] == "desplegar":
        desplegar(*sys.argv[2:5])
    elif sys.argv[1] == "revertir":
        revertir(sys.argv[2])
