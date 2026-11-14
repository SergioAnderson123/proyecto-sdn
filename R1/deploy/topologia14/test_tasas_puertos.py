"""Pruebas del medidor de paquetes por puerto (files/tasas_puertos.py) con salidas de ejemplo de `ovs-ofctl dump-ports`."""
import importlib.util
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tasas_puertos", os.path.join(AQUI, "files", "tasas_puertos.py"))
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)

ANTES = """OFPST_PORT reply (OF1.3) (xid=0x2): 4 ports
  port  LOCAL: rx pkts=10, bytes=840, drop=0, errs=0, frame=0, over=0, crc=0
           tx pkts=5, bytes=400, drop=0, errs=0, coll=0
  port  ens4: rx pkts=1000, bytes=84000, drop=0, errs=0, frame=0, over=0, crc=0
           tx pkts=2000, bytes=168000, drop=0, errs=0, coll=0
  port  ens5: rx pkts=500, bytes=42000, drop=0, errs=0, frame=0, over=0, crc=0
           tx pkts=?, bytes=?, drop=?, errs=?, coll=?
  port  ens6: rx pkts=0, bytes=0, drop=0, errs=0, frame=0, over=0, crc=0
           tx pkts=0, bytes=0, drop=0, errs=0, coll=0
"""


def _despues(rx4, tx4, rx5, tx5):
    return ANTES.replace("rx pkts=1000", f"rx pkts={rx4}").replace("tx pkts=2000", f"tx pkts={tx4}").replace(
        "rx pkts=500", f"rx pkts={rx5}").replace("tx pkts=?", f"tx pkts={tx5}")


def test_lee_rx_y_tx_de_cada_puerto_y_tolera_los_signos_de_interrogacion():
    p = tp.leer_puertos(ANTES)
    assert p["ens4"] == (1000, 2000) and p["ens5"] == (500, 0) and p["LOCAL"] == (10, 5) and p["ens6"] == (0, 0)


def test_un_switch_en_calma_no_es_tormenta():
    filas = tp.tasas(tp.leer_puertos(ANTES), tp.leer_puertos(_despues(1050, 2100, 520, 0)), 5)
    assert dict((f[0], (f[1], f[2])) for f in filas)["ens4"] == (10.0, 20.0)
    assert not tp.hay_tormenta(filas, 2000)


def test_dos_puertos_con_decenas_de_miles_de_paquetes_por_segundo_son_tormenta():
    filas = tp.tasas(tp.leer_puertos(ANTES), tp.leer_puertos(_despues(1000 + 50000, 2000 + 50000, 500 + 60000, 0)), 5)
    assert tp.hay_tormenta(filas, 2000)


def test_un_solo_puerto_alto_no_basta():
    filas = tp.tasas(tp.leer_puertos(ANTES), tp.leer_puertos(_despues(1000 + 50000, 2000, 500, 0)), 5)
    assert not tp.hay_tormenta(filas, 2000)          # un host que descarga mucho no es una tormenta


def test_el_puerto_local_no_cuenta():
    antes = tp.leer_puertos(ANTES)
    despues = dict(antes, LOCAL=(10 + 500000, 5 + 500000))
    assert not tp.hay_tormenta(tp.tasas(antes, despues, 5), 2000)


def test_los_playbooks_de_diagnostico_y_freno_son_yaml_valido_y_el_freno_exige_confirmacion():
    import pytest
    yaml = pytest.importorskip("yaml")
    diag = yaml.safe_load(open(os.path.join(AQUI, "95_diagnostico_lazos.yml"), encoding="utf-8"))
    freno = yaml.safe_load(open(os.path.join(AQUI, "99_freno_emergencia.yml"), encoding="utf-8"))
    texto = open(os.path.join(AQUI, "95_diagnostico_lazos.yml"), encoding="utf-8").read()
    assert "SOLO LECTURA" in texto
    for tarea in diag[0]["tasks"]:                    # el diagnóstico no usa ningún módulo que cambie estado
        assert not any(k in tarea for k in ("ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.service"))
    assert "confirmar" in str(freno[0]["tasks"][0])   # el freno se niega sin -e confirmar=true


def test_la_prueba_de_rstp_se_limita_a_los_cores_y_siempre_la_desactiva():
    import pytest
    yaml = pytest.importorskip("yaml")
    texto = open(os.path.join(AQUI, "96_prueba_rstp.yml"), encoding="utf-8").read()
    plays = yaml.safe_load(texto)
    cores = next(p for p in plays if p["name"].startswith("Probar RSTP"))
    assert cores["hosts"] == "cores"                       # los accesos y el borde no se tocan
    bloque = next(t for t in cores["tasks"] if "block" in t)
    assert "rstp_enable=true" in str(bloque["block"][0]) and "rstp_enable=false" in str(bloque["always"][0])
    assert "dejar_rstp" in bloque["always"][0]["when"]     # solo se deja activo si se pide
    assert plays[0]["hosts"] == "switches" and plays[0].get("any_errors_fatal")       # se detiene si ya hay tormenta
    assert "ansible.builtin.copy" not in texto and "set-controller" not in texto      # no instala nada ni mueve controladores
