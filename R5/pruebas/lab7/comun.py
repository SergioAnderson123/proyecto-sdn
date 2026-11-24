"""Utilidades compartidas por las pruebas del Lab 7 de R5 (r5_01.py ... r5_06.py).

Cada prueba es un archivo aparte que se corre solo, desde la raiz del repositorio:   python3 R5/pruebas/lab7/r5_01.py
Muestra, caso por caso, la ENTRADA, lo ESPERADO, lo OBTENIDO y cada verificacion. No necesita ONOS ni Suricata:
ONOS se sustituye por un doble (OnosFalso) que registra las peticiones. Codigo de salida: 0 si todos los casos pasan.
"""
import importlib
import json
import logging
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))          # .../R5
sys.path.insert(0, os.path.join(RAIZ, "codigo"))
os.environ.update({"ONOS_URL": "http://onos.test", "ONOS_USER": "u", "ONOS_PASS": "p", "HOME_NET": "10.0.0.0/24",
                   "LISTA_BLANCA": "10.0.0.99/32,10.0.0.10/32", "CORE_DEVICE": "of:0000000000000001", "MAX_SEVERITY": "2",
                   "P_R5": "42000", "BLOCK_SEC": "300"})
integ = importlib.import_module("integrador")
logging.disable(logging.CRITICAL)                          # el log del integrador se omite: lo relevante se muestra en cada caso

ANCHO = 100
RESULTADOS = []


class Resp:
    def __init__(self, cuerpo=None, codigo=200):
        self._c, self.status_code, self.text = cuerpo or {}, codigo, json.dumps(cuerpo or {})

    def json(self):
        return self._c


class OnosFalso:
    """Doble de ONOS: registra las peticiones y responde lo minimo para que el integrador funcione."""

    def __init__(self):
        self.llamadas = []

    def __call__(self, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta))
        if metodo == "GET" and ruta == "/hosts":
            return Resp({"hosts": []})
        if metodo == "POST" and ruta == "/flows":
            dev = kw["json"]["flows"][0]["deviceId"]
            return Resp({"flows": [{"deviceId": dev, "flowId": "777"}]})
        return Resp()

    def posts(self):
        return [c for c in self.llamadas if c[0] == "POST"]


def estado():
    return {"bloqueadas": {}, "inode": None, "offset": 0}


def recortar(texto, n=ANCHO):
    texto = str(texto).replace("\n", "\\n")
    return texto if len(texto) <= n else texto[:n - 3] + "..."


def mostrar(valor):
    """Un valor de entrada o salida en texto legible: JSON con sangria si lo es, repr() en otro caso."""
    if isinstance(valor, str):
        try:
            return json.dumps(json.loads(valor), indent=4, ensure_ascii=False, sort_keys=False)
        except ValueError:
            return repr(valor)
    if isinstance(valor, dict):
        return json.dumps(valor, indent=4, ensure_ascii=False, default=str)
    return repr(valor)


def registro(st):
    """Lo que el integrador anota por cada IP bloqueada (dispositivo, flow_id, firma), una linea por IP."""
    return "\n".join(f"{ip} -> device={v['device']}, flow_id={v['flow_id']}, sid={v['sid']}, firma={v['firma']!r}"
                     for ip, v in sorted(st["bloqueadas"].items())) or "(ninguna IP bloqueada)"


def sangrar(texto, n=14):
    return ("\n" + " " * n).join(str(texto).splitlines())


def caso(codigo, titulo, funcion, entrada, esperado, obtenido, verificaciones):
    """Imprime un caso y devuelve True si todas sus verificaciones son ciertas."""
    ok = all(v for _, v in verificaciones)
    RESULTADOS.append((codigo, ok))
    print("-" * ANCHO)
    print(f"[{codigo}] {titulo}")
    print(f"  Funcion   : {funcion}")
    print(f"  Entrada   : {sangrar(entrada)}")
    print(f"  Esperado  : {sangrar(esperado)}")
    print(f"  Obtenido  : {sangrar(obtenido)}")
    print("  Verificaciones:")
    for texto, valor in verificaciones:
        print(f"    [{'OK' if valor else 'FALLA'}] {texto}")
    print(f"  RESULTADO {codigo}: {'PASS' if ok else 'FAIL'}")
    return ok


def terminar(cod):
    """Imprime el resumen y devuelve el codigo de salida (0 si todos pasan)."""
    total = len(RESULTADOS)
    bien = sum(1 for _, ok in RESULTADOS if ok)
    print("=" * ANCHO)
    print(f"RESUMEN {cod}: {bien} de {total} casos PASS" + ("" if bien == total else f"; FALLAN: {[c for c, ok in RESULTADOS if not ok]}"))
    return 0 if bien == total else 1
