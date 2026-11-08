#!/usr/bin/env python3
"""Asistente para desplegar y probar R1/R2 en un slice de VNRT. Se ejecuta en la VM Controller.

    python3 slice.py              menu interactivo (va pidiendo los datos)
    python3 slice.py config       pregunta los datos del slice y los guarda en ~/slice.json
    python3 slice.py preparar     instala Ansible y copia tu llave SSH a switches y hosts
    python3 slice.py controller   levanta ONOS, LDAP, Policy Mapper y RADIUS y comprueba las apps de ONOS
    python3 slice.py switches     conecta los switches al controlador (Ansible)
    python3 slice.py hosts        instala en los hosts el agente 802.1X con interfaz web (Ansible); no los arranca
    python3 slice.py alta         autentica los hosts y fija en LDAP la MAC, el switch y el puerto de cada usuario
    python3 slice.py estado       muestra apps, switches, sesiones, flujos y LDAP
    python3 slice.py interfaz     muestra los tuneles SSH para abrir la interfaz web de cada host
    python3 slice.py limpiar      borra sesiones y flujos para repetir la prueba
    python3 slice.py todo         controller + switches + hosts + alta

Solo usa la biblioteca estandar. Los usuarios disponibles son los que tienen certificado en
R1/shared/mininet/supplicants (ver USUARIOS).
"""
import argparse
import atexit
import base64
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "R1" / "deploy"
CONFIG = Path.home() / "slice.json"
ONOS = "http://127.0.0.1:8181/onos"
ONOS_AUTH = (os.environ.get("ONOS_USER", "onos"), os.environ.get("ONOS_PASS", "rocks"))
MAPPER = "http://127.0.0.1:5000"
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new"]
APPS = ["org.onosproject.openflow", "org.opencord.sadis", "org.opencord.aaa", "org.onosproject.dhcp",
        "org.onosproject.acl", "org.onosproject.vpls", "org.onosproject.flowanalyzer"]

# Usuarios con certificado EAP-TLS y su suplicante (R1/shared/mininet/supplicants). `clave` es la contrasena de la clave privada.
USUARIOS = {
    "profesor01": {"rol": "Docente", "vlan": 10, "conf": "wpa_profe_01.conf", "clave": "professor_password"},
    "profesor02": {"rol": "Docente", "vlan": 10, "conf": "wpa_profe_02.conf", "clave": "professor_password"},
    "alumno01": {"rol": "Alumno", "vlan": 20, "conf": "wpa_alumno_01.conf", "clave": "student_password"},
    "alumno02": {"rol": "Alumno", "vlan": 20, "conf": "wpa_alumno_02.conf", "clave": "student_password"},
    "alumno03": {"rol": "Alumno", "vlan": 20, "conf": "wpa_alumno_03.conf", "clave": "student_password"},
    "support01": {"rol": "Soporte (Administrador)", "vlan": 40, "conf": "wpa_support_01.conf", "clave": "support_password"},
}


# ---------------------------------------------------------------- utilidades de pantalla

def titulo(texto):
    print(f"\n== {texto}")


def preguntar(texto, defecto=""):
    sufijo = f" [{defecto}]" if defecto != "" else ""
    return input(f"{texto}{sufijo}: ").strip() or defecto


def preguntar_entero(texto, defecto, minimo=1):
    while True:
        valor = preguntar(texto, str(defecto))
        if valor.isdigit() and int(valor) >= minimo:
            return int(valor)
        print(f"  Escribe un numero entero (minimo {minimo}).")


def si_no(texto, defecto=True):
    r = input(f"{texto} [{'S/n' if defecto else 's/N'}]: ").strip().lower()
    return defecto if not r else r in ("s", "si", "y", "yes")


def ejecutar(cmd, cwd=None, check=True, capturar=False):
    """Ejecuta un comando mostrando lo que se lanza. Con capturar=True devuelve la salida."""
    print("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, check=False, text=True, capture_output=capturar)
    if check and r.returncode != 0:
        if capturar and r.stdout:
            print(r.stdout)
        if capturar and r.stderr:
            print(r.stderr, file=sys.stderr)
        raise SystemExit(f"El comando fallo (codigo {r.returncode}).")
    return r.stdout if capturar else r.returncode


# ---------------------------------------------------------------- REST

def http(metodo, url, auth=None, timeout=10):
    req = urllib.request.Request(url, method=metodo)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def onos(metodo, ruta):
    codigo, cuerpo = http(metodo, ONOS + ruta, ONOS_AUTH)
    try:
        return codigo, json.loads(cuerpo)
    except ValueError:
        return codigo, None


def sesiones():
    codigo, cuerpo = http("GET", MAPPER + "/sessions")
    try:
        return json.loads(cuerpo) if codigo == 200 else {"total_sessions": 0, "sessions": {}}
    except ValueError:
        return {"total_sessions": 0, "sessions": {}}


# ---------------------------------------------------------------- configuracion

def detectar_ip():
    r = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
    return r.stdout.split()[0] if r.returncode == 0 and r.stdout.split() else ""


def cargar_config():
    if not CONFIG.exists():
        return None
    return json.loads(CONFIG.read_text())


def config_o_salir():
    cfg = cargar_config()
    if cfg is None:
        print("Aun no hay configuracion: te la pido ahora.")
        cfg = config()
    return cfg


def mostrar_usuarios():
    print("\nUsuarios disponibles (certificado EAP-TLS en R1/shared/mininet/supplicants):")
    print(f"  {'N':<3}{'Usuario':<13}{'Rol':<25}{'VLAN':<6}Suplicante")
    for i, (u, d) in enumerate(USUARIOS.items(), 1):
        print(f"  {i:<3}{u:<13}{d['rol']:<25}{d['vlan']:<6}{d['conf']}")


def elegir_usuario(defecto):
    nombres = list(USUARIOS)
    while True:
        r = preguntar("  Usuario (numero o nombre)", defecto)
        if r.isdigit() and 1 <= int(r) <= len(nombres):
            return nombres[int(r) - 1]
        if r in USUARIOS:
            return r
        print("  No es un usuario de la lista.")


def config():
    titulo("Datos del slice (Enter acepta el valor entre corchetes)")
    cfg = cargar_config() or {}
    ip_publica = preguntar("IP por la que se llega por SSH a switches y hosts", cfg.get("ip_publica", "10.20.12.78"))
    controller_ip = preguntar("IP del Controller vista desde los switches (red de gestion)",
                              cfg.get("controller_ip") or detectar_ip())
    n_sw = preguntar_entero("Cuantos switches hay (sw1, sw2...)", len(cfg.get("switches", [])) or 3)
    switches = []
    for i in range(1, n_sw + 1):
        previo = next((s["puerto"] for s in cfg.get("switches", []) if s["nombre"] == f"sw{i}"), 5800 + i)
        switches.append({"nombre": f"sw{i}", "puerto": preguntar_entero(f"  Puerto SSH de sw{i}", previo)})
    iface = preguntar("Interfaz de datos de los hosts (la que va al switch; ip -br link)", cfg.get("iface", "ens4"))
    n_h = preguntar_entero("Cuantos hosts van a autenticarse (h1, h2...)", len(cfg.get("hosts", [])) or 4)
    mostrar_usuarios()
    por_defecto = ["profesor01", "alumno01", "alumno02", "support01", "profesor02", "alumno03"]   # el reparto ya validado
    hosts = []
    for i in range(1, n_h + 1):
        previo = next((h for h in cfg.get("hosts", []) if h["nombre"] == f"h{i}"), None)
        puerto = preguntar_entero(f"  Puerto SSH de h{i}", previo["puerto"] if previo else 5810 + i)
        usuario = elegir_usuario(previo["usuario"] if previo else por_defecto[(i - 1) % len(por_defecto)])
        hosts.append({"nombre": f"h{i}", "puerto": puerto, "usuario": usuario})
    repetidos = {h["usuario"] for h in hosts if [x["usuario"] for x in hosts].count(h["usuario"]) > 1}
    if repetidos:
        print(f"\nATENCION: {', '.join(sorted(repetidos))} esta asignado a mas de un host. Un usuario solo puede tener "
              "una sesion a la vez; el segundo host sera rechazado como conflicto.")
    cfg = {"ip_publica": ip_publica, "controller_ip": controller_ip, "switches": switches, "iface": iface, "hosts": hosts}
    resumen(cfg)
    if si_no("Guardar esta configuracion", True):
        CONFIG.write_text(json.dumps(cfg, indent=2))
        print(f"Guardado en {CONFIG}")
    return cfg


def resumen(cfg):
    titulo("Configuracion")
    print(f"  IP SSH: {cfg['ip_publica']}   Controller (visto desde los switches): {cfg['controller_ip']}   Interfaz: {cfg['iface']}")
    print("  Switches: " + ", ".join(f"{s['nombre']}:{s['puerto']}" for s in cfg["switches"]))
    for h in cfg["hosts"]:
        d = USUARIOS[h["usuario"]]
        print(f"  {h['nombre']}  puerto {h['puerto']}  {h['usuario']} ({d['rol']}, VLAN {d['vlan']})")


# ---------------------------------------------------------------- inventarios de Ansible

def inventario_switches(cfg):
    lineas = ["[switches]"]
    for s in cfg["switches"]:
        lineas.append(f"{s['nombre']} ansible_host={cfg['ip_publica']} ansible_port={s['puerto']} bridge={s['nombre']}")
    lineas += ["", "[switches:vars]", "ansible_user=ubuntu", "ansible_ssh_private_key_file=~/.ssh/id_ed25519",
               "ansible_ssh_common_args=-o StrictHostKeyChecking=accept-new",
               f"controller_ip={cfg['controller_ip']}", "controller_port=6653", ""]
    return "\n".join(lineas)


def inventario_hosts(cfg):
    lineas = ["[hosts]"]
    for h in cfg["hosts"]:
        lineas.append(f"{h['nombre']} ansible_host={cfg['ip_publica']} ansible_port={h['puerto']} iface={cfg['iface']} "
                      f"conf={USUARIOS[h['usuario']]['conf']} cert={h['usuario']}@example.org.pem")
    lineas += ["", "[hosts:vars]", "ansible_user=ubuntu", "ansible_ssh_private_key_file=~/.ssh/id_ed25519",
               "ansible_ssh_common_args=-o StrictHostKeyChecking=accept-new", f"controller_ip={cfg['controller_ip']}", ""]
    return "\n".join(lineas)


def comandos_tunel(cfg):
    """Un tunel SSH por host para abrir su agente 802.1X en el navegador: h1 en el puerto local 8081, h2 en el 8082..."""
    return [(h["nombre"], 8080 + i, f"ssh -L {8080 + i}:127.0.0.1:8080 -p {h['puerto']} ubuntu@{cfg['ip_publica']}")
            for i, h in enumerate(cfg["hosts"], 1)]


def interfaz():
    cfg = config_o_salir()
    titulo("Interfaz web del agente 802.1X de cada host")
    print("Cada host tiene una pagina de autenticacion (como el dialogo de red de una PC) en su puerto 8080 local.")
    print("Para verla desde tu PC abre un tunel SSH y luego el navegador:\n")
    for h, (nombre, local, cmd) in zip(cfg["hosts"], comandos_tunel(cfg)):
        print(f"  {nombre} ({h['usuario']}):  {cmd}\n      -> http://localhost:{local}")
    print("\nSe autentican solas al arrancar el host con la identidad asignada; desde la pagina puedes elegir otra.")


_become_file = None


def archivo_sudo():
    """Contrasena de sudo de ubuntu en switches y hosts: se pide una vez y se pasa a Ansible por un archivo temporal 0600."""
    global _become_file
    if _become_file:
        return _become_file
    clave = getpass.getpass("Contrasena de sudo de ubuntu en switches y hosts: ")
    fd, ruta = tempfile.mkstemp(prefix="slice_")
    with os.fdopen(fd, "w") as f:
        json.dump({"ansible_become_password": clave}, f)
    os.chmod(ruta, 0o600)
    atexit.register(lambda: os.path.exists(ruta) and os.remove(ruta))
    _become_file = ruta
    return ruta


# ---------------------------------------------------------------- pasos

def preparar():
    cfg = config_o_salir()
    titulo("Ansible y llave SSH")
    if shutil.which("ansible-playbook") is None:
        ejecutar(["sudo", "apt-get", "install", "-y", "ansible"])
    llave = Path.home() / ".ssh" / "id_ed25519"
    if not llave.exists():
        ejecutar(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", llave])
    print("Se copia la llave a cada maquina (te pide su contrasena de ubuntu una vez por maquina).")
    puertos = [s["puerto"] for s in cfg["switches"]] + [h["puerto"] for h in cfg["hosts"]]
    for p in puertos:
        print(f"\n-- puerto {p}")
        ejecutar(["ssh-copy-id", *SSH_OPTS, "-i", f"{llave}.pub", "-p", p, f"ubuntu@{cfg['ip_publica']}"])


def estado_apps():
    ok = True
    for app in APPS:
        codigo, d = onos("GET", f"/v1/applications/{app}")
        estado = d.get("state", "?") if codigo == 200 and d else ("NO INSTALADA" if codigo == 404 else "sin respuesta")
        print(f"  {app:<42}{estado}")
        ok = ok and estado == "ACTIVE"
    return ok


def contenedores_ajenos():
    """Quita los contenedores y la red de otro despliegue con el mismo nombre (otro proyecto de compose)."""
    for c in ("onos", "ldap", "policy_mapper", "radius"):
        r = subprocess.run(["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project"}}', c],
                           capture_output=True, text=True)
        proyecto = r.stdout.strip()
        if r.returncode == 0 and proyecto != "controller":
            subprocess.run(["docker", "rm", "-f", c], capture_output=True)
            print(f"  Quitado {c} (era de otro despliegue: '{proyecto}')")
    r = subprocess.run(["docker", "network", "inspect", "-f", '{{index .Labels "com.docker.compose.project"}}', "sdn"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip() != "controller":
        subprocess.run(["docker", "network", "rm", "sdn"], capture_output=True)


def controller():
    cfg = config_o_salir()
    titulo("Controller: ONOS, LDAP, Policy Mapper y RADIUS")
    env = DEPLOY / ".env"
    if not env.exists():
        shutil.copy(DEPLOY / ".env.example", env)
    lineas = [f"CONTROLLER_IP={cfg['controller_ip']}" if l.startswith("CONTROLLER_IP=") else l
              for l in env.read_text().splitlines()]
    env.write_text("\n".join(lineas) + "\n")
    contenedores_ajenos()
    ejecutar(["bash", "controller/up.sh"], cwd=DEPLOY)
    titulo("Apps de ONOS")
    if not estado_apps():
        print("Faltan apps: repito init-onos.sh")
        ejecutar(["bash", "init-onos.sh"], cwd=DEPLOY / "controller")
        if not estado_apps():
            print("ATENCION: siguen faltando apps (mira docker logs onos).")


def switches():
    cfg = config_o_salir()
    titulo("Switches")
    carpeta = DEPLOY / "switches"
    (carpeta / "inventory.ini").write_text(inventario_switches(cfg))
    sudo = archivo_sudo()
    ejecutar(["ansible", "-i", "inventory.ini", "switches", "-m", "ping"], cwd=carpeta)
    ejecutar(["ansible-playbook", "-i", "inventory.ini", "switches.yml", "-e", f"@{sudo}"], cwd=carpeta)


def playbook_hosts(*extra):
    ejecutar(["ansible-playbook", "-i", "inventory.ini", "hosts.yml", "-e", f"@{archivo_sudo()}", *extra],
             cwd=DEPLOY / "hosts")


def hosts():
    cfg = config_o_salir()
    titulo("Hosts (suplicantes 802.1X)")
    carpeta = DEPLOY / "hosts"
    (carpeta / "inventory.ini").write_text(inventario_hosts(cfg))
    archivo_sudo()
    ejecutar(["ansible", "-i", "inventory.ini", "hosts", "-m", "ping"], cwd=carpeta)
    playbook_hosts()
    interfaz()


def esperar_sesiones(n, maximo=120):
    inicio = time.time()
    while time.time() - inicio < maximo:
        total = sesiones()["total_sessions"]
        if total >= n:
            print(f"  {total} sesion(es) tras {int(time.time() - inicio)} s")
            return True
        print(".", end="", flush=True)
        time.sleep(5)
    print(f"\n  Solo {sesiones()['total_sessions']} de {n} sesion(es) tras {maximo} s")
    return False


def ldap(accion, usuario):
    return ejecutar(["bash", DEPLOY / "controller" / "ldap-ubicacion.sh", accion, usuario], check=False, capturar=True)


def alta():
    cfg = config_o_salir()
    n = len(cfg["hosts"])
    usuarios = [h["usuario"] for h in cfg["hosts"]]
    carpeta = DEPLOY / "hosts"
    if not (carpeta / "inventory.ini").exists():
        (carpeta / "inventory.ini").write_text(inventario_hosts(cfg))
    archivo_sudo()
    titulo("1/4 Liberar a los usuarios en LDAP (sin MAC/switch/puerto: la primera autenticacion pasa)")
    for u in usuarios:
        ldap("liberar", u)
        print(f"  {u}")
    ejecutar(["docker", "restart", "policy_mapper"], capturar=True)
    time.sleep(5)
    titulo("2/4 Arrancar los suplicantes")
    playbook_hosts("-e", "arrancar=true")
    titulo("3/4 Esperar a que ONOS vea las autenticaciones y fijar la ubicacion en LDAP")
    esperar_sesiones(n)
    for u in usuarios:
        salida = ldap("fijar", u) or ""
        linea = next((l for l in salida.splitlines() if "ONOS ve a" in l or "no tiene" in l), None)
        print(f"  {linea or u + ': sin datos en ONOS'}")
    ejecutar(["docker", "restart", "policy_mapper"], capturar=True)
    time.sleep(5)
    titulo("4/4 Autenticar otra vez, ahora con la comprobacion de MAC y ubicacion activa")
    playbook_hosts("-e", "arrancar=true")
    esperar_sesiones(n)
    estado()
    interfaz()


def estado():
    cfg = config_o_salir()
    titulo("Apps de ONOS")
    estado_apps()
    titulo("Switches")
    codigo, d = onos("GET", "/v1/devices")
    for dev in (d or {}).get("devices", []):
        print(f"  {dev['id']}  {'available' if dev['available'] else 'NO DISPONIBLE'}")
    titulo("Sesiones del Policy Mapper")
    s = sesiones()
    for mac, x in s["sessions"].items():
        print(f"  {x['username']:<24}{mac:<20}VLAN {x['vlan']:<4}{x['dpid']}:{x['port']}")
    print(f"  total: {s['total_sessions']}")
    titulo("Flujos de sesion (prioridad 40001) por switch")
    codigo, d = onos("GET", "/v1/flows")
    conteo = {}
    for f in (d or {}).get("flows", []):
        if f["priority"] == 40001:
            conteo[f["deviceId"]] = conteo.get(f["deviceId"], 0) + 1
    for dev, n in sorted(conteo.items()):
        print(f"  {dev}  {n}")
    titulo("LDAP")
    for h in cfg["hosts"]:
        print(f"  {h['usuario']}")
        salida = ldap("ver", h["usuario"]) or ""
        for l in salida.splitlines():
            if l.strip():
                print("    " + l)


def limpiar():
    titulo("Limpiar sesiones y flujos")
    onos("DELETE", "/v1/flows/application/org.onosproject.rest")
    ejecutar(["docker", "restart", "policy_mapper"], capturar=True)
    time.sleep(5)
    http("POST", MAPPER + "/policy/sync")
    print("Sesiones y flujos borrados; tablas de rol reinstaladas.")


def todo():
    controller()
    switches()
    hosts()
    alta()


PASOS = {
    "config": ("Configurar los datos del slice", config),
    "preparar": ("Instalar Ansible y copiar la llave SSH", preparar),
    "controller": ("Levantar ONOS, LDAP, Policy Mapper y RADIUS", controller),
    "switches": ("Conectar los switches al controlador", switches),
    "hosts": ("Preparar los hosts como suplicantes", hosts),
    "alta": ("Autenticar los hosts y fijar su ubicacion en LDAP", alta),
    "estado": ("Ver el estado (apps, switches, sesiones, flujos, LDAP)", estado),
    "interfaz": ("Ver como abrir la interfaz web de cada host (tuneles SSH)", interfaz),
    "limpiar": ("Borrar sesiones y flujos para repetir la prueba", limpiar),
    "todo": ("Todo: controller + switches + hosts + alta", todo),
}


def menu():
    while True:
        print("\n=== Asistente de despliegue R1/R2 ===")
        for i, (clave, (texto, _)) in enumerate(PASOS.items(), 1):
            print(f"  {i}) {texto}")
        print("  q) Salir")
        r = input("Elige una opcion: ").strip().lower()
        if r in ("q", "salir", "0"):
            return
        claves = list(PASOS)
        clave = claves[int(r) - 1] if r.isdigit() and 1 <= int(r) <= len(claves) else (r if r in PASOS else None)
        if clave is None:
            print("Opcion no valida.")
            continue
        try:
            PASOS[clave][1]()
        except SystemExit as e:
            print(f"\n{e}")
        except KeyboardInterrupt:
            print("\nInterrumpido.")


def main():
    ap = argparse.ArgumentParser(description="Asistente de despliegue de R1/R2 en un slice de VNRT")
    ap.add_argument("paso", nargs="?", choices=list(PASOS), help="paso a ejecutar; sin argumento abre el menu")
    args = ap.parse_args()
    try:
        PASOS[args.paso][1]() if args.paso else menu()
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        sys.exit(130)


if __name__ == "__main__":
    main()
