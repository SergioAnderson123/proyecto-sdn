"""Lab 7 (TEL354): pruebas unitarias comunes de R1 (control de acceso por rol) sobre el portal (R2/policy/portal_auth.py), /portal/login y intentos.py.

Numeración X.Y.Z del informe: módulo 1 = autenticación (M1); sección 1.N = prueba R1-0N; Z = caso.
  R1-01 credenciales válidas -> 1.1.x   R1-02 credenciales inválidas -> 1.2.x   R1-03 bloqueo por intentos fallidos -> 1.3.x
El LDAP es el servidor simulado en memoria de la propia librería ldap3 (MOCK_SYNC): se ejecutan de verdad el bind, el escape de DN y de filtros.
R1-01 y R1-02 se omiten si faltan `ldap3` o `fastapi`; R1-03 no los necesita.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intentos  # noqa: E402  (R1-03 es lógica pura: corre en cualquier parte)

try:                                   # R1-01 y R1-02 necesitan ldap3 y fastapi; sin ellos esas pruebas se omiten
    import ldap3
    from fastapi.testclient import TestClient
    import eventos
    import policy_mapper as pm
    import policy_tables
    import portal_auth
    from session_manager import session_store
    HAY_DEPENDENCIAS = True
except ImportError:
    HAY_DEPENDENCIAS = False

BASE = portal_auth.LDAP_BASE if HAY_DEPENDENCIAS else "dc=example,dc=org"
MAC = "fa:16:3e:b6:96:08"
DPID = "of:0000e6925d87354b"
USUARIOS = {                      # uid -> (clave, grupo)
    "profesor01": ("Clave-prof-01", "profesor"),
    "invitado01": ("Clave-inv-01", "invitado"),
    "alumno02": ("Clave-alu-02", "alumno"),       # grupo sin rol en el portal
}
MENSAJE = "Usuario o contraseña incorrectos"


@pytest.fixture
def ldap(monkeypatch):
    """Directorio LDAP simulado con tres usuarios y sus grupos; ldap3.Connection apunta a él."""
    if not HAY_DEPENDENCIAS:
        pytest.skip("faltan ldap3 y/o fastapi")
    srv = ldap3.Server("ldap-simulado")
    real = ldap3.Connection
    semilla = real(srv, user=f"cn=admin,{BASE}", password="adm", client_strategy=ldap3.MOCK_SYNC)
    semilla.strategy.add_entry(f"cn=admin,{BASE}", {"userPassword": "adm", "objectClass": "person"})
    for uid, (clave, grupo) in USUARIOS.items():
        dn = f"uid={uid},ou=users,{BASE}"
        semilla.strategy.add_entry(dn, {"userPassword": clave, "objectClass": "inetOrgPerson", "uid": uid})
        semilla.strategy.add_entry(f"cn={grupo},ou=groups,{BASE}", {"objectClass": "groupOfNames", "cn": grupo, "member": [dn]})
    estado = {"caido": False}

    def fabrica(server, user=None, password=None, **kw):
        if estado["caido"]:
            raise ldap3.core.exceptions.LDAPSocketOpenError("LDAP caído")
        kw.pop("receive_timeout", None)
        auto = kw.pop("auto_bind", False)
        con = real(srv, user=user, password=password, client_strategy=ldap3.MOCK_SYNC, **kw)
        if auto:
            con.bind()
        return con

    monkeypatch.setattr(ldap3, "Connection", fabrica)
    monkeypatch.setattr(portal_auth, "LDAP_ADMIN_PASSWORD", "adm")
    return estado


@pytest.fixture
def cliente(ldap, monkeypatch):
    """Cliente HTTP del Policy Mapper sin ONOS: solo lo imprescindible para llegar hasta /portal/login."""
    async def ubicar(mac, onos_url, auth):
        return DPID, "1"

    async def inyectar(dpid, port, mac, vlan):
        return "flujo-1"

    async def sin_faltantes(*a, **k):
        return []

    monkeypatch.setattr(pm.portal_auth, "ubicar_host", ubicar)
    monkeypatch.setattr(pm, "inyectar_flujo_tabla_0", inyectar)
    monkeypatch.setattr(pm.policy_tables, "faltantes", sin_faltantes)
    monkeypatch.setattr(pm, "PORTAL_TOKEN", "")
    session_store.remove_session(MAC)
    eventos.limpiar()
    pm._candados_por_usuario.clear()
    yield TestClient(pm.app)
    session_store.remove_session(MAC)


def _login(cliente, usuario, clave, mac=MAC):
    return cliente.post("/portal/login", json={"usuario": usuario, "clave": clave, "mac": mac})


# =====================================================================================================
# R1-01 (1.1.x) Usuario registrado y activo con credenciales correctas: se autentica y recibe su rol.
# =====================================================================================================
def test_R1_01_1_1_1_un_profesor_se_autentica_y_obtiene_el_rol_docente(ldap):
    grupos = portal_auth.autenticar_ldap("profesor01", USUARIOS["profesor01"][0])
    assert grupos == ["profesor"]
    assert portal_auth.rol_de_grupos(policy_tables.cargar_politica(), grupos) == "docente"


def test_R1_01_1_1_2_un_invitado_se_autentica_y_obtiene_el_rol_invitado(ldap):
    grupos = portal_auth.autenticar_ldap("invitado01", USUARIOS["invitado01"][0])
    assert portal_auth.rol_de_grupos(policy_tables.cargar_politica(), grupos) == "invitado"


def test_R1_01_1_1_3_el_login_del_invitado_instala_su_sesion_con_su_identificador_y_rol(cliente):
    r = _login(cliente, "invitado01", USUARIOS["invitado01"][0])
    assert r.status_code == 200 and r.json()["usuario"] == "invitado01" and r.json()["rol"] == "Invitado"
    ses = session_store.get_by_mac(MAC)
    assert ses is not None and ses.username == "invitado01" and ses.rol == "Invitado"


def test_R1_01_1_1_4_un_usuario_valido_cuyo_grupo_no_tiene_rol_en_el_portal_no_recibe_acceso(cliente):
    r = _login(cliente, "alumno02", USUARIOS["alumno02"][0])
    assert r.status_code == 403 and session_store.get_by_mac(MAC) is None


def test_R1_01_1_1_5_un_docente_no_entra_directo_desde_un_equipo_sin_sesion(cliente):
    # Regla de R1/R2: el profesor solo sube un equipo ya autenticado por 802.1X (queda atado a un equipo con certificado).
    r = _login(cliente, "profesor01", USUARIOS["profesor01"][0])
    assert r.status_code == 403 and session_store.get_by_mac(MAC) is None


# =====================================================================================================
# R1-02 (1.2.x) Credenciales inválidas: se rechazan sin romper nada, con el mismo mensaje y sin asignar rol.
# =====================================================================================================
INVALIDAS = [
    ("profesor01", "contraseña-incorrecta"),            # clave mala
    ("no-existe", "Clave-prof-01"),                     # usuario inexistente
    ("", ""),                                           # campos vacíos
    ("profesor01", ""),                                 # bind anónimo "con éxito" si no se filtrara
    ("", "Clave-prof-01"),
    ("' OR '1'='1", "' OR '1'='1"),                     # inyección SQL clásica
    ("profesor01' OR '1'='1", "x"),
    ("*", "*"),                                         # comodín LDAP
    ("*)(uid=*", "x"),                                  # inyección de filtro LDAP
    ("profesor01)(|(uid=*", "Clave-prof-01"),           # inyección con clave válida de otro usuario
    ("profesor01,ou=users,dc=example,dc=org", "x"),     # intento de manipular el DN
    ("profesor01,ou=users,dc=example,dc=org", "Clave-prof-01"),   # DN manipulado con la clave CORRECTA de otra cuenta
    ("profesor01\x00", "Clave-prof-01"),                # byte nulo al final del usuario
    ("profesor01 ", "Clave-prof-01"),                   # espacio sobrante: no es el mismo usuario
]


@pytest.mark.parametrize("usuario,clave", INVALIDAS)
def test_R1_02_1_2_1_cada_intento_invalido_devuelve_none_sin_excepcion(ldap, usuario, clave):
    assert portal_auth.autenticar_ldap(usuario, clave) is None


@pytest.mark.parametrize("usuario,clave", INVALIDAS)
def test_R1_02_1_2_2_el_endpoint_rechaza_sin_asignar_rol_ni_sesion(cliente, usuario, clave):
    r = _login(cliente, usuario, clave)
    assert r.status_code == 401 and session_store.get_by_mac(MAC) is None
    assert "rol" not in r.json()


def test_R1_02_1_2_3_el_mensaje_de_rechazo_es_el_mismo_exista_o_no_el_usuario(cliente):
    detalles = {_login(cliente, u, c).json()["detail"] for u, c in INVALIDAS}
    assert detalles == {MENSAJE}                         # una sola respuesta: no revela qué usuarios existen


def test_R1_02_1_2_4_si_el_ldap_esta_caido_se_rechaza_y_no_se_abre_el_acceso(cliente, ldap):
    ldap["caido"] = True
    r = _login(cliente, "invitado01", USUARIOS["invitado01"][0])
    assert r.status_code == 401 and session_store.get_by_mac(MAC) is None


def test_R1_02_1_2_5_cada_rechazo_queda_registrado_como_evento(cliente):
    _login(cliente, "profesor01", "mala")
    assert any(e.get("tipo") == "rechazado" or e.get("evento") == "rechazado" for e in eventos.listar(10))


# =====================================================================================================
# R1-03 (1.3.x) Intentos fallidos seguidos: bloqueo temporal al llegar al límite N.
# =====================================================================================================
class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def lim():
    r = Reloj()
    return intentos.LimiteIntentos(max_intentos=5, ventana_s=300, bloqueo_s=900, reloj=r), r


def test_R1_03_1_3_1_con_n_menos_uno_fallos_la_cuenta_sigue_habilitada(lim):
    l, _ = lim
    for _ in range(4):
        assert l.register_failed_attempt("profesor01") is False
    assert l.is_locked("profesor01") is False


def test_R1_03_1_3_2_al_llegar_a_n_fallos_la_cuenta_se_bloquea(lim):
    l, _ = lim
    resultados = [l.register_failed_attempt("profesor01") for _ in range(5)]
    assert resultados == [False, False, False, False, True] and l.is_locked("profesor01") is True


def test_R1_03_1_3_3_el_bloqueo_dura_el_tiempo_establecido_y_luego_se_levanta(lim):
    l, reloj = lim
    for _ in range(5):
        l.register_failed_attempt("profesor01")
    reloj.t += 899
    assert l.is_locked("profesor01") is True and 0 < l.segundos_restantes("profesor01") <= 1
    reloj.t += 2
    assert l.is_locked("profesor01") is False
    assert l.register_failed_attempt("profesor01") is False        # cuenta nueva: empieza de cero


def test_R1_03_1_3_4_un_acceso_correcto_reinicia_la_cuenta(lim):
    l, _ = lim
    for _ in range(4):
        l.register_failed_attempt("profesor01")
    l.register_success("profesor01")
    for _ in range(4):
        assert l.register_failed_attempt("profesor01") is False


def test_R1_03_1_3_5_las_fallas_fuera_de_la_ventana_no_se_acumulan(lim):
    l, reloj = lim
    for _ in range(4):
        l.register_failed_attempt("profesor01")
    reloj.t += 301                                                   # la ventana de 300 s ya pasó
    assert l.register_failed_attempt("profesor01") is False and l.is_locked("profesor01") is False


def test_R1_03_1_3_6_el_bloqueo_de_un_usuario_no_afecta_a_otro(lim):
    l, _ = lim
    for _ in range(5):
        l.register_failed_attempt("profesor01")
    assert l.is_locked("invitado01") is False


def test_R1_03_1_3_7_el_nombre_se_normaliza_y_un_usuario_inexistente_tambien_se_bloquea(lim):
    l, _ = lim
    for u in ("Profesor01", " profesor01", "PROFESOR01", "profesor01", "profesor01 "):
        l.register_failed_attempt(u)
    assert l.is_locked("profesor01") is True
    for _ in range(5):
        l.register_failed_attempt("no-existe")                      # mismo trato: no se revela quién existe
    assert l.is_locked("no-existe") is True


def test_R1_03_1_3_8_un_usuario_bloqueado_no_alarga_su_bloqueo_con_mas_fallos(lim):
    l, reloj = lim
    for _ in range(5):
        l.register_failed_attempt("profesor01")
    reloj.t += 600
    assert l.register_failed_attempt("profesor01") is True
    reloj.t += 301                                                   # 901 s desde el bloqueo original
    assert l.is_locked("profesor01") is False


@pytest.mark.parametrize("kw", [{"max_intentos": 0}, {"ventana_s": 0}, {"bloqueo_s": -1}])
def test_R1_03_1_3_9_parametros_invalidos_se_rechazan(kw):
    with pytest.raises(ValueError):
        intentos.LimiteIntentos(**kw)
