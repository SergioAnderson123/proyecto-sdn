"""Rutas de importación: el Policy Mapper (R1) importa policy_tables y portal_auth, que viven en R2/policy."""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
for ruta in (os.path.join(AQUI, ".."), os.path.join(AQUI, "..", "..", "..", "..", "R2", "policy")):
    ruta = os.path.normpath(ruta)
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
