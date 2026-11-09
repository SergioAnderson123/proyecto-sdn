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
    "hosts": [{"nombre": "h1", "puerto": 7001, "usuario": "profesor01"},
              {"nombre": "h2", "puerto": 7002, "usuario": "support01"}],
}


def test_cada_usuario_tiene_su_suplicante_y_su_certificado():
    for usuario, d in sd.USUARIOS.items():
        assert (SUPP / d["conf"]).is_file(), d["conf"]
        assert (SUPP / "certs" / f"{usuario}@example.org.pem").is_file(), usuario
        assert f'identity="{usuario}@example.org"' in (SUPP / d["conf"]).read_text()
        assert f'private_key_passwd="{d["clave"]}"' in (SUPP / d["conf"]).read_text()


def test_los_usuarios_existen_en_ldap():
    ldif = (AQUI.parent / "shared" / "ldap" / "ldif" / "custom" / "estructura.ldif").read_text()
    for usuario in sd.USUARIOS:
        assert f"dn: uid={usuario},ou=users" in ldif


def test_inventario_de_switches():
    inv = sd.inventario_switches(CFG)
    assert "sw2 ansible_host=1.2.3.4 ansible_port=6002 bridge=sw2" in inv
    assert "controller_ip=9.9.9.9" in inv and "controller_port=6653" in inv
    assert "# " not in inv   # los comentarios al final de una variable pasan a formar parte del valor en un INI de Ansible


def test_inventario_de_hosts():
    inv = sd.inventario_hosts(CFG)
    assert "h1 ansible_host=1.2.3.4 ansible_port=7001 iface=ens9 conf=wpa_profe_01.conf cert=profesor01@example.org.pem" in inv
    assert "h2 ansible_host=1.2.3.4 ansible_port=7002 iface=ens9 conf=wpa_support_01.conf cert=support01@example.org.pem" in inv
    assert "controller_ip=9.9.9.9" in inv    # el agente de cada host consulta al Policy Mapper del Controller
    assert "#" not in inv


def test_tuneles_a_la_interfaz_web_de_cada_host():
    assert sd.comandos_tunel(CFG) == [
        ("h1", 8081, "ssh -L 8081:127.0.0.1:8080 -p 7001 ubuntu@1.2.3.4"),
        ("h2", 8082, "ssh -L 8082:127.0.0.1:8080 -p 7002 ubuntu@1.2.3.4")]


def test_el_playbook_de_hosts_instala_el_agente_y_no_deja_dos_suplicantes():
    import yaml
    play = yaml.safe_load((AQUI / "hosts" / "hosts.yml").read_text())[0]
    nombres = " ".join(t["name"] for t in play["tasks"])
    assert "agente" in nombres and "Quitar el servicio r1-supplicant" in nombres
    assert {u["uid"] for u in play["vars"]["usuarios_todos"]} == set(sd.USUARIOS)
    assert (AQUI.parent / "agente_802_1x" / "agente.py").is_file()


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
        "", "1",       # h1: puerto por defecto, usuario 1 (profesor01)
        "5822", "alumno03",   # h2: puerto, usuario por nombre
        "", "6",       # h3: puerto por defecto, usuario 6 (support01)
        "",            # guardar: si
    ])
    cfg = sd.config()
    assert cfg["controller_ip"] == "192.168.0.10" and cfg["iface"] == "ens5"
    assert [s["puerto"] for s in cfg["switches"]] == [5801, 5802]
    assert [(h["nombre"], h["puerto"], h["usuario"]) for h in cfg["hosts"]] == [
        ("h1", 5811, "profesor01"), ("h2", 5822, "alumno03"), ("h3", 5813, "support01")]
    assert json.loads((tmp_path / "slice.json").read_text()) == cfg


def test_elegir_usuario_rechaza_lo_que_no_es_de_la_lista(monkeypatch, capsys):
    _respuestas(monkeypatch, ["nadie", "99", "alumno02"])
    assert sd.elegir_usuario("profesor01") == "alumno02"
    assert capsys.readouterr().out.count("No es un usuario de la lista") == 2


def test_advierte_si_un_usuario_se_repite(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sd, "CONFIG", tmp_path / "slice.json")
    _respuestas(monkeypatch, ["1.1.1.1", "2.2.2.2", "1", "", "ens4", "2", "", "alumno01", "", "alumno01", "n"])
    sd.config()
    assert "alumno01 esta asignado a mas de un host" in capsys.readouterr().out
    assert not (tmp_path / "slice.json").exists()
