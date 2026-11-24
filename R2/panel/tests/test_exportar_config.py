"""exportar_config.py: la foto de la configuración estática (archivos con sus copias y diferencias) y la política interpretada con el mismo código del mapper, sobre los archivos REALES del repo."""
import json
import os
import shutil
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
REPO = os.path.abspath(os.path.join(RAIZ, "..", ".."))
sys.path.insert(0, RAIZ)
import exportar_config as ex  # noqa: E402

POLICY = os.path.join(REPO, "R2", "policy", "policy.yaml")
PLAN = os.path.join(REPO, "R2", "policy", "planes", "vnrt_topologia14.json")
HOSTS = os.path.join(REPO, "R2", "policy", "planes", "hosts_fijos_capa3.json")
PRIORIDADES = os.path.join(REPO, "R5", "deploy", "prioridades.yaml")
LOCAL = "reenvio: proactivo\nsin_usuarios: [\"of:00007e3401ecd842\", \"of:000076f83813d34e\"]\ntrusted:\n  \"of:000052ca28945241\": all\n"
CORE1, ACC1 = "of:00007e3401ecd842", "of:0000e6925d87354b"


def poner(carpeta, fuente, nombre, contenido):
    os.makedirs(os.path.join(carpeta, fuente), exist_ok=True)
    modo = "wb" if isinstance(contenido, bytes) else "w"
    with open(os.path.join(carpeta, fuente, nombre), modo, **({} if modo == "wb" else {"encoding": "utf-8", "newline": ""})) as f:
        f.write(contenido)


def copiar(carpeta, fuente, nombre, origen):
    os.makedirs(os.path.join(carpeta, fuente), exist_ok=True)
    shutil.copy(origen, os.path.join(carpeta, fuente, nombre))


@pytest.fixture()
def carpeta(tmp_path):
    c = str(tmp_path)
    copiar(c, "repo", "policy.yaml", POLICY)
    copiar(c, "repo", "plan_capa3.json", PLAN)
    copiar(c, "repo", "hosts_fijos_capa3.json", HOSTS)
    copiar(c, "repo", "prioridades.yaml", PRIORIDADES)
    for ctrl in ("ctrl-1", "ctrl-2"):
        copiar(c, ctrl, "policy.yaml", POLICY)
        poner(c, ctrl, "policy.local.yaml", LOCAL)
    for ctrl in ("ctrl-2", "ctrl-3"):
        copiar(c, ctrl, "plan_capa3.json", PLAN)
        copiar(c, ctrl, "hosts_fijos_capa3.json", HOSTS)
        poner(c, ctrl, "vigilante-capa3.service", "[Service]\nExecStart=/usr/bin/python3 /opt/vigilante/vigilante.py --aplicar\n")
    return c


def por_id(res):
    return {a["id"]: a for a in res["archivos"]}


# ---------- los archivos y sus copias ----------
def test_todas_las_copias_iguales_dan_igual_y_se_listan_las_fuentes_sin_copia(carpeta):
    a = por_id(ex.exportar(carpeta, ahora=5.0))
    p = a["policy.yaml"]
    assert p["estado"] == "igual" and p["faltan"] == ["ctrl-3"] and len(p["contenidos"]) == 1 and [c["fuente"] for c in p["copias"] if c["existe"]] == ["repo", "ctrl-1", "ctrl-2"]
    assert a["plan_capa3.json"]["estado"] == "igual" and a["hosts_fijos_capa3.json"]["faltan"] == ["ctrl-1"]
    assert a["policy.local.yaml"]["estado"] == "igual" and a["policy.local.yaml"]["faltan"] == ["repo", "ctrl-3"]                # el local no está en el repo, y es lo esperado


def test_un_archivo_con_una_sola_copia_no_dice_que_es_igual_a_nada(carpeta):
    a = por_id(ex.exportar(carpeta))
    assert a["prioridades.yaml"]["estado"] == "una_copia" and a["vigilante-capa3.service"]["estado"] == "igual"           # el de prioridades solo está en el repo; la unidad, en ctrl-2 y ctrl-3
    assert a["prioridades.yaml"]["diferencias"] == []


def test_una_copia_distinta_se_marca_diverge_con_su_diff_frente_al_repo(carpeta):
    original = open(POLICY, encoding="utf-8", newline="").read()
    poner(carpeta, "ctrl-2", "policy.yaml", original.replace("student,   tabla: 20", "student,   tabla: 21").replace("tabla: 20}", "tabla: 21}"))
    p = por_id(ex.exportar(carpeta))["policy.yaml"]
    assert p["estado"] == "diverge" and len(p["contenidos"]) == 2 and len(p["diferencias"]) == 1
    d = p["diferencias"][0]
    assert d["base"] == "repo" and d["con"] == ["ctrl-2"] and "-" in d["diff"] and "+" in d["diff"] and "--- repo" in d["diff"] and "+++ ctrl-2" in d["diff"]
    assert "21" in d["diff"]


def test_dos_copias_iguales_entre_si_pero_distintas_del_repo_dan_un_solo_diff_para_las_dos(carpeta):
    for ctrl in ("ctrl-1", "ctrl-2"):
        poner(carpeta, ctrl, "plan_capa3.json", '{"gw_mac": "02:00:5E:00:00:FE", "subredes": []}')
    p = por_id(ex.exportar(carpeta))["plan_capa3.json"]
    assert p["estado"] == "diverge" and [d["con"] for d in p["diferencias"]] == [["ctrl-1", "ctrl-2"]]


def test_sin_ninguna_copia_es_sin_datos_y_un_archivo_binario_o_enorme_no_rompe(tmp_path):
    c = str(tmp_path)
    assert por_id(ex.exportar(c))["hosts_fijos_capa3.json"]["estado"] == "sin_datos"
    poner(c, "repo", "hosts_fijos_capa3.json", b"\xff\xfe\x00 no es utf-8 \x80")
    poner(c, "ctrl-1", "plan_capa3.json", "x" * 300_000)
    a = por_id(ex.exportar(c))
    assert a["hosts_fijos_capa3.json"]["estado"] == "una_copia" and "�" in next(iter(a["hosts_fijos_capa3.json"]["contenidos"].values()))
    texto = next(iter(a["plan_capa3.json"]["contenidos"].values()))
    assert len(texto) < 201_000 and "recortado" in texto
    assert a["plan_capa3.json"]["copias"][1]["bytes"] == 300_000


def test_la_salida_es_json_y_no_lleva_los_bytes_crudos(carpeta):
    res = ex.exportar(carpeta)
    texto = json.dumps(res)
    assert "_datos" not in texto and json.loads(texto)["version"] == 1 and res["fuentes"] == ["repo", "ctrl-1", "ctrl-2", "ctrl-3"]


# ---------- la política interpretada ----------
def pol(carpeta):
    return ex.exportar(carpeta)["politica"]


def test_se_interpreta_la_politica_de_la_replica_del_mapper_con_su_overlay_local(carpeta):
    p = pol(carpeta)
    assert p["origen"] == "ctrl-1" and p["reenvio"] == "proactivo" and "error" not in p
    assert {r["rol"]: (r["matriz"], r["tabla"]) for r in p["roles"]} == {"docente": ("professor", 10), "alumno": ("student", 20), "soporte": ("administrator", 40), "invitado": ("guest", 50)}


def test_la_matriz_dice_permitido_denegado_o_sin_regla_con_lo_real_del_policy_yaml(carpeta):
    m = pol(carpeta)["matriz"]
    assert m["professor"]["grade_system"] is True and m["student"]["grade_system"] is False and m["guest"]["university_portal"] is True
    assert m["administrator"]["network_management"] is True and m["student"]["network_management"] is False
    assert set(m) == {"professor", "student", "administrator", "guest"} and "internet" in m["student"]


def test_el_catalogo_y_los_servicios_sin_host_se_distinguen(carpeta):
    p = pol(carpeta)
    assert p["servicios"]["grade_system"] == {"ip": "10.0.0.50", "tcp": 8101} and p["sin_host"] == ["internet"]      # «internet» tiene reglas pero ningún host: no se instala
    assert p["n_reglas_matriz"] == 41 or p["n_reglas_matriz"] > 30


def test_portal_ascenso_y_cuarentena_se_resumen(carpeta):
    p = pol(carpeta)
    assert p["ascenso"] == {"alumno": ["docente"]} and p["ascenso_exigir_ubicacion"] is True and p["cuarentena"] == {"tabla": 99}
    assert p["portal"]["login_directo"] == ["invitado"] and p["portal"]["grupos"] == {"profesor": "docente", "invitado": "invitado"} and p["portal"]["duracion_s"]["invitado"] == 1800


def test_los_flujos_de_cada_tabla_de_rol_salen_del_mismo_codigo_que_los_instala(carpeta):
    p = pol(carpeta)["flujos_por_rol"]
    alumno = p["alumno"]
    assert {f["tabla"] for f in alumno} == {20} and any(f["prioridad"] == 0 and f["acciones"] == ["descarte"] for f in alumno)             # DROP explícito del resto
    permitidos = {(f["coincide"]["ip_dst"], f["coincide"]["tcp_dst"]) for f in alumno if "tcp_dst" in f["coincide"]}
    assert ("10.0.0.40/32", "8001") in permitidos and ("10.0.0.50/32", "8101") not in permitidos                                          # el alumno no llega al sistema de notas
    docente = {(f["coincide"]["ip_dst"], f["coincide"]["tcp_dst"]) for f in p["docente"] if "tcp_dst" in f["coincide"]}
    assert ("10.0.0.50/32", "8101") in docente
    assert all(f["app"] == "org.campus.r2" for f in alumno) and any("ir a la tabla 60" in f["acciones_texto"] for f in alumno)             # reenvío proactivo: a la tabla 60
    assert any("rol alumno" in f["explicacion"] for f in alumno)


def test_para_cada_switch_se_listan_los_flujos_que_r2_espera_y_los_cores_sin_usuarios_no_llevan_tablas_de_rol(carpeta):
    esp = pol(carpeta)["esperados"]
    assert len(esp) == 5 and esp[CORE1]["nombre"] == "core-1" and esp[ACC1]["nombre"] == "acc-1"
    tablas_core = {f["tabla"] for f in esp[CORE1]["flujos"]}
    tablas_acc = {f["tabla"] for f in esp[ACC1]["flujos"]}
    assert not (tablas_core & {10, 20, 40, 50, 99}) and {10, 20, 40, 50, 99} <= tablas_acc
    assert len(esp[ACC1]["flujos"]) > len(esp[CORE1]["flujos"]) > 0


def test_sin_copias_desplegadas_se_interpreta_la_del_repo_y_sin_ninguna_se_dice(tmp_path):
    c = str(tmp_path)
    copiar(c, "repo", "policy.yaml", POLICY)
    assert pol(c)["origen"] == "repo"
    assert "no hay ningún policy.yaml" in ex.exportar(str(tmp_path / "vacia"))["politica"]["error"]


def test_una_politica_rota_no_impide_ver_los_archivos(tmp_path):
    c = str(tmp_path)
    poner(c, "ctrl-1", "policy.yaml", "roles: [esto: no: es: yaml: valido\n  - {")
    r = ex.exportar(c)
    assert "error" in r["politica"] and r["politica"]["origen"] == "ctrl-1" and por_id(r)["policy.yaml"]["estado"] == "una_copia"


def test_policy_tables_se_importa_sin_httpx_porque_el_borde_puede_no_tenerlo(monkeypatch):
    import importlib
    monkeypatch.setitem(sys.modules, "httpx", None)                 # simula «no instalado»: import httpx lanza ImportError
    sys.modules.pop("policy_tables", None)
    pt = importlib.import_module("policy_tables")
    assert pt.httpx is None and pt.cargar_politica(POLICY)["roles"]["alumno"]["tabla"] == 20
    sys.modules.pop("policy_tables", None)


def test_la_linea_de_comandos_escribe_el_json_de_una_vez(carpeta, tmp_path, capsys):
    salida = str(tmp_path / "salida" / "config.json")
    os.makedirs(os.path.dirname(salida))
    assert ex.main(["--carpeta", carpeta, "--salida", salida]) == 0
    j = json.load(open(salida, encoding="utf-8"))
    assert j["politica"]["roles"] and not os.path.exists(salida + ".tmp") and "policy.yaml: igual" in capsys.readouterr().out


# ---------- comparación por contenido lógico (lo que se vio al desplegar en la red real) ----------
def test_el_mismo_json_con_otra_sangria_es_igual_y_lo_dice_en_una_nota(tmp_path):
    c = str(tmp_path)
    poner(c, "repo", "hosts_fijos_capa3.json", '[\n {"nombre": "server", "ip": "10.0.0.10", "port": 5}\n]\n')
    poner(c, "ctrl-2", "hosts_fijos_capa3.json", '[{"port": 5, "ip": "10.0.0.10", "nombre": "server"}]')                       # compacto y con otro orden de claves
    a = por_id(ex.exportar(c))["hosts_fijos_capa3.json"]
    assert a["estado"] == "igual" and "distinto formato" in a["nota"] and a["diferencias"] == [] and len(a["contenidos"]) == 2          # se siguen pudiendo ver las dos


def test_un_json_con_otro_valor_si_diverge(tmp_path):
    c = str(tmp_path)
    poner(c, "repo", "hosts_fijos_capa3.json", '[{"ip": "10.0.0.10"}]')
    poner(c, "ctrl-2", "hosts_fijos_capa3.json", '[{"ip": "10.0.0.11"}]')
    a = por_id(ex.exportar(c))["hosts_fijos_capa3.json"]
    assert a["estado"] == "diverge" and a["nota"] is None and len(a["diferencias"]) == 1 and "10.0.0.11" in a["diferencias"][0]["diff"]


def test_un_json_roto_se_compara_byte_a_byte_sin_romper(tmp_path):
    c = str(tmp_path)
    poner(c, "repo", "plan_capa3.json", "{esto no es json")
    poner(c, "ctrl-2", "plan_capa3.json", "{esto no es json")
    poner(c, "ctrl-3", "plan_capa3.json", "{esto tampoco")
    assert por_id(ex.exportar(c))["plan_capa3.json"]["estado"] == "diverge"


UNIDAD = "[Service]\nExecStart=/usr/bin/python3 /opt/vigilante/vigilante.py --subredes /etc/vigilante/plan_capa3.json --salud-puerto 8098 --estable 15 {extra}--aplicar\n"


def test_la_unidad_del_principal_y_la_del_respaldo_solo_difieren_en_respaldo_de_y_eso_no_es_una_divergencia(tmp_path):
    c = str(tmp_path)
    poner(c, "ctrl-3", "vigilante-capa3.service", UNIDAD.format(extra=""))
    poner(c, "ctrl-2", "vigilante-capa3.service", UNIDAD.format(extra="--respaldo-de http://192.168.201.3:8098/salud "))
    a = por_id(ex.exportar(c))["vigilante-capa3.service"]
    assert a["estado"] == "igual" and "--respaldo-de" in a["nota"] and a["diferencias"] == []


def test_si_la_unidad_difiere_en_otra_cosa_si_diverge_aunque_tambien_cambie_respaldo_de(tmp_path):
    c = str(tmp_path)
    poner(c, "ctrl-3", "vigilante-capa3.service", UNIDAD.format(extra=""))
    poner(c, "ctrl-2", "vigilante-capa3.service", UNIDAD.format(extra="--respaldo-de http://192.168.201.3:8098/salud ").replace("--estable 15", "--estable 5"))
    a = por_id(ex.exportar(c))["vigilante-capa3.service"]
    assert a["estado"] == "diverge" and "--estable 5" in a["diferencias"][0]["diff"]


def test_copias_con_los_mismos_bytes_no_llevan_nota(carpeta):
    a = por_id(ex.exportar(carpeta))
    assert a["policy.yaml"]["nota"] is None and a["plan_capa3.json"]["nota"] is None
