#!/usr/bin/env python3
"""Asistente para desplegar y probar R1/R2 en un slice de VNRT. Se ejecuta en la VM Controller.

    python3 slice.py              menu interactivo (va pidiendo los datos)
    python3 slice.py config       pregunta los datos del slice y los guarda en ~/slice.json
    python3 slice.py preparar     instala Ansible y copia tu llave SSH a switches y hosts
    python3 slice.py controller   levanta ONOS, LDAP, Policy Mapper y RADIUS y comprueba las apps de ONOS
    python3 slice.py switches     conecta los switches al controlador (Ansible)
    python3 slice.py hosts        instala en los hosts el agente 802.1X con interfaz web (Ansible); no los arranca
    python3 slice.py dhcp         instala en los hosts el cliente DHCP (la app DHCP de ONOS les da IP desde que arrancan)
    python3 slice.py portal       pone el portal cautivo (IP fija) en el host de tipo "portal" y fija los puertos de confianza
    python3 slice.py alta         autentica los hosts y fija en LDAP la MAC, el switch y el puerto de cada usuario
    python3 slice.py estado       muestra apps, switches, sesiones, flujos y LDAP
    python3 slice.py interfaz     muestra los tuneles SSH para abrir la interfaz web de cada host
    python3 slice.py limpiar      borra sesiones y flujos para repetir la prueba
    python3 slice.py todo         controller + switches + hosts + portal (si hay host de portal) + alta

Solo usa la biblioteca estandar. Cada host es una identidad de EQUIPO con certificado (alumno01..03 y support01, ver USUARIOS) o un host sin 802.1X
(portal, invitado, iot; ver TIPOS). Los profesores no llevan certificado en las PC: suben el equipo a Docente desde el portal.
"""
import argparse
import atexit
import base64
import getpass
import json
import os
import secrets
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
POLITICA_LOCAL = REPO / "R2" / "policy" / "policy.local.yaml"   # lo propio de este slice (no se versiona): R2/policy/policy_tables.py lo mezcla
ONOS = "http://127.0.0.1:8181/onos"
ONOS_AUTH = (os.environ.get("ONOS_USER", "onos"), os.environ.get("ONOS_PASS", "rocks"))
MAPPER = "http://127.0.0.1:5000"
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new"]
APPS = ["org.onosproject.openflow", "org.opencord.sadis", "org.opencord.aaa", "org.onosproject.dhcp",
        "org.onosproject.acl", "org.onosproject.vpls", "org.onosproject.flowanalyzer"]

# Identidades 802.1X de los EQUIPOS con su suplicante (R1/shared/mininet/supplicants). `clave` es la contrasena de la clave privada.
#  - alumno01..03: PC de aula (rol Alumno). Los certificados de alumno se copian a todos los hosts con 802.1X.
#  - support01: equipo de TI (rol Soporte): el certificado de administrador va SOLO al host al que se asigna, nunca a las PC de aula.
# Los profesores NO llevan certificado en las PC: entran por el portal con su cuenta personal de LDAP y suben el equipo de Alumno a
# Docente (docs/CERTIFICADOS_Y_ESCALABILIDAD.md, seccion 6).
USUARIOS = {
    "alumno01": {"rol": "Alumno", "vlan": 20, "conf": "wpa_alumno_01.conf", "clave": "student_password"},
    "alumno02": {"rol": "Alumno", "vlan": 20, "conf": "wpa_alumno_02.conf", "clave": "student_password"},
    "alumno03": {"rol": "Alumno", "vlan": 20, "conf": "wpa_alumno_03.conf", "clave": "student_password"},
    "support01": {"rol": "Soporte (Administrador)", "vlan": 40, "conf": "wpa_support_01.conf", "clave": "support_password"},
}
# Cuentas con certificado que NO se instalan por defecto en las PC: solo para probar el rol Docente por 802.1X en un host concreto
# (hay que escribir el nombre; el playbook copia a ese host solo su certificado y quita los de versiones anteriores).
USUARIOS_PRUEBA = {
    "profesor01": {"rol": "Docente", "vlan": 10, "conf": "wpa_profe_01.conf", "clave": "professor_password"},
    "profesor02": {"rol": "Docente", "vlan": 10, "conf": "wpa_profe_02.conf", "clave": "professor_password"},
}
# Red de datos del slice (la de la interfaz que va al switch). Los hosts toman su IP por DHCP (app DHCP de ONOS, rango en
# R1/deploy/.env.example); solo el portal tiene IP fija, la de portal.ip en R2/policy/policy.yaml (fuera del rango DHCP).
PORTAL_IP = "10.0.0.60"
PORTAL_PUERTO = 8443            # el portal de VNRT habla HTTPS (el laboratorio de Mininet conserva el 8080 de policy.yaml); portal.puerto de policy.local.yaml
PORTAL_TLS = DEPLOY / "hosts" / "portal-tls"      # certificado autofirmado del portal (no se versiona): hosts/portal-tls/portal.crt y portal.key
# Hosts sin 802.1X: no llevan agente ni certificado.
TIPOS = {
    "portal": f"Servidor del portal cautivo (https://{PORTAL_IP}:{PORTAL_PUERTO})",
    "invitado": "Invitado: entra por el portal (guest01)",
    "iot": "IoT: se identifica por su MAC (MAB)",
}


def con_certificado():
    return {**USUARIOS, **USUARIOS_PRUEBA}


def usa_802_1x(h):
    return h["usuario"] in con_certificado()


def descripcion(usuario):
    d = con_certificado().get(usuario)
    return f"{d['rol']}, VLAN {d['vlan']}" if d else TIPOS[usuario]


def ip_datos(h):
    """IP fija de la interfaz de datos: solo el servidor del portal la tiene; el resto la recibe por DHCP (None)."""
    return PORTAL_IP if h["usuario"] == "portal" else None


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


def opciones():
    return list(USUARIOS) + list(TIPOS)


def mostrar_usuarios():
    print("\nQue es cada host (identidad de equipo con certificado EAP-TLS, o un host sin 802.1X):")
    print(f"  {'N':<3}{'Usuario/tipo':<14}{'Rol':<44}Suplicante")
    for i, nombre in enumerate(opciones(), 1):
        d = USUARIOS.get(nombre)
        print(f"  {i:<3}{nombre:<14}{descripcion(nombre):<44}{d['conf'] if d else '-'}")
    print("  Solo para pruebas (escribe el nombre): " + ", ".join(USUARIOS_PRUEBA) +
          ". Los profesores no llevan certificado en las PC: suben el equipo desde el portal.")


def elegir_usuario(defecto):
    nombres = opciones()
    while True:
        r = preguntar("  Usuario o tipo (numero o nombre)", defecto)
        if r.isdigit() and 1 <= int(r) <= len(nombres):
            return nombres[int(r) - 1]
        if r in nombres or r in USUARIOS_PRUEBA:
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
    n_h = preguntar_entero("Cuantos hosts hay (h1, h2...)", len(cfg.get("hosts", [])) or 4)
    mostrar_usuarios()
    # alumnos (equipos con 802.1X), un servidor de portal y despues invitado e IoT (sin 802.1X)
    por_defecto = ["alumno01", "alumno02", "alumno03", "portal", "invitado", "iot"]
    hosts = []
    for i in range(1, n_h + 1):
        previo = next((h for h in cfg.get("hosts", []) if h["nombre"] == f"h{i}"), None)
        puerto = preguntar_entero(f"  Puerto SSH de h{i}", previo["puerto"] if previo else 5810 + i)
        usuario = elegir_usuario(previo["usuario"] if previo else por_defecto[(i - 1) % len(por_defecto)])
        hosts.append({"nombre": f"h{i}", "puerto": puerto, "usuario": usuario})
    usados = [h["usuario"] for h in hosts]
    repetidos = {u for u in usados if usados.count(u) > 1 and (u in con_certificado() or u == "portal")}
    if repetidos:
        print(f"\nATENCION: {', '.join(sorted(repetidos))} esta asignado a mas de un host. Una identidad solo puede tener "
              "una sesion a la vez (el segundo host sera rechazado como conflicto) y solo puede haber un servidor de portal.")
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
        print(f"  {h['nombre']}  puerto {h['puerto']}  {h['usuario']} ({descripcion(h['usuario'])})  IP de datos "
              f"{ip_datos(h) or 'por DHCP'}")


# ---------------------------------------------------------------- inventarios de Ansible

def inventario_switches(cfg):
    lineas = ["[switches]"]
    for s in cfg["switches"]:
        lineas.append(f"{s['nombre']} ansible_host={cfg['ip_publica']} ansible_port={s['puerto']} bridge={s['nombre']}")
    lineas += ["", "[switches:vars]", "ansible_user=ubuntu", "ansible_ssh_private_key_file=~/.ssh/id_ed25519",
               "ansible_ssh_common_args=-o StrictHostKeyChecking=accept-new",
               f"controller_ip={cfg['controller_ip']}", "controller_port=6653", ""]
    return "\n".join(lineas)


def token_env(nombre):
    """Clave compartida entre el Policy Mapper y un cliente suyo. Se genera una vez y se guarda en R1/deploy/.env; el compose se la
    pasa al Policy Mapper y el playbook, al servicio del host."""
    env = DEPLOY / ".env"
    if not env.exists():
        shutil.copy(DEPLOY / ".env.example", env)
    for linea in env.read_text().splitlines():
        if linea.startswith(nombre + "=") and linea.split("=", 1)[1].strip():
            return linea.split("=", 1)[1].strip()
    token = secrets.token_hex(16)
    contenido = env.read_text()
    env.write_text(contenido + ("" if contenido.endswith("\n") else "\n") + f"{nombre}={token}\n")
    return token


def token_agente():
    """AGENTE_TOKEN: la clave del agente 802.1X de cada host (POST /sessions/logout)."""
    return token_env("AGENTE_TOKEN")


def token_portal():
    """PORTAL_TOKEN: la clave del servidor del portal (X-Portal-Token en /portal/login: la MAC la aporta el portal, no el cliente)."""
    return token_env("PORTAL_TOKEN")


def inventario_hosts(cfg, token="", portal=""):
    """Tres grupos: [hosts] (con 802.1X: agente y certificado), [portal] (el servidor del portal, IP fija) y [sin_802_1x] (invitado, IoT).
    Todos menos el portal toman su IP por DHCP."""
    grupos = {"hosts": [], "portal": [], "sin_802_1x": []}
    for h in cfg["hosts"]:
        linea = f"{h['nombre']} ansible_host={cfg['ip_publica']} ansible_port={h['puerto']} iface={cfg['iface']}"
        if usa_802_1x(h):
            grupos["hosts"].append(f"{linea} conf={con_certificado()[h['usuario']]['conf']} cert={h['usuario']}@example.org.pem")
        elif h["usuario"] == "portal":
            grupos["portal"].append(f"{linea} ip_datos={ip_datos(h)}")      # el unico con IP fija
        else:
            grupos["sin_802_1x"].append(linea)
    lineas = []
    for nombre, filas in grupos.items():
        lineas += [f"[{nombre}]", *filas, ""]
    lineas += ["[all:vars]", "ansible_user=ubuntu", "ansible_ssh_private_key_file=~/.ssh/id_ed25519",
               "ansible_ssh_common_args=-o StrictHostKeyChecking=accept-new", f"controller_ip={cfg['controller_ip']}"]
    if token:
        lineas.append(f"agente_token={token}")
    if portal:
        lineas += [f"portal_token={portal}", f"portal_ip={PORTAL_IP}", f"portal_puerto={PORTAL_PUERTO}"]
    return "\n".join(lineas + [""])


def comandos_tunel(cfg):
    """Un tunel SSH por host para abrir su agente 802.1X en el navegador: h1 en el puerto local 8081, h2 en el 8082..."""
    return [(h["nombre"], 8080 + i, f"ssh -L {8080 + i}:127.0.0.1:8080 -p {h['puerto']} ubuntu@{cfg['ip_publica']}")
            for i, h in enumerate(cfg["hosts"], 1) if usa_802_1x(h)]


def comandos_tunel_portal(cfg):
    """Un tunel SSH por host (menos el del portal) para abrir el portal en el navegador de tu PC: la conexion sale DESDE ese host, asi que el
    portal ve su IP y su MAC (la sesion que se inicie sera la de ese host). Un tunel al host del portal le mostraria la MAC del propio portal."""
    return [(h["nombre"], 8090 + i, f"ssh -L {8090 + i}:{PORTAL_IP}:{PORTAL_PUERTO} -p {h['puerto']} ubuntu@{cfg['ip_publica']}")
            for i, h in enumerate(cfg["hosts"], 1) if h["usuario"] != "portal"]


def interfaz():
    cfg = config_o_salir()
    titulo("Interfaz web del agente 802.1X de cada host")
    print("Cada host con 802.1X tiene una pagina de autenticacion (como el dialogo de red de una PC) en su puerto 8080 local.")
    print("Para verla desde tu PC abre un tunel SSH y luego el navegador:\n")
    usuarios = {h["nombre"]: h["usuario"] for h in cfg["hosts"]}
    for nombre, local, cmd in comandos_tunel(cfg):
        print(f"  {nombre} ({usuarios[nombre]}):  {cmd}\n      -> http://localhost:{local}")
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
    token_agente()   # AGENTE_TOKEN en .env: up.sh lo exporta y el compose se lo pasa al Policy Mapper
    token_portal()   # y PORTAL_TOKEN, la clave del servidor del portal
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
    titulo("Hosts (suplicantes 802.1X y cliente DHCP)")
    carpeta = DEPLOY / "hosts"
    (carpeta / "inventory.ini").write_text(inventario_hosts(cfg, token_agente(), token_portal()))
    archivo_sudo()
    ejecutar(["ansible", "-i", "inventory.ini", "all", "-m", "ping"], cwd=carpeta)
    playbook_hosts()
    playbook_dhcp()
    interfaz()


def playbook_dhcp():
    ejecutar(["ansible-playbook", "-i", "inventory.ini", "dhcp.yml", "-e", f"@{archivo_sudo()}"], cwd=DEPLOY / "hosts")


def dhcp():
    """Solo el cliente DHCP de los hosts (sin tocar el agente 802.1X)."""
    cfg = config_o_salir()
    titulo("Cliente DHCP en los hosts")
    (DEPLOY / "hosts" / "inventory.ini").write_text(inventario_hosts(cfg, token_agente(), token_portal()))
    playbook_dhcp()
    concesiones()


def concesiones():
    """Las IP que la app DHCP de ONOS ha dado (GET /onos/dhcp/dhcp/mappings)."""
    codigo, d = onos("GET", "/dhcp/dhcp/mappings")
    filas = (d or {}).get("mappings", []) if codigo == 200 else None
    if filas is None:
        print(f"  No pude leer las concesiones de la app DHCP de ONOS (HTTP {codigo}): `python3 slice.py estado` apps, y docker logs onos.")
        return
    for f in filas:
        print(f"  {f.get('host', '?'):<22}{f.get('ip', '?')}")
    print(f"  total: {len(filas)} concesion(es)")


# ---------------------------------------------------------------- portal cautivo en un host

def ubicar_portal(hosts_onos):
    """(mac, dpid, puerto) donde ONOS ve la IP del portal (entre sus hosts), o None."""
    for h in hosts_onos:
        if PORTAL_IP in h.get("ipAddresses", []) and h.get("locations"):
            loc = h["locations"][0]
            return h.get("mac", "").lower(), loc["elementId"], str(loc["port"])
    return None


def puertos_de_confianza(enlaces, portal):
    """{dpid: [puertos]} de confianza: los de los enlaces entre switches (troncales) y el del host del portal. Por ellos pasa el trafico
    sin sesion de 802.1X (las respuestas del portal y el trafico de un switch al otro); R2 no les aplica las tablas de rol."""
    res = {}
    for e in enlaces:
        for lado in (e["src"], e["dst"]):
            res.setdefault(lado["device"], set()).add(int(lado["port"]))
    res.setdefault(portal[1], set()).add(int(portal[2]))
    return {d: sorted(p) for d, p in sorted(res.items())}


def politica_local(confianza, mac, switches):
    """Texto de R2/policy/policy.local.yaml: puertos de confianza y portal de este slice (se mezclan con los de policy.yaml)."""
    lineas = ["# Generado por `slice.py portal` para este slice de VNRT. No se versiona: pisa lo de policy.yaml.", "trusted:"]
    lineas += [f'  "{d}": [{", ".join(str(p) for p in ps)}]' for d, ps in confianza.items()]
    lineas += ["portal:", f"  ip: {PORTAL_IP}", f'  mac: "{mac}"', f"  puerto: {PORTAL_PUERTO}",
               "  switches: [" + ", ".join(f'"{s}"' for s in switches) + "]",
               "  desvios: []          # el desvio del HTTP hacia un destino original es del laboratorio de Mininet", ""]
    return "\n".join(lineas)


def certificado_portal():
    """Certificado autofirmado del portal (subjectAltName = su IP), una sola vez. Se FIJA en cada host (`portal-curl`): un equipo que anuncie
    la IP del portal por ARP no puede presentarlo y no captura las contrasenas. Para renovarlo, borra hosts/portal-tls y repite `portal`."""
    cert, clave = PORTAL_TLS / "portal.crt", PORTAL_TLS / "portal.key"
    if cert.exists() and clave.exists():
        return cert, clave
    if shutil.which("openssl") is None:
        raise SystemExit("Falta openssl en el Controller: sudo apt-get install -y openssl")
    PORTAL_TLS.mkdir(parents=True, exist_ok=True)
    ejecutar(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-keyout", clave, "-out", cert,
              "-subj", "/CN=portal.r1.local", "-addext", f"subjectAltName=IP:{PORTAL_IP}"], capturar=True)
    os.chmod(clave, 0o600)
    return cert, clave


def portal():
    cfg = config_o_salir()
    titulo("Portal cautivo en un host (IP fija) y puertos de confianza")
    host = next((h for h in cfg["hosts"] if h["usuario"] == "portal"), None)
    if host is None:
        print("Ningun host esta asignado como 'portal'. Ejecuta `python3 slice.py config` y elige el tipo portal para uno de ellos.")
        return
    carpeta = DEPLOY / "hosts"
    token = token_portal()
    certificado_portal()
    (carpeta / "inventory.ini").write_text(inventario_hosts(cfg, token_agente(), token))
    ejecutar(["ansible-playbook", "-i", "inventory.ini", "portal.yml", "-e", f"@{archivo_sudo()}"], cwd=carpeta)
    en_mapper = (ejecutar(["docker", "exec", "policy_mapper", "printenv", "PORTAL_TOKEN"], check=False, capturar=True) or "").strip()
    if en_mapper != token:
        print("AVISO: el Policy Mapper no tiene la clave del portal (PORTAL_TOKEN): repite `python3 slice.py controller` para recrearlo "
              "con ella; si no, el portal recibira 403 al iniciar sesion.")
    titulo("Ubicacion del portal en ONOS")
    ubic = None
    for _ in range(12):
        ubic = ubicar_portal((onos("GET", "/v1/hosts")[1] or {}).get("hosts", []))
        if ubic:
            break
        print(".", end="", flush=True)
        time.sleep(5)
    if not ubic:
        print(f"\nONOS aun no ve {PORTAL_IP}: el host del portal debe enviar ARP (el playbook lo anuncia). Revisa `ip -br addr` en "
              f"{host['nombre']} y repite `python3 slice.py portal`.")
        return
    mac, dpid, puerto = ubic
    print(f"  {host['nombre']}: {PORTAL_IP} MAC {mac} en {dpid} puerto {puerto}")
    enlaces = (onos("GET", "/v1/links")[1] or {}).get("links", [])
    dispositivos = [d["id"] for d in (onos("GET", "/v1/devices")[1] or {}).get("devices", []) if d.get("available")]
    confianza = puertos_de_confianza(enlaces, ubic)
    POLITICA_LOCAL.write_text(politica_local(confianza, mac, dispositivos))
    print(f"  Escrito {POLITICA_LOCAL}")
    for d, ps in confianza.items():
        print(f"    confianza {d}: puertos {ps}")
    ejecutar(["docker", "restart", "policy_mapper"], capturar=True)
    time.sleep(5)
    http("POST", MAPPER + "/policy/sync")
    print(f"\nListo. Portal en https://{PORTAL_IP}:{PORTAL_PUERTO} (visible solo desde la red de datos del slice).")
    print("El Policy Mapper se reinicio: se perdieron las sesiones, repite `python3 slice.py alta`.")
    print("Prueba desde un host con sesion de Alumno (el portal toma la MAC del host de su tabla ARP):")
    print("(`portal-curl` es curl con el certificado del portal fijado; sin el, curl rechaza al portal)")
    print(f"  portal-curl -s https://{PORTAL_IP}:{PORTAL_PUERTO}/ | grep -i -o 'Subir de rol'            # el equipo puede subir")
    print(f"  portal-curl -s -d 'usuario=profesor01&clave=password123' https://{PORTAL_IP}:{PORTAL_PUERTO}/login | grep -i -o 'Docente'")
    print("O en el navegador de tu PC, con un tunel SSH A TRAVES del host cuya sesion quieres usar (el portal ve la MAC de ese host;")
    print("el navegador avisara del certificado: es autofirmado y el nombre es localhost; aceptalo solo para esta prueba):")
    for nombre, local, cmd in comandos_tunel_portal(cfg):
        print(f"  {nombre}:  {cmd}      -> https://localhost:{local}")


def reiniciar_aaa():
    """Reinicia la app AAA de ONOS: borra sus maquinas de estado. Si un cliente se fue sin EAPOL-Logoff, ONOS lo sigue dando
    por AUTHORIZED y puede ignorar su siguiente EAPOL-Start (el cliente espera 60 s y falla)."""
    onos("DELETE", "/v1/applications/org.opencord.aaa/active")
    time.sleep(3)
    onos("POST", "/v1/applications/org.opencord.aaa/active")
    time.sleep(3)
    print("  App AAA de ONOS reiniciada (estado de autenticaciones limpio)")


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
    usuarios = [h["usuario"] for h in cfg["hosts"] if usa_802_1x(h)]
    n = len(usuarios)
    if not n:
        print("Ningun host usa 802.1X (todos son portal, invitado o iot): no hay nada que autenticar.")
        return
    carpeta = DEPLOY / "hosts"
    (carpeta / "inventory.ini").write_text(inventario_hosts(cfg, token_agente(), token_portal()))   # siempre: lleva las claves y la config actual
    archivo_sudo()
    titulo("1/4 Liberar a los usuarios en LDAP (sin MAC/switch/puerto: la primera autenticacion pasa)")
    for u in usuarios:
        ldap("liberar", u)
        print(f"  {u}")
    ejecutar(["docker", "restart", "policy_mapper"], capturar=True)
    time.sleep(5)
    reiniciar_aaa()
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
    reiniciar_aaa()
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
    titulo("Concesiones DHCP (app DHCP de ONOS)")
    concesiones()
    titulo("LDAP")
    for h in (h for h in cfg["hosts"] if usa_802_1x(h)):
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
    reiniciar_aaa()
    http("POST", MAPPER + "/policy/sync")
    print("Sesiones y flujos borrados; tablas de rol reinstaladas.")


def todo():
    controller()
    switches()
    hosts()
    if any(h["usuario"] == "portal" for h in config_o_salir()["hosts"]):
        portal()
    alta()


PASOS = {
    "config": ("Configurar los datos del slice", config),
    "preparar": ("Instalar Ansible y copiar la llave SSH", preparar),
    "controller": ("Levantar ONOS, LDAP, Policy Mapper y RADIUS", controller),
    "switches": ("Conectar los switches al controlador", switches),
    "hosts": ("Preparar los hosts como suplicantes", hosts),
    "dhcp": ("Cliente DHCP en los hosts (IP desde que arrancan)", dhcp),
    "portal": ("Portal cautivo en un host (IP fija) y puertos de confianza", portal),
    "alta": ("Autenticar los hosts y fijar su ubicacion en LDAP", alta),
    "estado": ("Ver el estado (apps, switches, sesiones, flujos, LDAP)", estado),
    "interfaz": ("Ver como abrir la interfaz web de cada host (tuneles SSH)", interfaz),
    "limpiar": ("Borrar sesiones y flujos para repetir la prueba", limpiar),
    "todo": ("Todo: controller + switches + hosts + portal + alta", todo),
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
