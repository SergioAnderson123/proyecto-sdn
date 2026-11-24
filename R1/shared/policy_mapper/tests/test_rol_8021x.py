"""Rol de una sesión 802.1X sacado de policy.yaml (cuarto sitio de docs/DISENO_EXCEPCIONES_POR_SESION.md 4.7, requisito P11).

Antes el webhook tenía tres `if "docente" in rol`, `"alumno"` y `"soporte"` escritos en el código: un rol nuevo (superadmin) se rechazaba con «Rol sin acceso autorizado» aunque estuviera
en LDAP, RADIUS y policy.yaml."""
import os
import re

import pytest

import policy_mapper as pm
import policy_tables as pt
from session_manager import session_store
import eventos
from test_identidad_clonada import DPID, MAC, USUARIO, _cuerpo, _entrada, _onos_con, cliente  # noqa: F401  (cliente es un fixture)

RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
POLITICA_REPO = pt.cargar_politica(os.path.join(RAIZ, "R2", "policy", "policy.yaml"))


def leer(ruta):
    with open(os.path.join(RAIZ, ruta), encoding="utf8") as f:
        return f.read()


# ---------- la función pura ----------
@pytest.mark.parametrize("rol, esperado", [
    ("docente", (10, "Docente")), ("alumno", (20, "Alumno")), ("soporte", (40, "Administrador")), ("superadmin", (45, "Superadmin")),
])
def test_los_roles_de_radius_dan_la_misma_tabla_y_nombre_que_los_if_de_antes_y_el_superadmin_la_45(rol, esperado):
    assert pm.rol_de_802_1x(POLITICA_REPO, rol) == esperado


@pytest.mark.parametrize("rol", ["Docente", " docente ", "SUPERADMIN", "Superadmin\n"])
def test_no_distingue_mayusculas_ni_espacios(rol):
    assert pm.rol_de_802_1x(POLITICA_REPO, rol) is not None


@pytest.mark.parametrize("rol", ["", "Sin Rol", "invitado", "superadmin2", "xsuperadmin", "super", "docente-admin", "profesor", "student", "administrator", None])
def test_lo_que_no_figura_en_la_politica_se_rechaza_con_coincidencia_exacta(rol):
    assert pm.rol_de_802_1x(POLITICA_REPO, rol) is None          # antes, «docente-admin» o «x-soporte» casaban por subcadena


def test_el_invitado_no_entra_por_802_1x_aunque_este_en_la_politica():
    assert "invitado" in POLITICA_REPO["roles"] and pm.rol_de_802_1x(POLITICA_REPO, "invitado") is None


def test_un_rol_nuevo_en_la_politica_funciona_sin_tocar_el_codigo():
    pol = {"roles": {"auditor": {"matriz": "auditor", "tabla": 47}}}
    assert pm.rol_de_802_1x(pol, "auditor") == (47, "Auditor")
    assert pm.rol_de_802_1x(pol, "docente") is None and pm.rol_de_802_1x({}, "docente") is None


def test_la_politica_puede_poner_el_nombre_que_muestra_el_rol():
    assert pm.rol_de_802_1x({"roles": {"x": {"matriz": "x", "tabla": 47, "nombre": "Equipo X"}}}, "x") == (47, "Equipo X")


# ---------- RADIUS y policy.yaml dicen lo mismo ----------
def _ramas_de_radius():
    texto = leer("R1/radius/raddb/sites-available/default")
    post = texto[texto.index("post-auth {"):]
    bloque = post[:post.index("rest\n")]
    return re.findall(r'LDAP-Group == "(\w+)"\)\s*\{\s*update reply \{[^}]*Tunnel-Private-Group-Id = "(\w+)"', bloque)


def test_cada_rol_que_manda_radius_existe_en_la_politica_y_entra_por_802_1x():
    ramas = _ramas_de_radius()
    assert {g for g, _ in ramas} == {"profesor", "alumno", "soporteTI", "superadmin"}
    for grupo, rol in ramas:
        assert pm.rol_de_802_1x(POLITICA_REPO, rol) is not None, (grupo, rol)


def test_la_rama_superadmin_va_la_ultima_para_que_gane_el_menos_privilegio_si_un_equipo_esta_en_dos_grupos():
    assert [g for g, _ in _ramas_de_radius()][-1] == "superadmin"


def test_el_ldif_trae_la_plantilla_del_superadmin_sin_cargarla():
    ldif = leer("R1/shared/ldap/ldif/custom/estructura.ldif")
    assert "# dn: cn=superadmin,ou=groups" in ldif and "\ndn: cn=superadmin" not in ldif       # solo comentada: falta elegir el equipo


# ---------- el webhook completo ----------
def _entrar(cli, monkeypatch, rol, mac_cabecera=MAC):
    _onos_con([_entrada(f"{DPID}/1", estado="UNAUTHORIZED_STATE")], monkeypatch)
    return cli.post("/webhook", json=_cuerpo(), headers={"X-Rol-LDAP": rol, "X-MAC-LDAP": mac_cabecera, "X-DPID-LDAP": DPID, "X-Port-LDAP": "1"})


def test_un_equipo_superadmin_entra_en_la_tabla_45_con_el_rol_que_el_panel_espera(monkeypatch, cliente):
    cli, _ = cliente
    r = _entrar(cli, monkeypatch, "superadmin")
    assert r.status_code == 200 and r.json()["Tunnel-Private-Group-Id"] == "45"
    ses = session_store.get_by_mac(MAC)
    assert ses.vlan == 45 and ses.rol.lower() == "superadmin"          # R2/panel compara el `rol` de la sesión con `roles_admin` sin distinguir mayúsculas
    assert any(e["tipo"] == "admitido" and e["vlan"] == 45 for e in eventos.listar(5))


@pytest.mark.parametrize("rol, vlan, nombre", [("docente", "10", "Docente"), ("alumno", "20", "Alumno"), ("soporte", "40", "Administrador")])
def test_los_roles_de_siempre_siguen_igual_por_el_webhook(monkeypatch, cliente, rol, vlan, nombre):
    cli, _ = cliente
    r = _entrar(cli, monkeypatch, rol)
    assert r.status_code == 200 and r.json()["Tunnel-Private-Group-Id"] == vlan
    assert session_store.get_by_mac(MAC).rol == nombre


@pytest.mark.parametrize("rol", ["Sin Rol", "invitado", "superadmin2", ""])
def test_un_rol_desconocido_se_rechaza_y_no_instala_sesion(monkeypatch, cliente, rol):
    cli, _ = cliente
    r = _entrar(cli, monkeypatch, rol)
    assert r.status_code == 403 and session_store.get_by_mac(MAC) is None
