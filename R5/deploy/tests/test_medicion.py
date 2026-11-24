"""Pruebas de las funciones puras de medicion_lib.py (falsos positivos por alertas, escalabilidad y recursos). No necesitan VM ni red."""
import calendar
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import medicion_lib as m  # noqa: E402


def alerta_json(src, dst, sid=9000002, firma="LOCAL Port scan SYN", sev=2, direction="to_server", ts="2026-11-23T15:22:00.500000+0000"):
    return json.dumps({"timestamp": ts, "event_type": "alert", "src_ip": src, "dest_ip": dst, "direction": direction,
                       "alert": {"severity": sev, "signature_id": sid, "signature": firma}})


# ---------- estadistica ----------
@pytest.mark.parametrize("p,esperado", [(0, 1), (50, 3), (100, 5), (95, 4.8)])
def test_percentil(p, esperado):
    assert m.percentil([5, 1, 3, 2, 4], p) == pytest.approx(esperado)


def test_percentil_sin_datos_y_con_un_dato():
    assert m.percentil([], 95) is None and m.percentil([7], 95) == 7


def test_resumen_serie():
    r = m.resumen_serie([1, 2, 3, 4])
    assert r["media"] == 2.5 and r["max"] == 4 and r["p95"] == pytest.approx(3.85)
    assert m.resumen_serie([])["media"] is None


# ---------- falsos positivos ----------
def test_leer_alertas_ignora_lo_que_no_es_una_alerta_valida():
    texto = "\n".join([alerta_json("203.0.113.150", "10.0.0.10"), "basura", '{"event_type": "stats"}', '{"event_type": "alert"}', ""])
    a = m.leer_alertas(texto)
    assert len(a) == 1 and a[0]["src"] == "203.0.113.150" and a[0]["sid"] == 9000002
    assert a[0]["epoch"] == pytest.approx(calendar.timegm((2026, 11, 23, 15, 22, 0)) + 0.5)


def test_atacante_de_usa_el_destino_si_la_firma_salto_sobre_la_respuesta():
    assert m.atacante_de({"src": "10.0.0.10", "dst": "203.0.113.7", "direction": "to_client"}) == "203.0.113.7"
    assert m.atacante_de({"src": "203.0.113.7", "dst": "10.0.0.10", "direction": "to_server"}) == "203.0.113.7"


def test_clasificar_separa_verdaderas_falsas_y_otras():
    texto = "\n".join([alerta_json("203.0.113.151", "10.0.0.10"), alerta_json("203.0.113.151", "10.0.0.10", sid=9000003, firma="LOCAL SYN flood"),
                       alerta_json("203.0.113.103", "10.0.0.10"),                                   # trafico legitimo senalado: falso positivo
                       alerta_json("10.0.0.10", "203.0.113.103", direction="to_client"),            # idem, con la firma sobre la respuesta
                       alerta_json("10.0.0.77", "10.0.0.10", sev=3, sid=1, firma="ruido")])          # no atribuible
    c = m.clasificar(m.leer_alertas(texto), ip_malo="203.0.113.151", ip_normal="203.0.113.103")
    assert (c["total"], c["verdaderas"], c["falsas"], c["otras"]) == (5, 2, 2, 1)
    assert c["por_firma"][9000002]["falsas"] == 2 and c["por_firma"][9000003]["verdaderas"] == 1


def test_clasificar_respeta_la_ventana():
    a = m.leer_alertas(alerta_json("203.0.113.103", "10.0.0.10", ts="2026-11-23T15:22:00.000000+0000"))
    t = a[0]["epoch"]
    assert m.clasificar(a, "x", "203.0.113.103", desde=t + 1)["total"] == 0
    assert m.clasificar(a, "x", "203.0.113.103", hasta=t - 1)["total"] == 0
    assert m.clasificar(a, "x", "203.0.113.103", desde=t - 1, hasta=t + 1)["falsas"] == 1


def test_tasas_y_suma_de_corridas():
    c1 = {"total": 10, "verdaderas": 8, "falsas": 1, "otras": 1, "por_firma": {9000002: {"firma": "s", "verdaderas": 8, "falsas": 1, "otras": 1}}}
    c2 = {"total": 10, "verdaderas": 10, "falsas": 0, "otras": 0, "por_firma": {9000002: {"firma": "s", "verdaderas": 10, "falsas": 0, "otras": 0}}}
    tot = m.sumar_clasificaciones([c1, c2])
    assert (tot["total"], tot["falsas"]) == (20, 1) and tot["por_firma"][9000002]["verdaderas"] == 18
    estricto, conservador = m.tasa_falsos_positivos(tot)
    assert estricto == pytest.approx(5.0) and conservador == pytest.approx(10.0)
    assert m.tasa_falsos_positivos({"total": 0, "falsas": 0, "otras": 0}) == (None, None)


# ---------- escalabilidad ----------
def test_resumen_escala_cuenta_la_primera_vez_que_se_bloquea_cada_ip():
    esperadas = [f"198.18.0.{i}" for i in range(1, 5)]
    bloqueos = [(100.5, "198.18.0.1", 10.0, "s1"), (100.9, "198.18.0.2", 12.0, "s1"), (101.0, "198.18.0.2", 30.0, "s2"),   # la 2.a la bloquean los dos sensores
                (102.0, "198.18.0.3", 14.0, "s2"), (100.7, "9.9.9.9", 1.0, "s1")]                                             # ajena al experimento
    r = m.resumen_escala(bloqueos, esperadas, t0=100.0)
    assert (r["esperadas"], r["bloqueadas"]) == (4, 3)
    assert r["primero_s"] == pytest.approx(0.5) and r["ultimo_s"] == pytest.approx(2.0)
    assert r["bloqueos_por_s"] == pytest.approx(3 / 1.5) and r["mitigacion_max_ms"] == 14.0


def test_resumen_escala_sin_bloqueos_o_con_uno_solo():
    assert m.resumen_escala([], ["1.1.1.1"], 0)["bloqueadas"] == 0
    r = m.resumen_escala([(5.0, "1.1.1.1", 9.0, "s1")], ["1.1.1.1"], 4.0)
    assert r["bloqueadas"] == 1 and r["bloqueos_por_s"] is None and r["primero_s"] == pytest.approx(1.0)


# ---------- recursos ----------
STAT = "1234 (Suricata-Main) S 1 1234 1234 0 -1 4194560 100 0 0 0 250 50 0 0 20 0 5 0 100 1000000 5000 18446744073709551615"


def test_jiffies_de_stat_con_nombre_raro():
    assert m.jiffies_de_stat(STAT) == 300
    assert m.jiffies_de_stat(STAT.replace("Suricata-Main", "mi (proc) x")) == 300


def test_rss_y_cpu_y_mbps():
    assert m.rss_kb_de_status("Name:\tx\nVmRSS:\t  57344 kB\nThreads:\t3\n") == 57344
    assert m.rss_kb_de_status("Name:\tx\n") == 0
    assert m.cpu_porcentaje(100, 150, 1.0, 100) == pytest.approx(50.0)
    assert m.cpu_porcentaje(0, 10, 0, 100) == 0.0
    assert m.mbps(0, 1_250_000, 1.0) == pytest.approx(10.0)
    assert m.mbps(5, 1, 1.0) == 0.0                                  # contador reiniciado


# ---------- muestreador (medir_recursos.py) con un /proc y un /sys falsos ----------
def test_muestreador_lee_cpu_memoria_y_ancho_de_banda(tmp_path, monkeypatch):
    import threading
    import time

    import medir_recursos as mr
    proc, sysd = tmp_path / "proc", tmp_path / "sys"
    for pid, cmd, jif in ((100, b"suricata\0-c\0/etc/suricata/suricata.yaml", 0), (200, b"python3\0integrador.py", 0), (300, b"bash", 0)):
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(cmd)
        (d / "stat").write_text(STAT.replace("250 50", f"{jif} 0"))
        (d / "status").write_text("VmRSS:\t  51200 kB\n")
    nic = sysd / "class" / "net" / "ens4" / "statistics"
    nic.mkdir(parents=True)
    (nic / "rx_bytes").write_text("0")
    (nic / "rx_packets").write_text("0")
    monkeypatch.setattr(mr, "PROC", str(proc))
    monkeypatch.setattr(mr, "SYS", str(sysd))

    def carga():                                   # a mitad de la medicion sube el uso de CPU de Suricata y llegan paquetes al espejo
        time.sleep(0.25)
        (proc / "100" / "stat").write_text(STAT.replace("250 50", "100 0"))
        (nic / "rx_bytes").write_text("1250000")
        (nic / "rx_packets").write_text("1000")
    hilo = threading.Thread(target=carga)
    hilo.start()
    muestras, hallados = mr.muestrear(0.6, 0.1, ["ens4"], 100)
    hilo.join()
    assert hallados == {"suricata": 1, "integrador": 1}
    assert max(f["cpu_suricata"] for f in muestras) > 0 and max(f["espejo_mbps"] for f in muestras) > 0
    assert all(f["cpu_integrador"] == 0 and f["rss_suricata_mb"] == 50.0 for f in muestras)
    r = mr.resumen(muestras)
    assert r["rss_suricata_mb"]["media"] == 50.0 and r["espejo_pps"]["max"] > 0


# ---------- orquestador (medicion_r5.py) con un remoto falso ----------
class RemotoFalso:
    """Responde como lo harian las VM: lineas del eve, CSV de verdad y lineas BLOQUEO del integrador."""

    def __init__(self, alertas_por_sensor, bloqueos_por_sensor, csv_verdad="", t_inicio=None):
        self.alertas, self.bloqueos, self.csv, self.llamadas = alertas_por_sensor, bloqueos_por_sensor, csv_verdad, []
        self.t_inicio = t_inicio

    def correr(self, ip, script, timeout=90):
        self.llamadas.append((ip, script))
        if "wc -l" in script:
            return 0, "0"
        if "tail -n +" in script:
            return 0, "\n".join(self.alertas.get(ip, []))
        if "docker logs" in script:
            return 0, "\n".join(self.bloqueos.get(ip, []))
        if "trafico_mixto.py" in script:
            return 0, "aviso de sudo\n" + self.csv
        if "T0=" in script:
            return 0, f"T0={self.t_inicio}"
        return 0, ""


def linea_bloqueo(epoch, ip, ms):
    import time as _t
    return f"{_t.strftime('%Y-%m-%d %H:%M:%S', _t.gmtime(epoch))},{int((epoch % 1) * 1000):03d} INFO BLOQUEO {ip} en of:0000000000000001 (x) flow=1 lectura->201={ms} ms"


def test_cmd_fp_clasifica_las_alertas_y_detecta_el_bloqueo_de_la_ip_legitima(monkeypatch, capsys):
    import argparse
    import time as _t

    import medicion_r5 as mr
    monkeypatch.setattr(mr, "subir", lambda *a, **k: True)
    monkeypatch.setattr(mr.time, "sleep", lambda s: None)
    ahora = _t.time()
    verdad = "epoch,etiqueta,tipo,origen,destino,paquetes,sid_esperado\n" + "\n".join(
        [f"{ahora:.3f},normal,http_get,propia,10.0.0.10,6,"] * 8 + [f"{ahora:.3f},malo,scan_syn,203.0.113.151,10.0.0.10,120,9000002"] * 2)
    s1, s2 = mr.SENSORES["node-sec-1"], mr.SENSORES["node-sec-2"]
    rem = RemotoFalso({s1: [alerta_json("203.0.113.151", "10.0.0.10"), alerta_json("203.0.113.151", "10.0.0.10", sid=9000003, firma="LOCAL SYN flood")],
                       s2: [alerta_json("203.0.113.103", "10.0.0.10"), alerta_json("203.0.113.151", "10.0.0.10")]},
                      {s1: [linea_bloqueo(ahora + 1, "203.0.113.151", 11.0)], s2: [linea_bloqueo(ahora + 2, "203.0.113.103", 12.0)]}, verdad)
    a = argparse.Namespace(n=1, duracion=10, pct_malo=20, nat=0, clientes=40, conexiones=6)
    filas = mr.cmd_fp(rem, a)
    salida = capsys.readouterr().out
    f = filas[0]
    assert (f["alertas"], f["verdaderas"], f["falsas"], f["otras"]) == (4, 3, 1, 0)
    assert (f["eventos_normales"], f["eventos_maliciosos"]) == (8, 2) and f["ip_legitima_bloqueada"] == 1
    assert "FP 25.0 %" in salida and "IP legítima bloqueada: SÍ" in salida


def test_cmd_escala_cuenta_los_bloqueos_de_los_dos_sensores(monkeypatch, capsys):
    import argparse
    import time as _t

    import medicion_r5 as mr
    monkeypatch.setattr(mr.time, "sleep", lambda s: None)
    t0 = _t.time()
    k = 4
    tercero = 1 + (1 + 10 * (k % 20)) % 250
    ips = [f"198.18.{tercero}.{i}" for i in range(1, k + 1)]
    s1, s2 = mr.SENSORES["node-sec-1"], mr.SENSORES["node-sec-2"]
    bl1 = [linea_bloqueo(t0 + 0.2 + 0.1 * i, ip, 10.0 + i) for i, ip in enumerate(ips[:3])]
    bl2 = [linea_bloqueo(t0 + 0.9, ips[3], 30.0), linea_bloqueo(t0 + 1.5, ips[0], 11.0)]          # la 4.a la bloquea el otro sensor; la 1.a, ambos
    rem = RemotoFalso({}, {s1: bl1, s2: bl2}, t_inicio=f"{t0:.3f}")
    a = argparse.Namespace(origenes=str(k), n=1, pausa=0)
    filas = mr.cmd_escala(rem, a)
    r = filas[0]
    assert (r["esperadas"], r["bloqueadas"], r["origenes"]) == (4, 4, 4)
    assert r["ultimo_s"] == pytest.approx(0.9, abs=0.05) and r["mitigacion_max_ms"] == 30.0
    assert "100%" in capsys.readouterr().out


def test_codigo_nat_es_un_python_valido():
    import medicion_r5 as mr
    cuerpo = mr.codigo_nat("10.0.0.10", 40, 6).split("<<'PY'\n")[1].rsplit("\nPY", 1)[0]
    compile(cuerpo, "nat", "exec")
