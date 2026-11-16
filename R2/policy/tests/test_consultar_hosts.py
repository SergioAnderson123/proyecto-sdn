"""Cruce de hosts de ONOS con las sesiones 802.1X (la consulta de lectura previa a la fase E)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import consultar_hosts as ch  # noqa: E402

HOSTS = {"hosts": [{"mac": "aa:00:00:00:00:01", "locations": [{"elementId": "of:0000e6925d87354b", "port": "1"}], "ipAddresses": ["10.0.0.101"]},
                   {"mac": "aa:00:00:00:00:09", "locations": [{"elementId": "of:000052ca28945241", "port": "1"}], "ipAddresses": []}]}
AAA = {"users": [{"macAddress": "AA:00:00:00:00:01", "state": "AUTHORIZED", "subscriberId": "of:0000e6925d87354b/1", "username": "alice"},
                 {"macAddress": "AA:00:00:00:00:02", "state": "AUTHORIZED", "subscriberId": "of:0000766a0c84c741/1", "username": "bob"},
                 {"macAddress": "AA:00:00:00:00:03", "state": "STARTED", "subscriberId": "of:0000766a0c84c741/2", "username": "carol"}]}


def test_hosts_y_sesiones_se_leen_y_las_mac_se_normalizan():
    h, s = ch.hosts_de(HOSTS), ch.sesiones_de(AAA)
    assert h[0]["mac"] == "AA:00:00:00:00:01" and h[0]["puerto"] == "1" and h[1]["ips"] == []
    assert [x["estado"] for x in s] == ["AUTHORIZED", "AUTHORIZED", "STARTED"]


def test_el_cruce_separa_quien_tiene_sesion_pero_onos_no_lo_conoce():
    r = ch.cruzar(ch.hosts_de(HOSTS), ch.sesiones_de(AAA))
    assert [x["mac"] for x in r["con_sesion_y_host"]] == ["AA:00:00:00:00:01"]
    assert [x["mac"] for x in r["con_sesion_sin_host"]] == ["AA:00:00:00:00:02"]          # autorizada y sin host: necesitaría otro camino
    assert [x["mac"] for x in r["hosts_sin_sesion"]] == ["AA:00:00:00:00:09"]             # la sesión no autorizada (STARTED) no cuenta


def test_formatos_raros_no_rompen_la_consulta():
    assert ch.sesiones_de([]) == [] and ch.sesiones_de({"entries": "x"}) == [] and ch.hosts_de({}) == []
    s = ch.sesiones_de([{"state": "AUTHORIZED", "algo": "aa:bb:cc:dd:ee:ff"}])
    assert s[0]["mac"] == "AA:BB:CC:DD:EE:FF"                                              # si el campo se llama distinto, se busca una MAC en la entrada
