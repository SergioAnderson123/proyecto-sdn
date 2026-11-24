"""Rutas de importación: el Policy Mapper (R1) importa policy_tables y portal_auth, que viven en R2/policy."""
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
for ruta in (os.path.join(AQUI, ".."), os.path.join(AQUI, "..", "..", "..", "..", "R2", "policy")):
    ruta = os.path.normpath(ruta)
    if ruta not in sys.path:
        sys.path.insert(0, ruta)


@pytest.fixture(autouse=True)
def limites_de_intentos_del_portal_limpios():
    """Los límites de intentos fallidos de /portal/login son estado de módulo (como en producción): sin esto, las contraseñas malas de unas pruebas
    bloquearían a las siguientes (mismo usuario y misma MAC). Cada prueba parte de cuentas y equipos sin fallas."""
    pm = sys.modules.get("policy_mapper")
    if pm is not None and hasattr(pm, "_limite_portal"):
        pm.LIMITE_PORTAL_CUENTA = pm._limite_portal("PORTAL_MAX_INTENTOS", "5")
        pm.LIMITE_PORTAL_EQUIPO = pm._limite_portal("PORTAL_MAX_INTENTOS_EQUIPO", "15")
    yield
