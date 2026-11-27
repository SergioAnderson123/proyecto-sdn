"""Rutas de importación: policy_tables (overlays/r2-policy) puede necesitar el Session Manager,
que vive en apps/onos/policy_mapper."""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
for ruta in (os.path.join(AQUI, ".."), os.path.join(AQUI, "..", "..", "..", "apps", "onos", "policy_mapper")):
    ruta = os.path.normpath(ruta)
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
