"""Excepciones por cuenta (docs/DISENO_EXCEPCIONES_POR_SESION.md, fase 1): validación, decisión, flujos y la tubería completa en el simulador."""
import copy
import datetime as dt
import os
import shutil
import sys

import pytest
import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
import excepciones as ex  # noqa: E402
import policy_tables as pt  # noqa: E402
from test_capa3_con_politica_r2 import IPS, desde, paquete, red  # noqa: E402
from test_fase_e import A1, A2, MAC, politica, sesion  # noqa: E402

HOY = dt.date(2026, 11, 24)
AHORA = dt.datetime(2026, 11, 24, 10, 0)
POLITICA_REPO = os.path.join(os.path.dirname(AQUI), "policy.yaml")


def base():
    return pt.cargar_politica(POLITICA_REPO)


def entrada(**cambios):
    e = {"usuario": "profesor02", "servicios": ["student_records"], "hasta": "2026-12-31", "motivo": "Coordinación de matrícula"}
    e.update(cambios)
    return e


def datos(*entradas, **extra):
    return {"roles": ["docente"], "no_excepcionables": ["network_management"], "lista": list(entradas) or [entrada()], **extra}


def errores(d, alta=False, pol=None):
    with pytest.raises(ex.ExcepcionesInvalidas) as e:
        ex.validar(pol or base(), d, HOY, alta=alta)
    return " | ".join(e.value.errores)


def con_excepciones(pol, d):
    pol = copy.deepcopy(pol)
    pol["excepciones"] = ex.validar(pol, d, HOY)
    return pol


# ---------- validación ----------
def test_una_seccion_valida_se_normaliza():
    sec = ex.validar(base(), datos(entrada(usuario="  Profesor02 ", hasta="2026-12-31")), HOY)
    assert sec["roles"] == ["docente"] and sec["no_excepcionables"] == ["network_management"]
    assert sec["lista"] == [{"usuario": "profesor02", "servicios": ["student_records"], "hasta": dt.date(2026, 12, 31),
                             "motivo": "Coordinación de matrícula"}]


def test_hasta_es_opcional():
    assert ex.validar(base(), datos(entrada(hasta=None)), HOY)["lista"][0]["hasta"] is None


@pytest.mark.parametrize("cambio, mensaje", [
    ({"usuario": ""}, "usuario vacío"),
    ({"motivo": "  "}, "falta el motivo"),
    ({"servicios": []}, "servicios debe ser una lista no vacía"),
    ({"servicios": ["no_existe"]}, "servicio desconocido: no_existe"),
    ({"servicios": ["network_management"]}, "no se puede abrir por excepción"),
    ({"servicios": ["grade_system"]}, "ya lo tiene el rol"),
    ({"servicios": ["student_records", "student_records"]}, "servicio repetido"),
    ({"hasta": "31/12/2026"}, "AAAA-MM-DD"),
    ({"hasta": "2027-06-01"}, "máximo de 90 días"),
    ({"otra": 1}, "claves desconocidas"),
])
def test_cada_error_de_una_entrada_se_rechaza_con_un_mensaje_claro(cambio, mensaje):
    assert mensaje in errores(datos(entrada(**cambio)))


def test_rol_desconocido_y_no_excepcionable_desconocido():
    assert "rol desconocido: decano" in errores(datos(roles=["decano"]))
    assert "no_excepcionables: servicio desconocido: x" in errores(datos(no_excepcionables=["x"]))


def test_usuario_repetido_aunque_cambien_mayusculas():
    assert "usuario repetido" in errores(datos(entrada(), entrada(usuario="PROFESOR02", servicios=["print_server"])))


def test_limites_de_servicios_por_usuario_y_de_usuarios():
    pol = base()
    pol["services"].update({f"extra{i}": {"ip": "10.0.0.50", "tcp": 9000 + i} for i in range(4)})
    assert "como mucho 3 servicios" in errores(datos(entrada(servicios=[f"extra{i}" for i in range(4)])), pol=pol)
    once = [entrada(usuario=f"profesor{i:02d}") for i in range(11)]
    assert "como mucho 10 usuarios" in errores(datos(*once))


def test_todos_los_errores_se_informan_juntos():
    msg = errores(datos(entrada(usuario="", motivo=""), entrada(usuario="profesor03", servicios=["no_existe"])))
    assert "usuario vacío" in msg and "falta el motivo" in msg and "no_existe" in msg


def test_un_hasta_vencido_no_invalida_el_archivo_al_cargar_pero_si_al_dar_de_alta():
    vencida = datos(entrada(hasta="2026-11-01"))
    assert ex.validar(base(), vencida, HOY)["lista"][0]["hasta"] == dt.date(2026, 11, 1)
    assert "hasta ya pasó" in errores(vencida, alta=True)


# ---------- cargar_politica ----------
def copia_de_politica(tmp_path):
    destino = tmp_path / "policy.yaml"
    shutil.copy(POLITICA_REPO, destino)
    return str(destino)


def test_sin_archivo_de_excepciones_la_politica_no_cambia(tmp_path, monkeypatch):
    monkeypatch.delenv("EXCEPCIONES_PATH", raising=False)
    p = pt.cargar_politica(copia_de_politica(tmp_path))
    assert "excepciones" not in p and p == base()


def test_el_archivo_junto_a_la_politica_se_carga_y_valida(tmp_path, monkeypatch):
    monkeypatch.delenv("EXCEPCIONES_PATH", raising=False)
    ruta = copia_de_politica(tmp_path)
    (tmp_path / "excepciones.yaml").write_text(yaml.safe_dump(datos(entrada(hasta=None))), encoding="utf8")
    assert pt.cargar_politica(ruta)["excepciones"]["lista"][0]["usuario"] == "profesor02"


def test_un_archivo_invalido_rechaza_toda_la_politica(tmp_path, monkeypatch):
    monkeypatch.delenv("EXCEPCIONES_PATH", raising=False)
    ruta = copia_de_politica(tmp_path)
    (tmp_path / "excepciones.yaml").write_text(yaml.safe_dump(datos(entrada(servicios=["network_management"]))), encoding="utf8")
    with pytest.raises(ex.ExcepcionesInvalidas):
        pt.cargar_politica(ruta)


def test_excepciones_path_apunta_a_otro_archivo(tmp_path, monkeypatch):
    otro = tmp_path / "otro.yaml"
    otro.write_text(yaml.safe_dump(datos(entrada(hasta=None))), encoding="utf8")
    monkeypatch.setenv("EXCEPCIONES_PATH", str(otro))
    assert pt.cargar_politica(POLITICA_REPO)["excepciones"]["lista"][0]["servicios"] == ["student_records"]


# ---------- quién tiene qué ----------
def test_excepciones_de_por_usuario_rol_y_fecha():
    pol = con_excepciones(base(), datos())
    assert ex.excepciones_de(pol, "PROFESOR02", "docente", AHORA) == ["student_records"]
    assert ex.excepciones_de(pol, "profesor03", "docente", AHORA) == []
    assert ex.excepciones_de(pol, "profesor02", "alumno", AHORA) == []          # el rol del momento no admite excepciones
    assert ex.excepciones_de(base(), "profesor02", "docente", AHORA) == []      # sin archivo, nada


def test_hasta_incluye_todo_ese_dia_y_despues_vence():
    pol = con_excepciones(base(), datos())
    assert ex.excepciones_de(pol, "profesor02", "docente", dt.datetime(2026, 12, 31, 23, 59)) == ["student_records"]
    assert ex.excepciones_de(pol, "profesor02", "docente", dt.datetime(2027, 1, 1, 0, 0)) == []


# ---------- flujos ----------
def test_un_flujo_por_servicio_con_la_mac_y_la_prioridad_de_excepcion():
    pol = politica()
    fl = ex.flujos_excepcion(pol, A1, 10, "AA:00:00:00:00:01", ["student_records"])
    assert len(fl) == 1
    f = fl[0]
    assert f["priority"] == ex.PRIO_EXCEPCION == 30500 and f["tableId"] == 10 and f["deviceId"] == A1 and f["isPermanent"]
    tipos = {c["type"]: c for c in f["selector"]["criteria"]}
    assert tipos["ETH_SRC"]["mac"] == "aa:00:00:00:00:01" and tipos["VLAN_VID"]["vlanId"] == 10
    assert tipos["IPV4_DST"]["ip"] == f"{pol['services']['student_records']['ip']}/32" and tipos["TCP_DST"]["tcpPort"] == 8102
    regla_rol = next(r for r in pt.flujos_tabla_rol(pol, A1, "docente", 10) if r["priority"] == pt.PRIO_ROL and len(r["selector"]["criteria"]) == 5)
    assert f["treatment"] == regla_rol["treatment"]                      # mismo tratamiento que las reglas del rol


@pytest.mark.parametrize("reenvio, salida", [("proactivo", {"type": "TABLE", "tableId": 60}), ("normal", {"type": "OUTPUT", "port": "NORMAL"})])
def test_el_tratamiento_sigue_al_modo_de_reenvio(reenvio, salida):
    f = ex.flujos_excepcion(politica(reenvio), A1, 10, MAC["alumno"], ["student_records"])[0]
    assert f["treatment"]["instructions"] == [{"type": "L2MODIFICATION", "subtype": "VLAN_POP"}, salida]


def test_sin_mac_o_con_un_servicio_prohibido_no_hay_flujos():
    with pytest.raises(ValueError):
        ex.flujos_excepcion(politica(), A1, 10, "", ["student_records"])
    with pytest.raises(ValueError):
        ex.flujos_excepcion(politica(), A1, 10, MAC["alumno"], ["network_management"])
    with pytest.raises(ValueError):
        ex.flujos_excepcion(politica(), A1, 10, MAC["alumno"], ["no_existe"])


def test_vista_previa_por_switch():
    pol = con_excepciones(politica(), datos(entrada(servicios=["student_records"])))
    plan = ex.vista_previa(pol, "profesor02", "docente", MAC["alumno"], [A1, A2], AHORA)
    assert set(plan) == {A1, A2} and all(len(v) == 1 and v[0]["tableId"] == 10 for v in plan.values())
    assert ex.vista_previa(pol, "profesor03", "docente", MAC["alumno"], [A1], AHORA) == {}


def test_las_excepciones_no_cuentan_como_reglas_de_r2():
    """`dispositivos_incompletos` cuenta los flujos del appId de R2: las excepciones van con su propio appId y prioridad, y no deben
    hacer pasar por completo a un switch al que le falten reglas del rol."""
    pol = politica()
    assert ex.APP_ID_EXCEPCIONES != pt.APP_ID
    assert all(f["priority"] != ex.PRIO_EXCEPCION for f in pt.flujos_dispositivo(pol, A1))


# ---------- tubería completa: tabla 0 -> tabla del rol -> tabla 60 (capa 3) ----------
def red_con_excepcion(servicios=("student_records",), otro_docente=True):
    r = red(10)                                                            # el «alumno» de la topología entra con una sesión de docente
    pol = politica()
    r["flujos"][A1] += ex.flujos_excepcion(pol, A1, 10, MAC["alumno"], servicios)
    if otro_docente:                                                       # otro equipo con sesión de docente, sin excepción
        r["flujos"][A2].append(sesion(A2, 1, MAC["invitado"], 10))
    return r


def llega(r, nombre, puerto):
    res = desde(r, nombre, paquete(MAC[nombre], IPS[nombre], IPS["servidor"], puerto))
    assert not res["inunda"] and not res["cortado"]
    return res["entregado"] == {"srv1": 1}


def test_sin_excepcion_el_docente_no_alcanza_expedientes():
    assert not llega(red(10), "alumno", 8102)


def test_la_excepcion_abre_solo_ese_servicio_y_solo_a_esa_mac():
    r = red_con_excepcion()
    assert llega(r, "alumno", 8102)              # con la excepción
    assert not llega(r, "invitado", 8102)        # otro docente: no
    assert llega(r, "invitado", 8101)            # pero sigue con lo de su rol
    assert not llega(r, "alumno", 8105)          # otro servicio no autorizado: no
    assert llega(r, "alumno", 8101)              # y lo de su rol sigue igual


def test_la_excepcion_no_sirve_con_otro_rol():
    r = red(20)                                  # la misma MAC con sesión de alumno (tabla 20)
    r["flujos"][A1] += ex.flujos_excepcion(politica(), A1, 10, MAC["alumno"], ["student_records"])   # aunque quedara una regla en la tabla 10
    assert not llega(r, "alumno", 8102)


def test_sin_sesion_la_excepcion_no_abre_nada():
    r = red(None)
    r["flujos"][A1] += ex.flujos_excepcion(politica(), A1, 10, MAC["alumno"], ["student_records"])
    assert not llega(r, "alumno", 8102)
