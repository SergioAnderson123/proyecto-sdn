"""Pruebas de slice.py: inventarios, tabla de usuarios y el asistente de configuracion con respuestas simuladas."""
import importlib.util
import json
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("slice_deploy", AQUI / "slice.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

SUPP = AQUI.parent / "shared" / "mininet" / "supplicants"
CFG = {
    "ip_publica": "1.2.3.4", "controller_ip": "9.9.9.9", "iface": "ens9",
    "switches": [{"nombre": "sw1", "puerto": 6001}, {"nombre": "sw2", "puerto": 6002}],
    "hosts": [{"nombre": "h1", "puerto": 7001, "usuario": "alumno01"},
              {"nombre": "h2", "puerto": 7002, "usuario": "alumno03"},
              {"nombre": "h3", "puerto": 7003, "usuario": "portal"},
              {"nombre": "h4", "puerto": 7004, "usuario": "invitado"},
              {"nombre": "h5", "puerto": 7005, "usuario": "iot"}],
}


def test_cada_usuario_tiene_su_suplicante_y_su_certificado():
    for usuario, d in sd.USUARIOS.items():
        assert (SUPP / d["conf"]).is_file(), d["conf"]
        assert (SUPP / "certs" / f"{usuario}@example.org.pem").is_file(), usuario
        assert f'identity="{usuario}@example.org"' in (SUPP / d["conf"]).read_text()
        assert f'private_key_passwd="{d["clave"]}"' in (SUPP / d["conf"]).read_text()


def test_los_usuarios_existen_en_ldap():
    ldif = (AQUI.parent / "shared" / "ldap" / "ldif" / "custom" / "estructura.ldif").read_text()
    for usuario in sd.con_certificado():
        assert f"dn: uid={usuario},ou=users" in ldif


def test_por_defecto_solo_hay_identidades_de_equipo_con_rol_alumno():
    """Los profesores no llevan certificado en las PC: suben el equipo desde el portal con su cuenta personal."""
    assert set(sd.USUARIOS) == {"alumno01", "alumno02", "alumno03"} and all(d["rol"] == "Alumno" for d in sd.USUARIOS.values())
    assert set(sd.opciones()) == set(sd.USUARIOS) | {"portal", "invitado", "iot"}
    assert not set(sd.USUARIOS_PRUEBA) & set(sd.opciones())            # solo se asignan escribiendo el nombre
    for usuario, d in sd.USUARIOS_PRUEBA.items():                       # y siguen teniendo certificado y suplicante
        assert (SUPP / d["conf"]).is_file() and (SUPP / "certs" / f"{usuario}@example.org.pem").is_file()


def test_inventario_de_switches():
    inv = sd.inventario_switches(CFG)
    assert "sw2 ansible_host=1.2.3.4 ansible_port=6002 bridge=sw2" in inv
    assert "controller_ip=9.9.9.9" in inv and "controller_port=6653" in inv
    assert "# " not in inv   # los comentarios al final de una variable pasan a formar parte del valor en un INI de Ansible


def test_inventario_de_hosts():
    inv = sd.inventario_hosts(CFG)
    assert "h1 ansible_host=1.2.3.4 ansible_port=7001 iface=ens9 conf=wpa_alumno_01.conf cert=alumno01@example.org.pem" in inv
    assert "h2 ansible_host=1.2.3.4 ansible_port=7002 iface=ens9 conf=wpa_alumno_03.conf cert=alumno03@example.org.pem" in inv
    assert inv.count("ip_datos=") == 1       # solo el portal tiene IP fija: el resto la recibe por DHCP
    assert "controller_ip=9.9.9.9" in inv    # el agente de cada host consulta al Policy Mapper del Controller
    assert "#" not in inv


def test_el_inventario_separa_hosts_con_802_1x_portal_y_sin_802_1x():
    grupos, actual = {}, None
    for linea in sd.inventario_hosts(CFG).splitlines():
        if linea.startswith("["):
            actual = linea.strip("[]")
            grupos[actual] = []
        elif linea and actual:
            grupos[actual].append(linea.split()[0] if actual in ("hosts", "portal", "sin_802_1x") else linea)
    assert grupos["hosts"] == ["h1", "h2"]            # los dos con agente y certificado
    assert grupos["portal"] == ["h3"] and grupos["sin_802_1x"] == ["h4", "h5"]
    inv = sd.inventario_hosts(CFG)
    assert "h3 ansible_host=1.2.3.4 ansible_port=7003 iface=ens9 ip_datos=10.0.0.60" in inv     # el portal toma la IP de policy.yaml
    assert "h4 ansible_host=1.2.3.4 ansible_port=7004 iface=ens9" in inv and "ip_datos" not in next(l for l in inv.splitlines() if l.startswith("h4 "))
    assert "conf=" not in next(l for l in inv.splitlines() if l.startswith("h4 "))           # sin certificado ni agente


def test_la_ip_del_portal_es_la_de_policy_yaml():
    import yaml
    pol = yaml.safe_load((AQUI.parent.parent / "R2" / "policy" / "policy.yaml").read_text(encoding="utf-8"))
    assert pol["portal"]["ip"] == sd.PORTAL_IP and int(pol["portal"]["puerto"]) == sd.PORTAL_PUERTO


def test_tuneles_a_la_interfaz_web_solo_de_los_hosts_con_802_1x():
    assert sd.comandos_tunel(CFG) == [
        ("h1", 8081, "ssh -L 8081:127.0.0.1:8080 -p 7001 ubuntu@1.2.3.4"),
        ("h2", 8082, "ssh -L 8082:127.0.0.1:8080 -p 7002 ubuntu@1.2.3.4")]


def test_el_playbook_de_hosts_instala_el_agente_y_no_deja_dos_suplicantes():
    import yaml
    play = yaml.safe_load((AQUI / "hosts" / "hosts.yml").read_text())[0]
    nombres = " ".join(t["name"] for t in play["tasks"])
    assert "agente" in nombres and "Quitar el servicio r1-supplicant" in nombres
    assert {u["uid"] for u in play["vars"]["usuarios_todos"]} == set(sd.USUARIOS)      # solo las identidades de equipo
    assert "Quitar los certificados que no son de este host" in nombres                  # y limpia los de profesor de versiones anteriores
    assert (AQUI.parent / "agente_802_1x" / "agente.py").is_file()


def test_el_playbook_del_portal_instala_el_servidor_y_la_ip_de_datos():
    import yaml
    plays = yaml.safe_load((AQUI / "hosts" / "portal.yml").read_text())
    assert [p["hosts"] for p in plays] == ["portal", "portal"]          # IP fija y servidor: solo el host del portal
    nombres = " ".join(t["name"] for p in plays for t in p["tasks"])
    assert "IP de datos" in nombres and "Servicio r1-portal" in nombres
    assert (AQUI.parent.parent / "R2" / "portal_cautivo" / "server.py").is_file()
    assert (AQUI.parent.parent / "R2" / "portal_cautivo" / "static" / "tabler.min.css").is_file()


def test_el_token_del_agente_se_genera_una_vez_y_se_conserva(monkeypatch, tmp_path):
    (tmp_path / ".env.example").write_text("CONTROLLER_IP=1.2.3.4\n")
    monkeypatch.setattr(sd, "DEPLOY", tmp_path)
    primero, segundo = sd.token_agente(), sd.token_agente()
    assert primero == segundo and len(primero) == 32
    env = (tmp_path / ".env").read_text()
    assert f"AGENTE_TOKEN={primero}" in env and "CONTROLLER_IP=1.2.3.4" in env   # no pisa lo que ya habia


def test_el_inventario_de_hosts_lleva_la_clave_solo_si_se_da():
    assert "agente_token=abc123" in sd.inventario_hosts(CFG, "abc123")
    assert "agente_token" not in sd.inventario_hosts(CFG)
    inv = sd.inventario_hosts(CFG, "abc123", "def456")
    assert "portal_token=def456" in inv and "portal_ip=10.0.0.60" in inv and "portal_puerto=8080" in inv
    assert "portal_token" not in sd.inventario_hosts(CFG, "abc123")


def test_cada_clave_se_genera_una_vez_y_son_distintas(monkeypatch, tmp_path):
    (tmp_path / ".env.example").write_text("CONTROLLER_IP=1.2.3.4\n")
    monkeypatch.setattr(sd, "DEPLOY", tmp_path)
    agente, portal = sd.token_agente(), sd.token_portal()
    assert agente != portal and sd.token_portal() == portal and sd.token_agente() == agente
    env = (tmp_path / ".env").read_text()
    assert f"AGENTE_TOKEN={agente}" in env and f"PORTAL_TOKEN={portal}" in env


# ---------------------------------------------------------------- portal: ubicacion y puertos de confianza

HOSTS_ONOS = [
    {"mac": "FA:16:3E:11:11:11", "ipAddresses": ["10.0.0.11"], "locations": [{"elementId": "of:00000000000000a1", "port": "2"}]},
    {"mac": "FA:16:3E:60:60:60", "ipAddresses": ["10.0.0.60"], "locations": [{"elementId": "of:00000000000000a2", "port": "3"}]},
]
ENLACES = [
    {"src": {"device": "of:00000000000000a1", "port": "5"}, "dst": {"device": "of:00000000000000a2", "port": "4"}},
    {"src": {"device": "of:00000000000000a2", "port": "4"}, "dst": {"device": "of:00000000000000a1", "port": "5"}},
    {"src": {"device": "of:00000000000000a2", "port": "6"}, "dst": {"device": "of:00000000000000a3", "port": "1"}},
]


def test_ubica_el_portal_por_su_ip():
    assert sd.ubicar_portal(HOSTS_ONOS) == ("fa:16:3e:60:60:60", "of:00000000000000a2", "3")
    assert sd.ubicar_portal(HOSTS_ONOS[:1]) is None and sd.ubicar_portal([]) is None


def test_los_puertos_de_confianza_son_las_troncales_y_el_puerto_del_portal():
    c = sd.puertos_de_confianza(ENLACES, sd.ubicar_portal(HOSTS_ONOS))
    assert c == {"of:00000000000000a1": [5], "of:00000000000000a2": [3, 4, 6], "of:00000000000000a3": [1]}


def test_la_politica_local_se_puede_cargar_y_se_mezcla_con_policy_yaml(tmp_path):
    import importlib.util as iu
    import shutil
    import sys
    portal = sd.ubicar_portal(HOSTS_ONOS)
    texto = sd.politica_local(sd.puertos_de_confianza(ENLACES, portal), portal[0], ["of:00000000000000a1", "of:00000000000000a2"])
    shutil.copy(AQUI.parent.parent / "R2" / "policy" / "policy.yaml", tmp_path / "policy.yaml")
    (tmp_path / "policy.local.yaml").write_text(texto)
    sys.path.insert(0, str(AQUI.parent.parent / "R2" / "policy"))
    sys.path.insert(0, str(AQUI.parent / "shared" / "policy_mapper"))
    pt = iu.module_from_spec(iu.spec_from_file_location("pt_local", AQUI.parent.parent / "R2" / "policy" / "policy_tables.py"))
    pt.__spec__.loader.exec_module(pt)
    p = pt.cargar_politica(str(tmp_path / "policy.yaml"))
    assert p["trusted"]["of:00000000000000a2"] == [3, 4, 6] and p["trusted"]["of:0000000000000002"] == "all"   # suma, no reemplaza
    assert p["portal"]["mac"] == "fa:16:3e:60:60:60" and p["portal"]["desvios"] == [] and p["portal"]["ip"] == "10.0.0.60"
    assert p["portal"]["switches"] == ["of:00000000000000a1", "of:00000000000000a2"]


def _respuestas(monkeypatch, lista):
    it = iter(lista)
    monkeypatch.setattr("builtins.input", lambda _="": next(it))


def test_asistente_de_configuracion(monkeypatch, tmp_path):
    monkeypatch.setattr(sd, "CONFIG", tmp_path / "slice.json")
    monkeypatch.setattr(sd, "detectar_ip", lambda: "192.168.0.10")
    _respuestas(monkeypatch, [
        "",            # IP SSH: por defecto
        "",            # IP del controller: la detectada
        "2",           # 2 switches
        "", "",        # puertos por defecto (5801, 5802)
        "ens5",        # interfaz
        "3",           # 3 hosts
        "", "1",       # h1: puerto por defecto, usuario 1 (alumno01)
        "5822", "alumno03",   # h2: puerto, usuario por nombre
        "", "6",       # h3: puerto por defecto, tipo 6 (iot)
        "",            # guardar: si
    ])
    cfg = sd.config()
    assert cfg["controller_ip"] == "192.168.0.10" and cfg["iface"] == "ens5"
    assert [s["puerto"] for s in cfg["switches"]] == [5801, 5802]
    assert [(h["nombre"], h["puerto"], h["usuario"]) for h in cfg["hosts"]] == [
        ("h1", 5811, "alumno01"), ("h2", 5822, "alumno03"), ("h3", 5813, "iot")]
    assert json.loads((tmp_path / "slice.json").read_text()) == cfg


def test_por_defecto_los_hosts_son_alumnos_y_un_portal(monkeypatch, tmp_path):
    monkeypatch.setattr(sd, "CONFIG", tmp_path / "slice.json")
    _respuestas(monkeypatch, ["1.1.1.1", "2.2.2.2", "1", "", "ens4", "6"] + [""] * 12 + ["n"])
    cfg = sd.config()
    assert [h["usuario"] for h in cfg["hosts"]] == ["alumno01", "alumno02", "alumno03", "portal", "invitado", "iot"]


def test_una_cuenta_de_prueba_solo_se_asigna_escribiendo_su_nombre(monkeypatch, tmp_path):
    monkeypatch.setattr(sd, "CONFIG", tmp_path / "slice.json")
    _respuestas(monkeypatch, ["1.1.1.1", "2.2.2.2", "1", "", "ens4", "1", "", "support01", "n"])
    assert sd.config()["hosts"][0]["usuario"] == "support01"
    assert sd.usa_802_1x({"usuario": "support01"}) and not sd.usa_802_1x({"usuario": "portal"})


def test_elegir_usuario_rechaza_lo_que_no_es_de_la_lista(monkeypatch, capsys):
    _respuestas(monkeypatch, ["nadie", "99", "alumno02"])
    assert sd.elegir_usuario("alumno01") == "alumno02"
    assert capsys.readouterr().out.count("No es un usuario de la lista") == 2


def test_advierte_si_un_usuario_se_repite(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sd, "CONFIG", tmp_path / "slice.json")
    _respuestas(monkeypatch, ["1.1.1.1", "2.2.2.2", "1", "", "ens4", "2", "", "alumno01", "", "alumno01", "n"])
    sd.config()
    assert "alumno01 esta asignado a mas de un host" in capsys.readouterr().out
    assert not (tmp_path / "slice.json").exists()


def test_advierte_si_hay_dos_portales_pero_no_si_se_repite_iot(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sd, "CONFIG", tmp_path / "slice.json")
    _respuestas(monkeypatch, ["1.1.1.1", "2.2.2.2", "1", "", "ens4", "2", "", "portal", "", "portal", "n"])
    sd.config()
    assert "portal esta asignado a mas de un host" in capsys.readouterr().out
    _respuestas(monkeypatch, ["1.1.1.1", "2.2.2.2", "1", "", "ens4", "2", "", "iot", "", "iot", "n"])
    sd.config()
    assert "asignado a mas de un host" not in capsys.readouterr().out


# ---------------------------------------------------------------- DHCP

def test_el_playbook_de_dhcp_toca_los_hosts_con_y_sin_802_1x_pero_no_el_portal():
    import yaml
    [play] = yaml.safe_load((AQUI / "hosts" / "dhcp.yml").read_text())
    assert play["hosts"] == "hosts:sin_802_1x"
    nombres = " ".join(t["name"] for t in play["tasks"])
    assert "Script de dhclient" in nombres and "Servicio r1-dhcp" in nombres


def test_el_script_de_dhclient_solo_pone_la_direccion(tmp_path):
    """Lo ejecuta con un `ip` falso: pone la direccion con el prefijo bien calculado y nunca instala rutas ni DNS."""
    import os
    import shutil
    import subprocess
    import yaml
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sin sh")
    play = yaml.safe_load((AQUI / "hosts" / "dhcp.yml").read_text())[0]
    script = next(t for t in play["tasks"] if t["name"].startswith("Script de dhclient"))["ansible.builtin.copy"]["content"]
    (tmp_path / "script.sh").write_text(script)
    (tmp_path / "ip").write_text('#!/bin/sh\necho "$@" >> "$TRAZA"\n')
    os.chmod(tmp_path / "ip", 0o755)

    def correr(**env):
        traza = tmp_path / "traza"
        traza.write_text("")
        r = subprocess.run([sh, str(tmp_path / "script.sh")], env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "TRAZA": str(traza),
                           "interface": "ens4", **env}, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return traza.read_text().splitlines()

    assert correr(reason="BOUND", new_ip_address="10.0.0.101", new_subnet_mask="255.255.255.0") == ["addr replace 10.0.0.101/24 dev ens4"]
    assert correr(reason="BOUND", new_ip_address="10.1.2.3", new_subnet_mask="255.255.0.0") == ["addr replace 10.1.2.3/16 dev ens4"]
    assert correr(reason="RENEW", old_ip_address="10.0.0.101", new_ip_address="10.0.0.150", new_subnet_mask="255.255.255.0") == [
        "addr del 10.0.0.101/24 dev ens4", "addr replace 10.0.0.150/24 dev ens4"]
    assert correr(reason="EXPIRE") == ["addr flush dev ens4"]
    assert correr(reason="PREINIT") == [] and correr(reason="TIMEOUT") == []
    assert "route" not in script and "resolv" not in script           # una ruta por defecto en la interfaz de datos cortaria el SSH


def test_los_parametros_dhcp_de_init_onos_estan_documentados_en_env_example():
    import re
    init = (AQUI / "controller" / "init-onos.sh").read_text()
    ejemplo = (AQUI / ".env.example").read_text()
    variables = set(re.findall(r"\$\{(DHCP_\w+):-", init))
    assert len(variables) >= 10
    for v in variables:
        assert f"#{v}=" in ejemplo, v
    for clave in ("ip", "mac", "subnet", "broadcast", "router", "domain", "startip", "endip", "lease", "renew", "rebind", "delay"):
        assert '\\"' + clave + '\\":' in init, clave         # claves de DhcpConfig de ONOS 2.7


def test_el_rango_dhcp_por_defecto_no_pisa_ninguna_ip_fija_ni_el_servidor():
    import ipaddress
    import re
    import yaml
    init = (AQUI / "controller" / "init-onos.sh").read_text()
    d = {m[0]: m[1] for m in re.findall(r'(DHCP_\w+)="\$\{DHCP_\w+:-([^}]+)\}"', init)}
    ini, fin = ipaddress.ip_address(d["DHCP_INICIO"]), ipaddress.ip_address(d["DHCP_FIN"])
    assert ini < fin and int(fin) - int(ini) + 1 >= 50
    pol = yaml.safe_load((AQUI.parent.parent / "R2" / "policy" / "policy.yaml").read_text(encoding="utf-8"))
    fijas = {pol["portal"]["ip"], d["DHCP_SERVIDOR_IP"], d["DHCP_ROUTER"]} | {s["ip"] for s in pol["services"].values()}
    for ip in fijas:
        assert not ini <= ipaddress.ip_address(ip) <= fin, ip
    red = ipaddress.ip_network(f"{d['DHCP_SERVIDOR_IP']}/{d['DHCP_MASCARA']}", strict=False)
    assert ini in red and fin in red and ipaddress.ip_address(d["DHCP_BROADCAST"]) == red.broadcast_address
    assert int(d["DHCP_RENEW"]) < int(d["DHCP_REBIND"]) < int(d["DHCP_LEASE"])
    assert ipaddress.ip_address(pol["portal"]["ip"]) in red                  # el portal esta en la misma subred que los clientes
