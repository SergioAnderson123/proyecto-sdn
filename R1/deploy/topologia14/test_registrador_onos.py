"""Pruebas de files/registrador_onos.py sin red: formato de la linea, marca de LENTO, lectura de /proc y respuesta de /devices."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "files"))
import registrador_onos as r  # noqa: E402

PSI = "some avg10=10.19 avg60=6.57 avg300=6.51 total=5669380777\nfull avg10=8.93 avg60=5.70 avg300=5.62 total=5026102452\n"
SNMP = ("Ip: Forwarding DefaultTTL\nIp: 1 64\nTcp: RtoAlgorithm RtoMin RetransSegs AttemptFails\nTcp: 1 200 69571 693\n")
NETSTAT = ("TcpExt: SyncookiesSent TCPTimeouts TCPAbortOnTimeout\nTcpExt: 0 563 10\nIpExt: InNoRoutes\nIpExt: 0\n")


def test_el_avg10_se_lee_de_some_y_de_full():
    assert r._numeros(PSI, "some") == 10.19 and r._numeros(PSI, "full") == 8.93
    assert r._numeros("", "full") is None


def test_las_columnas_de_snmp_y_netstat_se_buscan_por_nombre_no_por_posicion():
    assert r._columna(SNMP, "Tcp:", "RetransSegs") == 69571
    assert r._columna(NETSTAT, "TcpExt:", "TCPTimeouts") == 563
    assert r._columna(SNMP, "Tcp:", "NoExiste") is None


def test_contadores_lee_una_carpeta_proc_falsa_y_no_falla_si_faltan_archivos(tmp_path):
    (tmp_path / "pressure").mkdir()
    (tmp_path / "net").mkdir()
    (tmp_path / "pressure" / "io").write_text(PSI)
    (tmp_path / "net" / "snmp").write_text(SNMP)
    (tmp_path / "net" / "netstat").write_text(NETSTAT)
    (tmp_path / "loadavg").write_text("0.31 0.20 0.10 1/200 999\n")
    c = r.contadores(str(tmp_path))
    assert c == {"io_full10": 8.93, "io_some10": 10.19, "retrans": 69571, "tcp_to": 563, "carga": 0.31}
    assert all(v is None for v in r.contadores(str(tmp_path / "no_existe")).values())


def test_devices_se_entiende_y_una_respuesta_rara_no_rompe():
    cuerpo = json.dumps({"devices": [{"id": "of:2", "available": True}, {"id": "of:1", "available": False}]}).encode()
    assert r.switches_de(cuerpo) == (["of:1", "of:2"], 1)
    assert r.switches_de(b"") == ([], None) and r.switches_de(b"{}") == ([], None)


def test_una_ronda_normal_no_es_lenta_y_lleva_las_tres_vistas():
    por_nodo = [(f"http://192.168.201.{i}:8181", (200, 12, 5, 5), ("of:0000e6925d87354b", 200, 30)) for i in (1, 2, 3)]
    texto, lento = r.linea("node-ctrl-2", 7, por_nodo, {"io_full10": 0.0, "retrans": 5}, 0, 1000)
    assert not lento and "LENTO" not in texto
    assert "ctrl1 dev=200/12ms/5de5 flujos[354b]=200/30ms" in texto and "ctrl3 dev=200/12ms/5de5" in texto
    assert "desde=node-ctrl-2 ronda=7" in texto and "io_full10=0.0" in texto and "io_some10=?" in texto


def test_un_tiempo_largo_o_un_error_marcan_la_linea_como_lenta():
    normal = (200, 12, 5, 5), ("of:1", 200, 30)
    for dev, fl in [((200, 1500, 5, 5), ("of:1", 200, 30)), ((200, 12, 5, 5), ("of:1", 200, 15000)),
                    ((200, 12, 0, 5), ("of:1", 500, 30)), (("ReadTimeout", 20000, None, None), ("of:1", 200, 30))]:
        texto, lento = r.linea("x", 0, [("http://192.168.201.1:8181", *normal), ("http://192.168.201.2:8181", dev, fl)], {}, 0, 1000)
        assert lento and texto.endswith("LENTO"), (dev, fl)


def test_cero_switches_disponibles_con_200_no_se_marca_lento_pero_se_ve_en_la_linea():
    """La vista mala de una replica (23-nov: 0 de 5) tarda poco: la linea lo dice, aunque no sea un atasco."""
    texto, lento = r.linea("x", 0, [("http://192.168.201.1:8181", (200, 12, 0, 5), None)], {}, 0, 1000)
    assert not lento and "dev=200/12ms/0de5" in texto


def test_el_switch_de_cada_ronda_rota_por_los_que_lista_onos(monkeypatch):
    pedidos = []

    def falso(base, ruta, auth, timeout):
        pedidos.append(ruta)
        if ruta == "/devices":
            return 200, 5, json.dumps({"devices": [{"id": f"of:{i}", "available": True} for i in range(3)]}).encode()
        return 200, 5, b"{}"
    monkeypatch.setattr(r, "pedir", falso)
    ids = []
    vistos = []
    for n in range(4):
        pedidos.clear()
        _, ids = r.ronda(["http://192.168.201.1:8181"], "x", 1, n, ids)
        vistos += [p for p in pedidos if p.startswith("/flows/")]
    assert vistos == ["/flows/of:1", "/flows/of:2", "/flows/of:0"]      # la 1.ª ronda aun no conoce los switches: solo pide /devices


def test_el_nombre_corto_es_el_ultimo_octeto():
    assert r.corto("http://192.168.201.3:8181") == "ctrl3"
