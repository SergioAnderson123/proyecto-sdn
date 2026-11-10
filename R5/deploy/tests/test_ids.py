"""Pruebas del despliegue de los sensores IDS (R5/deploy/ids): captura en varias NIC, compose y coherencia con topologia.example.yaml.

Ejecutar desde la raíz del repositorio:  python -m pytest R5/deploy/tests -q
"""
import os
import shutil
import subprocess
import sys

import pytest
import yaml

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
IDS = os.path.join(RAIZ, "R5", "deploy", "ids")
sys.path.insert(0, os.path.join(IDS, "suricata"))
sys.path.insert(0, os.path.join(RAIZ, "R5", "codigo"))
import af_packet  # noqa: E402
import topologia  # noqa: E402

# fragmento representativo del suricata.yaml que trae la imagen: la sección af-packet termina en la siguiente clave de primer nivel
SURICATA_YAML = """%YAML 1.1
---
vars:
  address-groups:
    HOME_NET: "[192.168.0.0/16]"

# Linux high speed capture support
af-packet:
  - interface: eth0
    # Number of receive threads. "auto" uses the number of cores
    #threads: auto
    cluster-id: 99
    cluster-type: cluster_flow
    defrag: yes
  - interface: eth1
    #threads: auto
  # Put default values here. These will be used for an interface that is not
  # in the list above.
  - interface: default

# Cross platform libpcap capture support
pcap:
  - interface: eth0

default-rule-path: /var/lib/suricata/rules
"""


def test_af_packet_reemplaza_la_seccion_por_las_interfaces_pedidas():
    nuevo = af_packet.reemplazar(SURICATA_YAML, ["ens4", "ens5"])
    datos = yaml.safe_load(nuevo)
    assert [i["interface"] for i in datos["af-packet"]] == ["ens4", "ens5"]
    assert len({i["cluster-id"] for i in datos["af-packet"]}) == 2                    # un cluster-id distinto por interfaz
    assert all(i["cluster-type"] == "cluster_flow" and i["defrag"] is True for i in datos["af-packet"])


def test_af_packet_no_toca_el_resto_del_archivo():
    nuevo = af_packet.reemplazar(SURICATA_YAML, ["ens4"])
    datos = yaml.safe_load(nuevo)
    assert datos["pcap"] == [{"interface": "eth0"}]
    assert datos["default-rule-path"] == "/var/lib/suricata/rules"
    assert datos["vars"]["address-groups"]["HOME_NET"] == "[192.168.0.0/16]"
    assert nuevo.startswith("%YAML 1.1\n---")                                          # Suricata exige la cabecera


def test_af_packet_es_idempotente():
    una = af_packet.reemplazar(SURICATA_YAML, ["ens4", "ens5"])
    assert af_packet.reemplazar(una, ["ens4", "ens5"]) == una


def test_af_packet_cambia_las_interfaces_en_un_segundo_arranque():
    una = af_packet.reemplazar(SURICATA_YAML, ["ens4", "ens5"])
    assert [i["interface"] for i in yaml.safe_load(af_packet.reemplazar(una, ["ens6"]))["af-packet"]] == ["ens6"]


def test_af_packet_agrega_la_seccion_si_no_existe():
    nuevo = af_packet.reemplazar("%YAML 1.1\n---\nvars: {}\n", ["ens4"])
    assert yaml.safe_load(nuevo)["af-packet"][0]["interface"] == "ens4"


@pytest.mark.parametrize("texto", ["", " , ", "ens4,ens4"])
def test_lista_de_interfaces_invalida(texto):
    with pytest.raises(ValueError):
        af_packet.lista_interfaces(texto)


def test_lista_de_interfaces_ignora_espacios():
    assert af_packet.lista_interfaces(" ens4 , ens5 ") == ["ens4", "ens5"]


# ---------- compose ----------
@pytest.fixture(scope="module")
def compose():
    with open(os.path.join(IDS, "compose.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_el_compose_define_los_tres_servicios(compose):
    assert set(compose["services"]) == {"suricata", "integrador", "espejo"}


def test_el_integrador_recibe_el_nombre_del_sensor_y_la_topologia(compose):
    env = compose["services"]["integrador"]["environment"]
    assert env["TOPOLOGIA"] == "/opt/r5/topologia.yaml" and "SENSOR" in env
    assert "CORE_DEVICE" not in env and "HOME_NET" not in env      # esos valores salen de topologia.yaml, una sola fuente


def test_suricata_lee_las_nic_de_espejo_y_la_topologia(compose):
    s = compose["services"]["suricata"]
    assert "IDS_IFACES" in s["environment"] and s["network_mode"] == "host"
    montajes = " ".join(s["volumes"])
    assert "af_packet.py" in montajes and "topologia.yaml" in montajes and "topologia.py" in montajes


def test_el_instalador_del_espejo_solo_se_corre_a_peticion(compose):
    assert compose["services"]["espejo"]["profiles"] == ["setup"]


def test_la_imagen_incluye_topologia_py_y_pyyaml():
    with open(os.path.join(IDS, "Containerfile"), encoding="utf-8") as f:
        texto = f.read()
    assert "pyyaml" in texto and "R5/codigo/topologia.py" in texto and "R5/lab/espejo.py" in texto


# ---------- coherencia con topologia.example.yaml y .env.example ----------
def leer_env(ruta):
    out = {}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.split("#", 1)[0].strip()
            if "=" in linea:
                k, v = linea.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def test_el_env_de_ejemplo_es_coherente_con_la_topologia_de_ejemplo():
    env = leer_env(os.path.join(RAIZ, "R5", "deploy", ".env.example"))
    topo = topologia.cargar(os.path.join(RAIZ, "R5", "deploy", "topologia.example.yaml"))
    assert env["SENSOR"] in topo["sensores"]
    assert len(env["IDS_IFACES"].split(",")) == len(topo["cores"])        # una NIC de espejo por core
    assert env["P_ESPEJO"] == "41000" and env["P_R5"] == "42000"


def test_topologia_yaml_real_no_se_versiona():
    with open(os.path.join(RAIZ, "R5", "deploy", ".gitignore"), encoding="utf-8") as f:
        assert "topologia.yaml" in f.read().split()


# ---------- scripts de shell ----------
@pytest.mark.skipif(shutil.which("bash") is None, reason="sin bash")
@pytest.mark.parametrize("ruta", [("up.sh",), ("macs.sh",), ("suricata", "entrypoint.sh")])
def test_los_scripts_de_shell_tienen_sintaxis_valida(ruta):
    r = subprocess.run(["bash", "-n", os.path.join(IDS, *ruta)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("ruta", [("up.sh",), ("macs.sh",), ("suricata", "entrypoint.sh")])
def test_los_scripts_de_shell_usan_fin_de_linea_lf(ruta):
    """Se ejecutan en Linux: un CRLF rompe el shebang (`bash\\r: no such file`)."""
    with open(os.path.join(IDS, *ruta), "rb") as f:
        assert b"\r\n" not in f.read()
