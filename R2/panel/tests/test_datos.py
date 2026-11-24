"""Lógica pura del panel (datos.py): sesiones, historial de eventos con filtros, vistas desiguales de ONOS, estado del vigilante y avisos."""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import datos as d  # noqa: E402

AHORA = 1_000_000.0


# ---------- sesiones ----------
def test_las_sesiones_se_aplanan_ordenadas_y_con_el_tiempo_que_les_queda():
    resp = {"total_sessions": 2, "sessions": {
        "FA:16:3E:B6:96:08": {"username": "profesor01@example.org", "dpid": "of:0000e6925d87354b", "port": "1", "vlan": 10, "rol": "Docente", "flow_id": "7", "metodo": "portal", "expira": AHORA + 300, "base": "h1"},
        "fa:16:3e:71:08:66": {"username": "alumno01@example.org", "dpid": "of:0000766a0c84c741", "port": "1", "vlan": 20, "rol": "Alumno", "flow_id": "8", "metodo": "802.1x", "expira": None, "base": None}}}
    s = d.normalizar_sesiones(resp, AHORA)
    assert [x["usuario"] for x in s] == ["alumno01@example.org", "profesor01@example.org"]
    assert s[0]["expira_en_s"] is None and s[1]["expira_en_s"] == 300 and s[1]["mac"] == "fa:16:3e:b6:96:08" and s[1]["base"] == "h1"


def test_una_respuesta_vacia_o_rota_no_da_sesiones_ni_falla():
    for resp in (None, {}, {"sessions": None}, {"sessions": {}}):
        assert d.normalizar_sesiones(resp, AHORA) == []


def test_el_resumen_de_sesiones_cuenta_por_rol():
    s = [{"rol": "Alumno"}, {"rol": "Alumno"}, {"rol": "Docente"}, {"rol": None}]
    assert d.sesiones_resumen(s) == {"total": 4, "por_rol": {"Alumno": 2, "Docente": 1, "?": 1}}


# ---------- historial de eventos ----------
def _ev(t, tipo="admitido", usuario="u@x", mac="aa:bb:cc:dd:ee:01", detalle="VLAN 20", **kw):
    return dict({"t": t, "tipo": tipo, "usuario": usuario, "mac": mac, "dpid": "of:1", "port": "1", "detalle": detalle, "rol": "Alumno", "vlan": 20}, **kw)


def test_un_evento_leido_muchas_veces_se_guarda_una_sola_vez():
    h = d.Historial()
    assert h.agregar([_ev(1), _ev(2)]) == 2
    assert h.agregar([_ev(1), _ev(2), _ev(3)]) == 1
    assert h.contar() == 3


def test_el_historial_conserva_mas_de_lo_que_el_mapper_rota_y_devuelve_lo_mas_nuevo_primero():
    h = d.Historial()
    h.agregar([_ev(t) for t in range(1, 401)])                       # el mapper solo guarda 200
    h.agregar([_ev(t) for t in range(201, 601)])                     # lo viejo ya no está en el mapper
    assert h.contar() == 600
    assert [e["t"] for e in h.listar(limite=3)] == [600, 599, 598]


def test_el_historial_tiene_tope_y_borra_lo_mas_viejo():
    h = d.Historial(maximo=50)
    h.agregar([_ev(t) for t in range(1, 201)])
    assert h.contar() <= 51 and h.listar(limite=1)[0]["t"] == 200


def test_los_eventos_sin_hora_o_que_no_son_diccionarios_se_ignoran():
    h = d.Historial()
    assert h.agregar([None, "x", {"tipo": "admitido"}, _ev(5)]) == 1


def test_filtros_por_tipo_usuario_mac_texto_y_fechas():
    h = d.Historial()
    h.agregar([_ev(1, "admitido", "profesor01@example.org", "fa:16:3e:b6:96:08", "VLAN 10, tabla 10"),
               _ev(2, "rechazado", "x", "", "Portal: usuario o contraseña incorrectos"),
               _ev(3, "logout", "profesor01@example.org", "fa:16:3e:b6:96:08", "Logout avisado por el agente"),
               _ev(4, "admitido", "alumno01@example.org", "fa:16:3e:71:08:66", "VLAN 20, tabla 20")])
    assert [e["t"] for e in h.listar(tipo="admitido")] == [4, 1]
    assert [e["t"] for e in h.listar(usuario="PROFESOR")] == [3, 1]                         # subcadena, sin mayúsculas
    assert [e["t"] for e in h.listar(mac="FA-16-3E-71")] == [4]                              # prefijo, con guiones y en mayúsculas
    assert [e["t"] for e in h.listar(texto="contraseña")] == [2]
    assert [e["t"] for e in h.listar(desde=2, hasta=3)] == [3, 2]
    assert [e["t"] for e in h.listar(tipo="admitido", usuario="alumno")] == [4]
    assert h.listar(tipo="inexistente") == []


def test_los_filtros_no_son_inyeccion_sql_ni_comodines():
    h = d.Historial()
    h.agregar([_ev(1, usuario="a_b"), _ev(2, usuario="axb"), _ev(3, usuario="100%")])
    assert [e["t"] for e in h.listar(usuario="a_b")] == [1]                                 # el guion bajo es literal
    assert [e["t"] for e in h.listar(usuario="%")] == [3]                                   # el porcentaje es literal
    assert h.listar(usuario="'; DROP TABLE eventos; --") == []
    assert h.contar() == 3


def test_el_limite_se_acota():
    h = d.Historial()
    h.agregar([_ev(t) for t in range(1, 30)])
    assert len(h.listar(limite=5)) == 5 and len(h.listar(limite=0)) == 1 and len(h.listar(limite=10**9)) == 29


# ---------- vistas de ONOS ----------
def _devs(**disp):
    return {"devices": [{"id": i, "available": v, "role": "MASTER" if v else "NONE"} for i, v in disp.items()]}


def test_vistas_iguales_en_los_tres_nodos_no_hay_desigualdad():
    v = d.vistas_onos({n: _devs(a=True, b=True) for n in ("ctrl1", "ctrl2", "ctrl3")})
    assert v["desiguales"] == [] and all(n["ven"] == 2 and n["responde"] for n in v["nodos"].values())


def test_la_vista_mala_del_23_nov_se_detecta_dos_nodos_ven_0_y_uno_ve_5():
    sw = ["s1", "s2", "s3", "s4", "s5"]
    v = d.vistas_onos({"ctrl1": _devs(**{i: False for i in sw}), "ctrl2": _devs(**{i: True for i in sw}), "ctrl3": _devs(**{i: False for i in sw})})
    assert v["desiguales"] == sw
    assert v["nodos"]["ctrl1"] == {"responde": True, "ven": 0, "total": 5} and v["nodos"]["ctrl2"]["ven"] == 5


def test_un_nodo_que_no_contesta_no_cuenta_como_que_no_ve():
    v = d.vistas_onos({"ctrl1": _devs(a=True), "ctrl2": None, "ctrl3": _devs(a=True)})
    assert v["desiguales"] == [] and v["nodos"]["ctrl2"] == {"responde": False, "ven": 0, "total": 0}


def test_un_switch_que_solo_conoce_un_nodo_no_es_desigual():
    v = d.vistas_onos({"ctrl1": _devs(a=True, b=True), "ctrl2": _devs(a=True)})
    assert v["desiguales"] == [] and v["switches"][1]["por_nodo"]["ctrl2"]["disponible"] is None


def test_sin_datos_de_ningun_nodo_no_falla():
    v = d.vistas_onos({"ctrl1": None, "ctrl2": None})
    assert v == {"switches": [], "nodos": {"ctrl1": {"responde": False, "ven": 0, "total": 0}, "ctrl2": {"responde": False, "ven": 0, "total": 0}}, "desiguales": []}


# ---------- vigilante ----------
def _salud(estado="sin cambios", ciclo=AHORA - 2, activo=True, rol="principal"):
    return {"rol": rol, "ultimo_ciclo": ciclo, "estado": estado, "aplica": True, "activo": activo}


def test_el_vigilante_sano_esta_ok():
    e = d.estado_vigilante(_salud(), AHORA)
    assert e["ok"] and e["responde"] and e["edad_ciclo_s"] == 2


def test_el_vigilante_en_partida_o_con_ciclo_viejo_pide_atencion():
    assert not d.estado_vigilante(_salud("partida"), AHORA)["ok"]
    assert "hace" in d.estado_vigilante(_salud(ciclo=AHORA - 120), AHORA)["motivo"]
    assert not d.estado_vigilante(None, AHORA)["responde"]
    assert not d.estado_vigilante({"estado": "sin cambios"}, AHORA)["ok"]


# ---------- avisos y resumen ----------
def test_los_avisos_incluyen_la_vista_desigual_con_la_gravedad_mas_alta_primero():
    sw = ["s1", "s2"]
    vistas = d.vistas_onos({"ctrl1": _devs(**{i: False for i in sw}), "ctrl2": _devs(**{i: True for i in sw}), "ctrl3": None})
    vig = {"ctrl3": d.estado_vigilante(None, AHORA), "ctrl2": d.estado_vigilante(_salud(rol="respaldo", activo=False), AHORA)}
    a = d.avisos(vistas, vig, True, ["mapper /events"])
    codigos = [x["codigo"] for x in a]
    assert codigos.count("VISTA_DESIGUAL") == 2 and "NODO_ONOS_SIN_RESPUESTA" in codigos and "NODO_SIN_SWITCHES" in codigos
    assert "VIGILANTE_SIN_ACTIVO" in codigos and "VIGILANTE_SIN_RESPUESTA" in codigos and "FUENTE_CAIDA" in codigos
    graves = [x["gravedad"] for x in a]
    assert graves == sorted(graves, key=lambda g: 0 if g == "alta" else 1)


def test_una_red_sana_no_da_avisos():
    vistas = d.vistas_onos({n: _devs(a=True) for n in ("ctrl1", "ctrl2", "ctrl3")})
    vig = {"ctrl3": d.estado_vigilante(_salud(), AHORA), "ctrl2": d.estado_vigilante(_salud(rol="respaldo", activo=False), AHORA)}
    assert d.avisos(vistas, vig, True, []) == []


def test_el_mapper_no_listo_es_un_aviso_alto():
    assert [a["codigo"] for a in d.avisos({"nodos": {}, "desiguales": []}, {}, False)] == ["MAPPER_NO_LISTO"]
    assert d.avisos({"nodos": {}, "desiguales": []}, {}, None) == []                                # desconocido no es una alarma


def test_el_resumen_junta_switches_nodos_sesiones_y_avisos():
    vistas = d.vistas_onos({"ctrl1": _devs(a=True, b=False), "ctrl2": _devs(a=True, b=False)})
    r = d.resumen(vistas, {}, [{"rol": "Alumno"}], True, [], AHORA)
    assert r["switches"] == {"total": 2, "disponibles": 1} and r["sesiones"] == {"total": 1, "por_rol": {"Alumno": 1}} and r["avisos"] == [] and r["t"] == AHORA
