#!/usr/bin/env python3
"""Verificación de R5 en la topología de 14 VM (VNRT). Solo lectura salvo con --activo.

Se ejecuta desde switch-borde (el nodo de control, 192.168.201.254), igual que lanzar.sh:

    cd ~/proyecto-sdn && git pull
    python3 R5/deploy/verificar_r5.py                 # pide la contraseña de ubuntu una vez (no se guarda)
    python3 R5/deploy/verificar_r5.py --sin-ssh       # solo lo que se ve por REST de ONOS y por OVS local (sin contraseña)
    python3 R5/deploy/verificar_r5.py --activo        # además: bloquea y desbloquea una IP de prueba (203.0.113.250) en ONOS
    python3 R5/deploy/verificar_r5.py --json r5.json  # guarda también el resultado en JSON

Solo usa la biblioteca estándar de Python 3 (más sshpass/ssh para llegar a las VM). La contraseña va por stdin/variable de entorno,
nunca en la línea de comandos. Cada comprobación termina en OK, FALLA, AVISO o INFO; el código de salida es 1 si hay alguna FALLA.
"""
import argparse
import base64
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- datos de la topología (los mismos de group_vars/all.yml)
ONOS_NODOS = {"node-ctrl-1": "192.168.201.1", "node-ctrl-2": "192.168.201.2", "node-ctrl-3": "192.168.201.3"}
ONOS_PUERTO, ONOS_USER, ONOS_PASS = 8181, "onos", "rocks"
MAPPER = "http://192.168.201.1:5000"
SWITCHES = {"switch-borde": None, "sw-core-1": "192.168.201.13", "sw-core-2": "192.168.201.14",
            "sw-acc-1": "192.168.201.12", "sw-acc-2": "192.168.201.11"}            # accesos como en inventario.ini (nombres de VNRT)
SENSORES = {"node-sec-1": "192.168.201.31", "node-sec-2": "192.168.201.32"}
BRIDGE, NIC_ESPEJO = "br0", "ens5"
PUERTOS_ESPEJO_BORDE = {"node-sec-1": "ens8", "node-sec-2": "ens9"}
ENTRADAS_BORDE = ["ens4", "ens6", "ens7"]
PREFIJOS_ESPEJO = 2                      # redes.home + redes.externa
APP_R5, APP_ESPEJO, PRIO_ESPEJO, PRIO_DROP = "org.campus.r5", ("org.campus.espejo", "org.onosproject.rest"), 41000, 42000
IP_PRUEBA = "203.0.113.250"
REPO = "~/proyecto-sdn"
USUARIO = "ubuntu"

# ---------------------------------------------------------------- salida
COLOR = sys.stdout.isatty()
CODIGOS = {"OK": "32", "FALLA": "31", "AVISO": "33", "INFO": "36"}
RESULTADOS = []          # (seccion, estado, texto)
SECCION = [""]
LOG = []


def _pinta(estado):
    return f"\033[{CODIGOS[estado]}m{estado:<5}\033[0m" if COLOR else f"{estado:<5}"


def seccion(titulo):
    SECCION[0] = titulo
    linea = f"\n=== {titulo} ".ljust(78, "=")
    print(linea)
    LOG.append(linea)


def reg(estado, texto, detalle=None):
    RESULTADOS.append((SECCION[0], estado, texto))
    print(f"[{_pinta(estado)}] {texto}")
    LOG.append(f"[{estado:<5}] {texto}")
    for l in (detalle or "").strip().splitlines():
        print(f"         {l}")
        LOG.append(f"         {l}")


ok = lambda t, d=None: reg("OK", t, d)
falla = lambda t, d=None: reg("FALLA", t, d)
aviso = lambda t, d=None: reg("AVISO", t, d)
info = lambda t, d=None: reg("INFO", t, d)


def cond(c, texto_ok, texto_mal, detalle=None, mal="FALLA"):
    reg("OK" if c else mal, texto_ok if c else texto_mal, detalle)
    return c


# ---------------------------------------------------------------- acceso a ONOS (REST, sin dependencias)
def rest(ip, ruta, metodo="GET", cuerpo=None, usuario=ONOS_USER, clave=ONOS_PASS, puerto=ONOS_PUERTO, base="/onos/v1", timeout=6):
    """Devuelve (codigo, json_o_texto) o (None, mensaje) si no hay conexión."""
    url = f"http://{ip}:{puerto}{base}{ruta}"
    cab = {"Authorization": "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()}
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode()
        cab["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf8", "replace")
            codigo = r.status
    except urllib.error.HTTPError as e:
        codigo, txt = e.code, e.read().decode("utf8", "replace")
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return None, str(e)
    try:
        return codigo, json.loads(txt) if txt.strip() else {}
    except ValueError:
        return codigo, txt


# ---------------------------------------------------------------- ejecución en las VM
def _es_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


class Remoto:
    def __init__(self, clave, usar_ssh):
        self.clave = clave
        self.usar_ssh = usar_ssh and clave is not None
        self.local = socket.gethostname()
        self.sshpass = shutil.which("sshpass")

    def _sudo_local(self, script):
        if _es_root():
            return ["bash", "-c", script], None
        return ["sudo", "-S", "-p", "", "bash", "-c", script], (self.clave + "\n") if self.clave else None

    def correr(self, host, script, sudo=True, timeout=25):
        """Ejecuta `script` (bash) en `host` ('switch-borde' = esta VM). Devuelve (rc, salida) o (None, motivo)."""
        if host in ("switch-borde", self.local):
            if sudo:
                if self.clave is None and not _es_root():
                    return None, "sin contraseña de sudo (use --sin-ssh solo para lo que no necesita sudo)"
                cmd, stdin = self._sudo_local(script)
            else:
                cmd, stdin = ["bash", "-c", script], None
        else:
            if not self.usar_ssh:
                return None, "SSH desactivado (--sin-ssh)"
            ip = {**{k: v for k, v in SWITCHES.items() if v}, **SENSORES, **ONOS_NODOS}.get(host, host)
            remoto = f"sudo -S -p '' bash -c {shlex.quote(script)}" if sudo else f"bash -c {shlex.quote(script)}"
            ssh = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                   "-o", "ConnectTimeout=8", f"{USUARIO}@{ip}", remoto]
            cmd = ([self.sshpass, "-e"] + ssh) if self.sshpass else (["ssh", "-o", "BatchMode=yes"] + ssh[1:])
            stdin = (self.clave + "\n") if sudo else None
        env = dict(os.environ, SSHPASS=self.clave or "")
        try:
            p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return None, "tiempo agotado"
        except OSError as e:
            return None, str(e)
        salida = (p.stdout + ("\n" + p.stderr if p.returncode and p.stderr else "")).strip()
        salida = "\n".join(l for l in salida.splitlines() if "[sudo]" not in l and "password for" not in l)
        return p.returncode, salida


def dpid_de(rem, host):
    rc, out = rem.correr(host, f"ovs-vsctl get bridge {BRIDGE} datapath_id")
    if rc == 0 and out:
        return "of:" + out.strip().strip('"')
    return None


# ================================================================ 1. ONOS
def revisar_onos(rem, nombres):
    seccion("1. ONOS: clúster, switches y aplicaciones")
    vivo = None
    for nombre, ip in ONOS_NODOS.items():
        c, _ = rest(ip, "/cluster")
        if c == 200:
            ok(f"{nombre} ({ip}) responde por REST")
            vivo = vivo or ip
        else:
            falla(f"{nombre} ({ip}) no responde por REST", f"respuesta: {_}")
    if not vivo:
        falla("ningún nodo ONOS responde: no se puede seguir con las demás comprobaciones de ONOS")
        return None, {}
    c, cl = rest(vivo, "/cluster")
    nodos = cl.get("nodes", []) if isinstance(cl, dict) else []
    listos = [n for n in nodos if n.get("status") == "READY"]
    cond(len(nodos) == 3 and len(listos) == 3, f"clúster ONOS: {len(listos)}/3 nodos READY",
         f"clúster ONOS: {len(listos)} READY de {len(nodos)} vistos",
         "\n".join(f"{n.get('id')}  {n.get('status')}" for n in nodos))
    c, d = rest(vivo, "/devices")
    disp = d.get("devices", []) if isinstance(d, dict) else []
    disponibles = [x for x in disp if x.get("available")]
    cond(len(disp) == 5 and len(disponibles) == 5, f"{len(disponibles)}/5 switches disponibles en ONOS",
         f"{len(disponibles)} de {len(disp)} switches disponibles (se esperan 5)")
    mapa = {}
    for x in sorted(disp, key=lambda x: nombres.get(x["id"], x["id"])):
        c, m = rest(vivo, f"/mastership/{x['id']}/master")
        maestro = m.get("nodeId") if isinstance(m, dict) else "?"
        mapa[x["id"]] = maestro
    info("maestro de cada switch", "\n".join(f"{nombres.get(i, i):<13} {i}  maestro={m}" for i, m in mapa.items()))
    c, enl = rest(vivo, "/links")
    n_enl = len(enl.get("links", [])) if isinstance(enl, dict) else 0
    cond(n_enl > 0, f"{n_enl} enlaces (dirigidos) descubiertos por LLDP", "ONOS no ve ningún enlace entre switches")
    c, ap = rest(vivo, "/applications")
    activas = {a["name"]: a for a in ap.get("applications", []) if a.get("state") == "ACTIVE"} if isinstance(ap, dict) else {}
    cond("org.onosproject.fwd" not in activas, "reenvío reactivo (fwd) apagado: no compite con R2/R5",
         "org.onosproject.fwd está ACTIVE: inunda y choca con las reglas de R2/R5", mal="AVISO")
    cond("org.onosproject.openflow" in activas, "app openflow activa", "app openflow NO activa")
    propias = sorted(n for n in activas if "campus" in n or "opencord" in n or "fallback" in n)
    info("apps del proyecto activas", ", ".join(propias) or "(ninguna)")
    return vivo, mapa


# ================================================================ 2. switch-borde: espejo y DROP en ONOS y en OVS
def revisar_borde(rem, vivo, nombres, dpid_borde, macs_sensor):
    seccion("2. Espejo y DROP de R5 en switch-borde (ONOS y OVS)")
    if not dpid_borde:
        falla("no se pudo leer el deviceId de switch-borde (ovs-vsctl): se omite esta sección")
        return
    c, f = rest(vivo, f"/flows/{dpid_borde}")
    flujos = f.get("flows", []) if isinstance(f, dict) else []
    info(f"switch-borde = {dpid_borde}", f"{len(flujos)} flujos en ONOS")
    espejo = [x for x in flujos if x.get("priority") == PRIO_ESPEJO and x.get("appId") in APP_ESPEJO]
    prefijos = {}
    for x in espejo:
        ip = next((c["ip"] for c in x["selector"]["criteria"] if c["type"] == "IPV4_SRC"), "?")
        prefijos[ip] = prefijos.get(ip, 0) + 1
    vigentes = {p: n for p, n in prefijos.items() if p in ("10.0.0.0/16", "203.0.113.0/24")}
    viejas = {p: n for p, n in prefijos.items() if p not in vigentes}
    cond(len(vigentes) == 2 and all(n == len(ENTRADAS_BORDE) for n in vigentes.values()),
         f"espejo del borde con los prefijos vigentes {sorted(vigentes)}: {len(ENTRADAS_BORDE)} entradas cada uno (prioridad {PRIO_ESPEJO}, appId org.campus.espejo)",
         f"espejo del borde incompleto: prefijos {prefijos}; se esperan 10.0.0.0/16 y 203.0.113.0/24 en {len(ENTRADAS_BORDE)} entradas cada uno")
    if viejas:
        aviso(f"quedan reglas de espejo de prefijos viejos {viejas} (inofensivas; retirar y reinstalar el espejo para limpiarlas)")
    if espejo:
        estados = {x.get("state") for x in espejo}
        cond(estados == {"ADDED"}, "todas las reglas de espejo en estado ADDED", f"estados de las reglas de espejo: {sorted(estados)}")
        paquetes = sum(x.get("packets", 0) for x in espejo)
        cond(paquetes > 0, f"el espejo ya copió {paquetes} paquetes",
             "el espejo no ha copiado ningún paquete (normal si todavía no pasó tráfico IP por las entradas del borde)", mal="AVISO")
        macs = {i["mac"].lower() for x in espejo for i in x["treatment"]["instructions"] if i.get("subtype") == "ETH_DST"}
        modo_grupo = any(i.get("type") == "GROUP" for x in espejo for i in x["treatment"]["instructions"])
        if modo_grupo:                       # modo grupo: las MAC viven en los cubos del grupo ALL del switch
            cg, gs = rest(vivo, f"/groups/{dpid_borde}")
            for g in (gs.get("groups", []) if isinstance(gs, dict) else []):
                if str(g.get("appCookie", "")).lower().startswith("0x52354d"):
                    for b in g.get("buckets", []):
                        for i in (b.get("treatment") or {}).get("instructions", []):
                            if i.get("subtype") == "ETH_DST":
                                macs.add(i["mac"].lower())
                    info(f"espejo en modo grupo: grupo {g.get('id')} del borde en estado {g.get('state')}")
        if macs_sensor:
            reales = {m.lower() for m in macs_sensor.values() if m}
            cond(macs == reales, "las MAC de destino del espejo coinciden con las de ens5 de los dos sensores",
                 "las MAC del espejo NO coinciden con las de ens5 de los sensores: la red de VNRT descartará las copias",
                 f"en las reglas: {sorted(macs)}\nen los sensores: {sorted(reales)}")
        else:
            info("MAC de destino del espejo (no se cotejaron con los sensores)", ", ".join(sorted(macs)))
        sin_salida = [x for x in espejo if not any(i.get("port") == "NORMAL" or i.get("type") == "TABLE" for i in x["treatment"]["instructions"])]
        cond(not sin_salida, "cada regla de espejo deja seguir al paquete original (NORMAL o goto_table): el tráfico real no se interrumpe",
             f"{len(sin_salida)} reglas de espejo sin reenvío del original: cortarían el tráfico")
        if modo_grupo:
            cond(all(any(i.get("type") == "TABLE" and int(i.get("tableId", -1)) == 60 for i in x["treatment"]["instructions"]) for x in espejo),
                 "el espejo sigue la tabla 60 (reenvío de R2 y de la capa 3): sin NORMAL, sin duplicados",
                 "algunas reglas de espejo no hacen goto_table 60")
    drops = [x for x in flujos if x.get("appId") == APP_R5 and x.get("priority") == PRIO_DROP]
    info(f"{len(drops)} DROP de R5 activos (prioridad {PRIO_DROP})",
         "\n".join(f"{next((c['ip'] for c in x['selector']['criteria'] if c['type'] == 'IPV4_SRC'), '?')} paquetes={x.get('packets', 0)} "
                   f"vida={x.get('life', 0)}s" for x in drops))
    # prioridades: quién más vive sobre/junto al espejo
    hist = {}
    for x in flujos:
        hist[x.get("priority")] = hist.get(x.get("priority"), 0) + 1
    altas = {p: n for p, n in hist.items() if p is not None and p >= 40000}
    info("flujos del borde con prioridad >= 40000 (prioridad: cantidad)", ", ".join(f"{p}: {n}" for p, n in sorted(altas.items(), reverse=True)))
    # puertos de espejo por nombre
    c, pu = rest(vivo, f"/devices/{dpid_borde}/ports")
    puertos = {}
    for p in (pu.get("ports", []) if isinstance(pu, dict) else []):
        nom = (p.get("annotations") or {}).get("portName")
        if nom:
            puertos[nom] = p
    for s, nic in PUERTOS_ESPEJO_BORDE.items():
        cond(nic in puertos and puertos[nic].get("isEnabled"), f"puerto {nic} del borde (hacia {s}) existe y está habilitado en ONOS",
             f"puerto {nic} del borde (hacia {s}) no aparece habilitado en ONOS")
    # lo mismo directamente en el switch
    rc, out = rem.correr("switch-borde", f"ovs-ofctl -O OpenFlow13 dump-flows {BRIDGE} | grep -c 'priority={PRIO_ESPEJO},'")
    if rc is not None:
        n = int(out.strip() or 0) if out.strip().isdigit() else 0
        cond(n == len(espejo) and n > 0, f"OVS local del borde tiene {n} flujos de prioridad {PRIO_ESPEJO} (coincide con ONOS)",
             f"OVS local del borde tiene {n} flujos de prioridad {PRIO_ESPEJO}; ONOS dice {len(espejo)}")
    else:
        info("no se pudo leer OVS local del borde", out)


# ================================================================ 3. switch-borde: plano de datos y NAT
def revisar_plano_borde(rem):
    seccion("3. switch-borde: puertos, controlador, NAT y red externa")
    rc, out = rem.correr("switch-borde", f"ovs-vsctl list-ports {BRIDGE}; echo ---; ovs-vsctl get-fail-mode {BRIDGE}; "
                         f"ovs-vsctl get bridge {BRIDGE} protocols; echo ---; ovs-vsctl get-controller {BRIDGE}; "
                         f"ovs-vsctl list controller | grep -E 'target|is_connected'; echo ---; sysctl -n net.ipv4.ip_forward; "
                         f"echo ---; iptables -t nat -S POSTROUTING 2>/dev/null; echo ---; ip -4 -br addr")
    if rc is None:
        aviso("no se pudo leer el plano de datos del borde", out)
        return
    partes = out.split("---")
    partes += [""] * (7 - len(partes))
    puertos = set(partes[0].split())
    faltan = [p for p in ENTRADAS_BORDE + list(PUERTOS_ESPEJO_BORDE.values()) if p not in puertos]
    cond(not faltan, f"puertos de br0: {', '.join(sorted(puertos))}", f"faltan en br0 los puertos {faltan}", f"hay: {sorted(puertos)}")
    cond("secure" in partes[1], "fail-mode secure", "fail-mode no es secure (si ONOS cae, el switch se vuelve un hub/learning)")
    cond("OpenFlow13" in partes[1], "protocolo OpenFlow13", "OpenFlow13 no configurado")
    conectado = re.findall(r"is_connected\s*:\s*(\w+)", partes[2])
    cond(conectado and all(c == "true" for c in conectado) and len(conectado) >= 1,
         f"controladores OpenFlow conectados: {conectado.count('true')}/{len(conectado)}",
         f"controladores OpenFlow del borde: {conectado}", "\n".join(l for l in partes[2].splitlines() if "tcp:" in l))
    cond(partes[3].strip() == "1", "ip_forward = 1 en el borde", "ip_forward no está en 1: el borde no enruta hacia el atacante", mal="AVISO")
    cond("MASQUERADE" in partes[4] or "SNAT" in partes[4], "NAT de salida (MASQUERADE) presente", "no hay NAT de salida en el borde", mal="AVISO")
    ext = [l for l in partes[5].splitlines() if "203.0.113." in l]
    if ext:
        ok("el borde tiene IP en la red externa 203.0.113.0/24 (del atacante)", "\n".join(ext))
    else:
        aviso("el borde NO tiene IP en 203.0.113.0/24: la red externa del atacante sigue pendiente",
              "sin esto, vm-atacante no puede generar tráfico con su IP externa que el espejo reconozca (redes.externa)")


# ================================================================ 4. sensores
def revisar_sensor(rem, s, vivo, dpid_borde):
    seccion(f"4. Sensor {s}: Suricata, integrador, NIC de espejo")
    rc, out = rem.correr(s, "echo ok", sudo=False)
    if rc is None:
        falla(f"no se pudo entrar por SSH a {s}", out)
        return None
    ok(f"SSH a {s} funciona")
    rc, out = rem.correr(s, f"ip -o link show {NIC_ESPEJO}; ip -4 -br addr show {NIC_ESPEJO}; cat /sys/class/net/{NIC_ESPEJO}/address; "
                            f"echo ---; df -h / | tail -1; free -m | awk 'NR==2{{print $2\" MB RAM, \"$7\" MB libre\"}}'")
    mac = None
    if rc == 0:
        lineas = out.splitlines()
        mac = next((l.strip() for l in lineas if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", l.strip())), None)
        cond("PROMISC" in out and "UP" in out.split("---")[0], f"{NIC_ESPEJO} UP y en modo promiscuo (MAC {mac})",
             f"{NIC_ESPEJO} no está UP/PROMISC (¿se reinició la VM? repetir ./lanzar.sh --tags sensores)", out.split("---")[0])
        sin_ip = not re.search(rf"{NIC_ESPEJO}\s+\w+\s+\d+\.\d+", out.split("---")[0])
        cond(sin_ip, f"{NIC_ESPEJO} sin IPv4 (el sensor no transmite hacia el borde)", f"{NIC_ESPEJO} tiene IPv4: el sensor podría transmitir por el espejo")
        info("recursos", out.split("---")[-1])
    else:
        falla(f"no se pudo leer {NIC_ESPEJO}", out)
    rc, out = rem.correr(s, "docker ps -a --format '{{.Names}}|{{.Status}}|{{.Image}}'; echo ---; "
                            "for c in r5-suricata r5-integrador; do echo $c $(docker inspect -f '{{.RestartCount}}' $c 2>/dev/null); done")
    if rc == 0:
        contenedores = {l.split("|")[0]: l.split("|")[1] for l in out.split("---")[0].splitlines() if "|" in l}
        for c in ("r5-suricata", "r5-integrador"):
            cond(contenedores.get(c, "").startswith("Up"), f"contenedor {c}: {contenedores.get(c)}",
                 f"contenedor {c}: {contenedores.get(c, 'NO EXISTE')}")
        reinicios = dict(l.split() for l in out.split("---")[1].splitlines() if len(l.split()) == 2)
        for c, n in reinicios.items():
            if n.isdigit() and int(n) > 0:
                aviso(f"{c} se reinició {n} veces", f"docker logs --tail 40 {c}")
    else:
        falla("no se pudo consultar Docker", out)
    rc, out = rem.correr(s, f"cd {REPO} && git rev-parse --abbrev-ref HEAD && git log -1 --format='%h %ad %s' --date=short | cut -c1-90; "
                            f"echo ---; python3 R5/codigo/topologia.py R5/deploy/topologia.yaml 2>&1 | tail -3; echo ---; "
                            f"grep -E '^(SENSOR|IDS_IFACES|P_R5|P_ESPEJO|MAX_SEVERITY|BLOCK_SEC|SURICATA_ET)=' R5/deploy/.env 2>&1", sudo=False)
    if rc == 0:
        p = out.split("---")
        info("repo del sensor (rama / último commit)", p[0])
        cond(p[1].strip().startswith("OK"), f"topologia.yaml válida: {p[1].strip()[:110]}", "topologia.yaml inválida o ausente", p[1])
        env = dict(l.split("=", 1) for l in p[2].strip().splitlines() if "=" in l)
        cond(env.get("SENSOR") == s and NIC_ESPEJO in env.get("IDS_IFACES", "").split(","), f".env: SENSOR={env.get('SENSOR')} IDS_IFACES={env.get('IDS_IFACES')}",
             f".env no coincide con este sensor (SENSOR={env.get('SENSOR')}, IDS_IFACES={env.get('IDS_IFACES')})")
    else:
        falla("no se pudo leer repo/topologia.yaml/.env del sensor", out)
    rc, out = rem.correr(s, "docker exec r5-suricata sh -c 'grep -m1 -E \"^ *HOME_NET:\" /etc/suricata/suricata.yaml; "
                            "grep -c \"^alert\" /var/lib/suricata/rules/local.rules; ls -l /var/log/suricata/eve-alerts.json 2>&1; "
                            "grep -c eve-alerts.json /var/log/suricata/suricata.log 2>/dev/null; "
                            "grep -E \"capture.kernel_(packets|drops)\" /var/log/suricata/stats.log 2>/dev/null | tail -2'")
    if rc == 0:
        l = out.splitlines()
        info("Suricata HOME_NET / reglas locales / archivo de alertas", "\n".join(l[:3]))
        cond(len(l) > 1 and l[1].strip() == "6", "6 reglas locales cargadas (escaneo SYN, flood, sqlmap, C2, UDP, ICMP)",
             f"reglas locales cargadas: {l[1] if len(l) > 1 else '?'} (se esperan 6)", mal="AVISO")
        stats = [x for x in l[3:] if "capture.kernel" in x]
        info("estadísticas de captura de Suricata (acumuladas)", "\n".join(stats) or "(aún sin estadísticas)")
    rc, out = rem.correr(s, "docker logs --tail 400 r5-suricata 2>&1 | grep -iE 'engine started|rules successfully loaded|failed|error|cannot' | tail -6")
    if rc == 0:
        arrancado = "ngine started" in out
        cond(arrancado, "Suricata: «Engine started» (captura en marcha)", "Suricata no muestra «Engine started» en sus últimos logs", out, mal="AVISO")
        malos = [x for x in out.splitlines() if re.search(r"error|failed|cannot", x, re.I) and not re.search(r"0 rules failed", x)]
        cond(not malos, "Suricata sin errores en los últimos logs", "Suricata registra errores", "\n".join(malos), mal="AVISO")
    rc, out = rem.correr(s, f"curl -s -m 5 -u {ONOS_USER}:{ONOS_PASS} -o /dev/null -w '%{{http_code}}' http://{ONOS_NODOS['node-ctrl-1']}:8181/onos/v1/cluster; echo; "
                            f"docker logs --tail 300 r5-integrador 2>&1 | grep -E 'Integrador|BLOQUEO|LIBERADA|ONOS: se pasa|WARNING|ERROR' | tail -6")
    if rc == 0:
        l = out.splitlines()
        cond(l and l[0] == "200", "el sensor alcanza ONOS (HTTP 200)", f"el sensor NO alcanza ONOS (HTTP {l[0] if l else '?'})")
        ini = [x for x in l[1:] if "Integrador" in x]
        if ini:
            ok("integrador arrancado", ini[-1][:160])
            if dpid_borde:
                cond(dpid_borde in ini[-1], "el integrador bloquea en switch-borde (cores = el borde)",
                     "el integrador no lista el deviceId del borde como core de bloqueo", mal="AVISO")
        else:
            aviso("no hay línea de arranque del integrador en sus últimos logs (¿log largo?)")
        otros = [x for x in l[1:] if "Integrador" not in x]
        if otros:
            info("últimos eventos del integrador", "\n".join(x[:170] for x in otros))
    rc, out = rem.correr(s, "docker exec r5-integrador r5ctl estado 2>&1 | head -25")
    if rc == 0:
        info("r5ctl estado", out)
    rc, out = rem.correr(s, f"timeout 4 tcpdump -l -ni {NIC_ESPEJO} -c 5 ip 2>&1 | grep -c ' IP '")
    if rc is not None:
        n = int(out.strip()) if out.strip().isdigit() else 0
        cond(n > 0, f"tcpdump en {NIC_ESPEJO}: llegan copias del espejo ({n} paquetes en 4 s)",
             f"tcpdump en {NIC_ESPEJO}: 0 paquetes IP en 4 s (normal si no hay tráfico por el borde; si hay, la red descarta las copias)", mal="AVISO")
    return mac


# ================================================================ 5. Policy Mapper
def revisar_mapper():
    seccion("5. Policy Mapper (R1/R2): lo que usa R5 para revocar sesiones")
    c, s = rest("192.168.201.1", "/sessions", puerto=5000, base="", usuario="", clave="")
    if c == 200:
        n = len(s) if isinstance(s, (list, dict)) else "?"
        ok(f"GET {MAPPER}/sessions responde (sesiones activas: {n})")
    else:
        falla(f"el Policy Mapper no responde en {MAPPER}", f"respuesta: {s}")
        return
    c, oa = rest("192.168.201.1", "/openapi.json", puerto=5000, base="", usuario="", clave="")
    rutas = oa.get("paths", {}) if isinstance(oa, dict) else {}
    cond("/sessions/revoke" in rutas, "existe POST /sessions/revoke (I11: R5 revoca la sesión del host interno bloqueado)",
         "no se ve /sessions/revoke en el Policy Mapper")
    info("réplica del Policy Mapper", "solo hay uno (node-ctrl-1); topologia.yaml de R5 apunta únicamente a él: si cae, R5 sigue bloqueando por ONOS pero no revoca sesiones")


# ================================================================ 6. prueba activa
def prueba_activa(rem, vivo, dpid_borde, sensor="node-sec-1"):
    seccion(f"6. Prueba activa: bloquear y desbloquear {IP_PRUEBA} (desde {sensor})")
    if not dpid_borde:
        falla("sin deviceId del borde, no se hace la prueba")
        return
    rc, out = rem.correr(sensor, f"docker exec r5-integrador r5ctl bloquear {IP_PRUEBA} --seg 60 2>&1")
    cond(rc == 0 and "Bloqueada" in (out or ""), f"r5ctl bloqueó {IP_PRUEBA}: {out}", "r5ctl no pudo bloquear la IP de prueba", out)
    time.sleep(2)
    c, f = rest(vivo, f"/flows/{dpid_borde}")
    flujos = [x for x in (f.get("flows", []) if isinstance(f, dict) else [])
              if x.get("appId") == APP_R5 and x.get("priority") == PRIO_DROP
              and any(cr.get("ip", "").startswith(IP_PRUEBA) for cr in x["selector"]["criteria"])]
    cond(len(flujos) == 1 and flujos[0].get("state") == "ADDED", f"ONOS instaló el DROP en switch-borde (estado {flujos[0].get('state') if flujos else '-'})",
         "ONOS no muestra el DROP de prueba en switch-borde")
    rc, out = rem.correr("switch-borde", f"ovs-ofctl -O OpenFlow13 dump-flows {BRIDGE} | grep -c 'priority={PRIO_DROP}.*nw_src={IP_PRUEBA}'")
    cond(rc == 0 and out.strip() == "1", "OVS del borde tiene el DROP (nw_src de la IP de prueba)", "OVS del borde no muestra el DROP de prueba", mal="AVISO")
    rc, out = rem.correr(sensor, f"docker exec r5-integrador r5ctl desbloquear {IP_PRUEBA} 2>&1")
    cond(rc == 0, f"r5ctl desbloqueó {IP_PRUEBA}", "r5ctl no pudo desbloquear: BORRAR A MANO el flujo de prueba", out)
    time.sleep(1)
    c, f = rest(vivo, f"/flows/{dpid_borde}")
    quedan = [x for x in (f.get("flows", []) if isinstance(f, dict) else []) if x.get("appId") == APP_R5 and x.get("priority") == PRIO_DROP
              and any(cr.get("ip", "").startswith(IP_PRUEBA) for cr in x["selector"]["criteria"])]
    cond(not quedan, "el DROP de prueba ya no existe (limpieza completa)", f"quedan {len(quedan)} DROP de prueba en el borde: retirar con r5ctl desbloquear {IP_PRUEBA}")


# ================================================================ principal
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-ssh", action="store_true", help="solo REST de ONOS/Mapper y OVS local del borde (sin contraseña)")
    ap.add_argument("--activo", action="store_true", help="además bloquea y desbloquea una IP de prueba (modifica y limpia ONOS)")
    ap.add_argument("--json", metavar="ARCHIVO", help="guarda los resultados en JSON")
    ap.add_argument("--informe", metavar="ARCHIVO", default=os.path.expanduser(f"~/r5_verificacion_{time.strftime('%Y%m%d_%H%M%S')}.txt"),
                    help="archivo de texto con la salida completa (por defecto en ~)")
    a = ap.parse_args()

    clave = None
    if not a.sin_ssh:
        clave = os.environ.get("R5_PASS") or getpass.getpass(f"Contraseña de {USUARIO} (SSH y sudo de las VM; no se guarda): ")
        if not shutil.which("sshpass"):
            print("AVISO: sshpass no está instalado (sudo apt-get install -y sshpass); se probará ssh con claves.")
    rem = Remoto(clave, not a.sin_ssh)
    print(f"Verificación de R5 · {time.strftime('%Y-%m-%d %H:%M:%S')} · ejecutado en {socket.gethostname()}")

    # deviceId -> nombre
    nombres, dpid_borde = {}, None
    for n in SWITCHES:
        d = dpid_de(rem, n) if (n == "switch-borde" or not a.sin_ssh) else None
        if d:
            nombres[d] = n
            if n == "switch-borde":
                dpid_borde = d

    vivo, _ = revisar_onos(rem, nombres)
    if vivo and not dpid_borde:       # sin sudo local: el borde es el switch que tiene los puertos ens8 y ens9 (hacia los sensores)
        c, d = rest(vivo, "/devices")
        for x in (d.get("devices", []) if isinstance(d, dict) else []):
            c, pu = rest(vivo, f"/devices/{x['id']}/ports")
            nom = {(p.get("annotations") or {}).get("portName") for p in (pu.get("ports", []) if isinstance(pu, dict) else [])}
            if set(PUERTOS_ESPEJO_BORDE.values()) <= nom:
                dpid_borde = x["id"]
                nombres[dpid_borde] = "switch-borde"
    macs = {}
    if vivo:
        # primero los sensores (para tener las MAC), pero se muestran después del borde: se capturan en silencio
        pass
    # MAC de ens5 de cada sensor (para cotejar el espejo)
    if not a.sin_ssh:
        for s in SENSORES:
            rc, out = rem.correr(s, f"cat /sys/class/net/{NIC_ESPEJO}/address", sudo=False)
            macs[s] = out.strip() if rc == 0 else None
    if vivo:
        revisar_borde(rem, vivo, nombres, dpid_borde, macs if macs else None)
    revisar_plano_borde(rem)
    if not a.sin_ssh:
        for s in SENSORES:
            revisar_sensor(rem, s, vivo, dpid_borde)
    revisar_mapper()
    if a.activo:
        if vivo and not a.sin_ssh:
            prueba_activa(rem, vivo, dpid_borde)
        else:
            aviso("--activo necesita ONOS y SSH a node-sec-1: se omite")

    # resumen
    seccion("RESUMEN")
    cuenta = {e: sum(1 for r in RESULTADOS if r[1] == e) for e in CODIGOS}
    print(f"OK={cuenta['OK']}  FALLA={cuenta['FALLA']}  AVISO={cuenta['AVISO']}  INFO={cuenta['INFO']}")
    for sec, e, t in RESULTADOS:
        if e == "FALLA":
            print(f"  [FALLA] ({sec.split(':')[0]}) {t}")
    for sec, e, t in RESULTADOS:
        if e == "AVISO":
            print(f"  [AVISO] ({sec.split(':')[0]}) {t}")
    veredicto = "R5 LISTO" if not cuenta["FALLA"] and not cuenta["AVISO"] else \
        ("R5 CASI LISTO (revisar avisos)" if not cuenta["FALLA"] else "R5 CON FALLAS")
    print(f"\nVeredicto: {veredicto}")
    LOG.append(f"\nVeredicto: {veredicto}")
    try:
        with open(a.informe, "w", encoding="utf8") as f:
            f.write("\n".join(LOG) + "\n")
        print(f"Informe guardado en {a.informe}")
    except OSError as e:
        print(f"No se pudo guardar el informe: {e}")
    if a.json:
        with open(a.json, "w", encoding="utf8") as f:
            json.dump({"fecha": time.strftime("%Y-%m-%dT%H:%M:%S"), "veredicto": veredicto, "resumen": cuenta,
                       "resultados": [{"seccion": s, "estado": e, "texto": t} for s, e, t in RESULTADOS]}, f, ensure_ascii=False, indent=2)
    return 1 if cuenta["FALLA"] else 0


if __name__ == "__main__":
    sys.exit(main())
