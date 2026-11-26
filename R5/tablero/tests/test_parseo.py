"""Pruebas del tablero que no necesitan red ni VM: solo el parseo de `r5ctl bloqueos` y la difusion SSE."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server  # noqa: E402


def test_parsear_bloqueos_una_fila():
    texto = "203.0.113.179  sw-core-1     42000      0         44 s  300 s de inactividad"
    filas = server.parsear_bloqueos(texto)
    assert filas == [{"ip": "203.0.113.179", "switch": "sw-core-1", "prioridad": 42000, "paquetes": 0, "vida_s": 44,
                       "vence": "300 s de inactividad"}]


def test_parsear_bloqueos_ignora_la_cabecera():
    texto = "IP bloqueada   switch        prioridad  paquetes  vida  timeout\n" \
            "-------------  ------------  ---------  --------  ----  --------------------\n" \
            "203.0.113.127  switch-borde  42000      20        44 s  300 s de inactividad"
    filas = server.parsear_bloqueos(texto)
    assert len(filas) == 1 and filas[0]["ip"] == "203.0.113.127" and filas[0]["paquetes"] == 20


def test_parsear_bloqueos_varias_filas_de_la_misma_ip():
    texto = "203.0.113.104  switch-borde  42000      17        45 s  300 s de inactividad\n" \
            "203.0.113.104  sw-core-1     42000      1         45 s  300 s de inactividad\n" \
            "203.0.113.104  sw-core-2     42000      0         45 s  300 s de inactividad"
    filas = server.parsear_bloqueos(texto)
    assert len(filas) == 3
    assert {f["switch"] for f in filas} == {"switch-borde", "sw-core-1", "sw-core-2"}


def test_parsear_bloqueos_vacio():
    assert server.parsear_bloqueos("") == []
    assert server.parsear_bloqueos(None) == []


def test_linea_bloqueo_log_reconoce_el_formato_del_integrador():
    """Es la unica forma de saber que una alerta SI termino en bloqueo (classify_alert no deja rastro cuando
    solo registra): el formato exacto lo escribe registrar_incidente en R5/codigo/integrador.py."""
    linea = ("2026-11-26 21:57:07,123 INFO BLOQUEO 203.0.113.103 en switch-borde, sw-core-1, sw-core-2 "
             "(LOCAL Port scan SYN) flow=51791395911384578 lectura->201=12.3 ms")
    m = server.LINEA_BLOQUEO_LOG.search(linea)
    assert m is not None
    assert m["ip"] == "203.0.113.103"
    assert m["switches"] == "switch-borde, sw-core-1, sw-core-2"
    assert m["firma"] == "LOCAL Port scan SYN"
    assert float(m["ms"]) == 12.3


def test_linea_bloqueo_log_no_reconoce_una_alerta_cualquiera():
    assert server.LINEA_BLOQUEO_LOG.search("2026-11-26 21:57:07,123 INFO Integrador arrancando") is None


def test_ejecutar_desbloqueo_sin_ips_validas_no_intenta_ssh():
    """Si la lista viene vacia o con algo que no es una IP, no debe intentar conectarse a ningun sensor (rapido,
    sin red): es la defensa de que el tablero solo desbloquea IP que ya conoce, nunca texto libre."""
    assert server.ejecutar_desbloqueo([]) == (True, "nada que desbloquear")
    assert server.ejecutar_desbloqueo(["no es una ip", "; rm -rf /"]) == (True, "nada que desbloquear")


def test_ip_valida_filtra_lo_que_no_es_una_ip():
    assert server.IP_VALIDA.match("203.0.113.103")
    assert not server.IP_VALIDA.match("203.0.113.103; echo hola")
    assert not server.IP_VALIDA.match("no-es-ip")


def test_siguiente_espera_crece_acotada():
    e = 0.0
    for _ in range(10):
        e = server.siguiente_espera(e)
    assert e == 30.0
    assert server.siguiente_espera(0.0) == 1.0


def test_difundir_llega_a_los_clientes_conectados():
    server._clientes.clear()
    import queue
    q1, q2 = queue.Queue(), queue.Queue()
    server._clientes.extend([q1, q2])
    server.difundir("alerta", {"sensor": "node-sec-1", "hora": "10:00:00", "src": "1.2.3.4", "dst": "10.0.0.10", "sev": 1, "firma": "f"})
    for q in (q1, q2):
        msg = q.get_nowait()
        assert msg.startswith("event: alerta\n")
        assert '"src": "1.2.3.4"' in msg
    server._clientes.clear()


def test_un_cliente_nuevo_recibe_el_ultimo_estado_conocido():
    """El bug real: un navegador que conecta DESPUES de que un sensor ya se puso verde no veia ese aviso (solo se
    manda una vez, en el momento del cambio) y el punto se quedaba en rojo para siempre aunque todo funcionara."""
    server._clientes.clear()
    server._ultimo_estado.clear()
    import queue
    server.difundir("estado", {"sensor": "node-sec-1", "conectado": True})
    server.difundir("bloqueos", {"total": 0, "filas": []})
    q = server.conectar_cliente()
    primero = q.get_nowait()
    segundo = q.get_nowait()
    assert "event: estado" in primero and '"conectado": true' in primero
    assert "event: bloqueos" in segundo
    assert q in server._clientes
    server._clientes.clear()


def test_difundir_descarta_un_cliente_lleno_sin_afectar_a_los_demas():
    server._clientes.clear()
    import queue
    lleno = queue.Queue(maxsize=1)
    lleno.put_nowait("ocupado")
    normal = queue.Queue()
    server._clientes.extend([lleno, normal])
    server.difundir("bloqueos", {"total": 0, "filas": []})
    assert lleno not in server._clientes
    assert normal in server._clientes
    assert not normal.empty()
    server._clientes.clear()
