"""Rutas de importación: policy_tables (R2) usa el Session Manager, que vive en R1/shared/policy_mapper."""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
for ruta in (os.path.join(AQUI, ".."), os.path.join(AQUI, "..", "..", "..", "R1", "shared", "policy_mapper")):
    ruta = os.path.normpath(ruta)
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
