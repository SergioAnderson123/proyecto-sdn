"""Pruebas de R5/codigo/topologia.py: lectura y validación de topologia.yaml (slice de 14 VM)."""
import copy
import os
import sys

import pytest
import yaml

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(RAIZ, "codigo"))
import topologia  # noqa: E402

EJEMPLO = os.path.join(RAIZ, "deploy", "topologia.example.yaml")


def base():
    with open(EJEMPLO, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_el_ejemplo_del_repo_es_valido():
    t = topologia.cargar(EJEMPLO)
    assert [c["nombre"] for c in t["cores"]] == ["sw-core-1", "sw-core-2"]
    assert t["sensores"] == ["nodo-sec-1", "nodo-sec-2"]
    assert len(t["onos_urls"]) == 3 and len(t["mapper_urls"]) == 2          # clúster ONOS de 3 nodos y 2 Policy Mapper
    assert t["home"] == ["10.0.0.0/24"]


def test_cada_core_espeja_a_los_dos_sensores_y_no_mezcla_entradas_con_espejo():
    for c in topologia.cargar(EJEMPLO)["cores"]:
        assert set(c["espejo"]) == {"nodo-sec-1", "nodo-sec-2"}
        assert not {e["puerto"] for e in c["espejo"].values()} & set(c["entradas"])


def test_las_urls_pierden_la_barra_final():
    d = base()
    d["onos"]["urls"] = ["http://a:8181/onos/v1/"]
    assert topologia.validar(d)["onos_urls"] == ["http://a:8181/onos/v1"]


def test_la_mac_se_normaliza_a_minusculas():
    d = base()
    d["cores"][0]["espejo"]["nodo-sec-1"]["mac"] = "FA:16:3E:AA:BB:CC"
    assert topologia.validar(d)["cores"][0]["espejo"]["nodo-sec-1"]["mac"] == "fa:16:3e:aa:bb:cc"


def test_la_mac_es_opcional():
    d = base()
    del d["cores"][0]["espejo"]["nodo-sec-1"]["mac"]
    assert topologia.validar(d)["cores"][0]["espejo"]["nodo-sec-1"]["mac"] is None


@pytest.mark.parametrize("cambio,texto", [
    (lambda d: d["cores"][0].update(device="of:1"), "deviceId"),
    (lambda d: d["cores"][0].update(device=d["cores"][1]["device"]), "repetido"),
    (lambda d: d["cores"][0]["espejo"]["nodo-sec-1"].update(mac="zz:00"), "MAC"),
    (lambda d: d["cores"][0]["espejo"].pop("nodo-sec-2"), "nodo-sec-2"),
    (lambda d: d["cores"][0]["espejo"].update({"nodo-sec-9": {"puerto": "ens20"}}), "desconocidos"),
    (lambda d: d["cores"][0].update(entradas=["ens5", "ens11"]), "entradas"),          # ens11 es el puerto de espejo
    (lambda d: d["cores"][0].update(entradas=[]), "no puede estar vacío"),
    (lambda d: d["redes"].update(home=["10.0.0.0/99"]), "red"),
    (lambda d: d["redes"].update(home=[]), "no puede estar vacío"),
    (lambda d: d["onos"].update(urls=[]), "no puede estar vacío"),
    (lambda d: d.update(cores=[]), "al menos un core"),
    (lambda d: d.update(sensores=[]), "no puede estar vacío"),
])
def test_rechaza_topologias_invalidas(cambio, texto):
    d = copy.deepcopy(base())
    cambio(d)
    with pytest.raises(topologia.TopologiaInvalida, match=texto):
        topologia.validar(d)


def test_un_archivo_que_no_es_un_mapa_se_rechaza():
    with pytest.raises(topologia.TopologiaInvalida):
        topologia.validar(["no", "es", "un", "mapa"])


def test_nombres_de_switches_incluye_cores_y_accesos():
    n = topologia.nombres_switches(topologia.cargar(EJEMPLO))
    assert n["of:00000000000000a1"] == "sw-core-1" and n["of:00000000000000b2"] == "sw-acc2"


def test_aplicar_entorno_rellena_lo_que_falta_y_no_pisa_lo_definido(monkeypatch):
    for v in ("ONOS_URL", "MAPPER_URL", "HOME_NET", "LISTA_BLANCA", "CORE_DEVICES"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME_NET", "192.0.2.0/24")                              # el entorno manda sobre el archivo
    t = topologia.aplicar_entorno(EJEMPLO)
    assert t is not None
    assert os.environ["HOME_NET"] == "192.0.2.0/24"
    assert os.environ["CORE_DEVICES"] == "of:00000000000000a1,of:00000000000000a2"
    assert os.environ["ONOS_URL"].count(",") == 2 and os.environ["MAPPER_URL"].count(",") == 1
    assert "10.0.0.40/32" in os.environ["LISTA_BLANCA"]
    for v in ("ONOS_URL", "MAPPER_URL", "LISTA_BLANCA", "CORE_DEVICES"):       # aplicar_entorno escribe en os.environ: se limpia
        monkeypatch.delenv(v, raising=False)


def test_aplicar_entorno_sin_topologia_no_hace_nada(monkeypatch):
    monkeypatch.delenv("TOPOLOGIA", raising=False)
    assert topologia.aplicar_entorno() is None
