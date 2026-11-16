"""El playbook 65 copia una lista fija de archivos al servidor del vigilante: debe incluir todo lo que el vigilante importa (regresion del 16-nov: faltaba capa3.py)."""
import os
import re

import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
CARPETA = os.path.dirname(AQUI)
PLAYBOOK = os.path.join(CARPETA, "..", "..", "R1", "deploy", "topologia14", "65_vigilante.yml")


def _importados(modulo):
    with open(os.path.join(CARPETA, modulo + ".py"), encoding="utf-8") as f:
        texto = f.read()
    return {m for m in re.findall(r"^\s*(?:import|from)\s+([a-z_0-9]+)", texto, re.M) if os.path.exists(os.path.join(CARPETA, m + ".py"))}


def _cierre(raiz):
    visto, pendientes = set(), [raiz]
    while pendientes:
        m = pendientes.pop()
        if m not in visto:
            visto.add(m)
            pendientes += _importados(m)
    return visto


def test_el_playbook_65_copia_todo_lo_que_el_vigilante_importa():
    with open(PLAYBOOK, encoding="utf-8") as f:
        juego = yaml.safe_load(f)[0]
    copiados = {a[:-3] for a in juego["vars"]["archivos"]}
    assert _cierre("vigilante") <= copiados, sorted(_cierre("vigilante") - copiados)
