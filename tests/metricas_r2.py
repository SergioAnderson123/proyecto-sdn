"""Métricas de R2 para la rúbrica: entradas de TCAM por switch y escalabilidad del enforcement por número de sesiones.

  python tests/metricas_r2.py tcam              inventario de TCAM por switch/tabla/aplicación y comparación con el switch real
  python tests/metricas_r2.py carga [N ...]     instala N flujos de sesión sintéticos de golpe (como N usuarios admitidos a la vez):
                                                tiempo de aceptación en ONOS, tiempo hasta verlos en el switch, flujos/s,
                                                TCAM resultante y CPU/RAM de ONOS; luego los borra
  python tests/metricas_r2.py admision [N]      admisión REAL: N veces por rol (docente h1, alumno h2, soporte h3) se cierra la sesión,
                                                se vuelve a lanzar wpa_supplicant y se mide cuánto tarda hasta que la sesión está
                                                activa en el Policy Mapper y hasta que el primer servicio permitido responde 200
  python tests/metricas_r2.py concurrente [R] [K ..]  K usuarios inician sesión a la vez (R repeticiones por nivel; K hasta 6)
  python tests/metricas_r2.py todo              tcam + carga con 50, 200, 500 y 1000

Requiere el stack levantado y la topología en marcha. Escribe en pruebas-r2/metricas.log y pruebas-r2/carga_sesiones.csv
(junto al repositorio), que lee monitor_r2.py. Los flujos sintéticos usan el appId org.campus.r2.prueba y MAC 02:00:..., y
se eliminan al terminar; no tocan las sesiones reales.
"""
import base64
import csv
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "R1", "shared", "policy_mapper"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "policy"))
import policy_tables as pt  # noqa: E402

ONOS = "http://localhost:8181/onos/v1"
S1, S2 = "of:0000000000000001", "of:0000000000000002"
APP_PRUEBA = "org.campus.r2.prueba"
CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "pruebas-r2")
LOG = os.path.join(CARPETA, "metricas.log")


def log(msg=""):
    print(msg, flush=True)
    os.makedirs(CARPETA, exist_ok=True)
    with open(LOG, "a", encoding="utf8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def onos(metodo, ruta, cuerpo=None):
    r = urllib.request.Request(ONOS + ruta, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    r.add_header("Authorization", "Basic " + base64.b64encode(b"onos:rocks").decode())
    if cuerpo is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=60) as resp:
        cuerpo_resp = resp.read()
        return json.loads(cuerpo_resp) if cuerpo_resp else {}


def flujos_switch(sw: str) -> str:
    return subprocess.run(["docker", "exec", "mininet", "ovs-ofctl", "-O", "OpenFlow13", "dump-flows", sw],
                          capture_output=True, text=True, timeout=60).stdout


def contar_switch(sw: str, patron: str = "table=") -> int:
    return sum(1 for l in flujos_switch(sw).splitlines() if patron in l)


def recursos(nombres=("onos", "policy_mapper")):
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"],
                         capture_output=True, text=True, timeout=30).stdout
    return {l.split("|")[0]: (l.split("|")[1], l.split("|")[2].split(" / ")[0]) for l in out.splitlines() if l.split("|")[0] in nombres}


def tcam():
    log("== TCAM por switch (flujos ADDED en ONOS) ==")
    flujos = onos("GET", "/flows")["flows"]
    por = Counter((f["deviceId"], f["tableId"], f["appId"].split(".")[-1]) for f in flujos if f["state"] == "ADDED")
    log(f"{'switch':<8}{'tabla':<7}{'app':<12}{'entradas':>8}")
    for (dev, tabla, app), n in sorted(por.items()):
        log(f"{dev[-2:]:<8}{tabla:<7}{app:<12}{n:>8}")
    for dev, sw in ((S1, "s1"), (S2, "s2")):
        en_onos = sum(n for (d, _, _), n in por.items() if d == dev)
        en_sw = contar_switch(sw)
        log(f"{sw}: ONOS {en_onos} entradas, switch real {en_sw}  {'(coinciden)' if en_onos == en_sw else '(DIFIEREN)'}")
    pol = pt.cargar_politica()
    log("")
    log("== Fórmula de TCAM de un switch de acceso ==")
    fijos = len([f for f in flujos if f["deviceId"] == S1 and f["tableId"] == 0 and f["priority"] == 40000 and f["state"] == "ADDED"])
    por_rol = {}
    for rol, d in pol["roles"].items():
        por_rol[rol] = len(pt.flujos_tabla_rol(pol, S1, rol, int(d["tabla"])))
    log(f"  entradas base de R1 en tabla 0 (EAPOL, LLDP, BDDP, ARP, DHCP): {fijos}")
    log(f"  puertos de confianza: {len(pt.flujos_confianza(pol, S1))}")
    log("  tablas de rol (servicios permitidos + ARP + DROP): " + ", ".join(f"{r}={n}" for r, n in por_rol.items()))
    log(f"  TCAM(s1) = {fijos} + {len(pt.flujos_confianza(pol, S1))} + {sum(por_rol.values())} + U   (U = sesiones activas, 1 entrada por usuario)")
    return en_sw


def flujo_sesion(i: int) -> dict:
    mac = f"02:00:00:00:{(i >> 8) & 0xff:02x}:{i & 0xff:02x}"
    return {
        "priority": 40001, "timeout": 0, "isPermanent": True, "deviceId": S1, "tableId": 0,
        "treatment": {"instructions": [
            {"type": "L2MODIFICATION", "subtype": "VLAN_PUSH", "ethernetType": "0x8100"},
            {"type": "L2MODIFICATION", "subtype": "VLAN_ID", "vlanId": 10},
            {"type": "TABLE", "tableId": 10}]},
        "selector": {"criteria": [{"type": "IN_PORT", "port": 1}, {"type": "ETH_SRC", "mac": mac}]},
    }


def esperar(cond, limite=120.0, paso=0.25):
    t0 = time.time()
    while time.time() - t0 < limite:
        if cond():
            return time.time() - t0
        time.sleep(paso)
    return None


def carga(ns):
    log("")
    log("== Carga de sesiones sintéticas (flujo de sesión 40001 en s1) ==")
    base = contar_switch("s1")
    log(f"TCAM de s1 antes: {base} entradas   recursos: {recursos()}")
    filas = []
    for n in ns:
        flujos = [flujo_sesion(i) for i in range(1, n + 1)]
        t0 = time.time()
        onos("POST", f"/flows?appId={APP_PRUEBA}", {"flows": flujos})
        t_post = time.time() - t0
        t_vis = esperar(lambda: contar_switch("s1", "dl_src=02:00:00") >= n)
        t_total = time.time() - t0
        tcam_s1 = contar_switch("s1")
        rec = recursos()
        cpu, mem = rec.get("onos", ("?", "?"))
        if t_vis is None:
            log(f"N={n:<5} NO se instalaron todos en 120 s (visibles {contar_switch('s1', 'dl_src=02:00:00')})")
            fila = [n, round(t_post * 1000), None, None, tcam_s1, cpu, mem]
        else:
            fila = [n, round(t_post * 1000), round(t_total * 1000), round(n / t_total), tcam_s1, cpu, mem]
            log(f"N={n:<5} ONOS acepta en {fila[1]:>6} ms | visible en el switch a los {fila[2]:>6} ms | {fila[3]:>5} flujos/s | TCAM s1 {tcam_s1:>5} | ONOS CPU {cpu} RAM {mem}")
        filas.append(fila)
        t0 = time.time()
        try:
            onos("DELETE", f"/flows/application/{APP_PRUEBA}")
        except Exception as e:  # noqa: BLE001
            log(f"  (borrado: {e})")
        t_clean = esperar(lambda: contar_switch("s1", "dl_src=02:00:00") == 0)
        log(f"        limpieza: {'%.1f s' % t_clean if t_clean is not None else 'no terminó'}; TCAM s1 vuelve a {contar_switch('s1')} (base {base})")
    with open(os.path.join(CARPETA, "carga_sesiones.csv"), "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["sesiones", "onos_acepta_ms", "visible_switch_ms", "flujos_por_s", "tcam_s1", "onos_cpu", "onos_ram"])
        w.writerows(filas)
    log("resultados: pruebas-r2/carga_sesiones.csv")
    return filas


MAPPER = "http://localhost:5000"
USUARIOS = [("h1", "docente", "profesor01@example.org", "00-00-00-00-00-11", "wpa_profe_01", ("10.0.0.40", 8001)),
            ("h2", "alumno", "alumno02@example.org", "00-00-00-00-00-22", "wpa_alumno_02", ("10.0.0.40", 8001)),
            ("h3", "soporte", "support01@example.org", "00-00-00-00-00-31", "wpa_support_01", ("10.0.0.40", 8001))]


def mnx(host, *cmd, timeout=30):
    return subprocess.run(["docker", "exec", "mininet", "bash", "/root/shared-host/mnx.sh", host, *cmd],
                          capture_output=True, text=True, timeout=timeout)


def sesiones():
    try:
        with urllib.request.urlopen(MAPPER + "/sessions", timeout=3) as r:
            return json.load(r)["sessions"]
    except Exception:  # noqa: BLE001
        return {}


def cerrar_sesion(usuario, mac):
    """Como el aviso del agente del host (POST /sessions/logout, requiere AGENTE_TOKEN): el mapper borra la sesión y su flujo 40001."""
    r = urllib.request.Request(MAPPER + "/sessions/logout", method="POST", data=json.dumps({"mac": mac}).encode(),
                               headers={"Content-Type": "application/json", "X-Agente-Token": os.environ.get("AGENTE_TOKEN", "")})
    urllib.request.urlopen(r, timeout=5).read()


def percentil(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def admision(n, roles=()):
    """Ejecuta R2/mininet/admision_r2.py DENTRO del contenedor (sin la sobrecarga de docker exec) y registra su salida."""
    log("")
    log("== Admisión real de usuarios (EAP-TLS + RADIUS + LDAP + Policy Mapper + flujo en el switch) ==")
    p = subprocess.Popen(["docker", "exec", "mininet", "python3", "/root/shared-r2/admision_r2.py", str(n), *roles],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf8")
    for linea in p.stdout:
        log(linea.rstrip())
    p.wait()


def concurrente(reps, niveles):
    """Admisiones simultáneas (K usuarios a la vez) dentro del contenedor; registra su salida."""
    log("")
    log("== Admisiones simultáneas (K usuarios inician sesión a la vez) ==")
    p = subprocess.Popen(["docker", "exec", "mininet", "python3", "/root/shared-r2/admision_concurrente_r2.py", str(reps), *niveles],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf8")
    for linea in p.stdout:
        log(linea.rstrip())
    p.wait()


def main():
    arg = sys.argv[1:] or ["todo"]
    if arg[0] == "concurrente":
        concurrente(int(arg[1]) if len(arg) > 1 else 5, arg[2:])
        return
    if arg[0] == "admision":
        admision(int(arg[1]) if len(arg) > 1 else 10, arg[2:])
        return
    if arg[0] in ("tcam", "todo"):
        tcam()
    if arg[0] == "carga":
        carga([int(x) for x in arg[1:]] or [50, 200, 500, 1000])
    elif arg[0] == "todo":
        carga([50, 200, 500, 1000])


if __name__ == "__main__":
    main()
