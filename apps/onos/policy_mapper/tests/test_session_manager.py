"""SessionManager: get_by_username debe encontrar lo que register_session acaba de registrar

(no dependía de nada más: antes del arreglo, _sessions_by_user nunca se llenaba y esta consulta
siempre devolvía None, enmascarado en el resto del código porque siempre había también un MAC con el que buscar).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_manager import ActiveSession, SessionManager  # noqa: E402


def test_get_by_username_encuentra_lo_registrado():
    m = SessionManager()
    m.register_session(ActiveSession("Profesor01@example.org", "00:00:00:00:00:11", "d", "1", 10, "Docente"))
    assert m.get_by_username("profesor01@example.org") is not None   # sin distinguir mayúsculas
    assert m.get_by_username("profesor01@example.org").mac == "00:00:00:00:00:11"
    assert m.get_by_username("otro@example.org") is None


def test_remove_session_tambien_limpia_el_indice_por_usuario():
    m = SessionManager()
    m.register_session(ActiveSession("profesor01@example.org", "00:00:00:00:00:11", "d", "1", 10, "Docente"))
    m.remove_session("00:00:00:00:00:11")
    assert m.get_by_username("profesor01@example.org") is None


def test_remove_session_no_borra_un_usuario_que_ya_fue_reemplazado():
    # mismo usuario, registrado de nuevo con otra MAC (otra sesión más reciente) antes de que se borre la vieja
    m = SessionManager()
    m.register_session(ActiveSession("profesor01@example.org", "00:00:00:00:00:11", "d", "1", 10, "Docente"))
    m.register_session(ActiveSession("profesor01@example.org", "00:00:00:00:00:12", "d", "2", 10, "Docente"))
    m.remove_session("00:00:00:00:00:11")        # borrar la sesión vieja no debe tocar el índice de la nueva
    assert m.get_by_username("profesor01@example.org").mac == "00:00:00:00:00:12"
