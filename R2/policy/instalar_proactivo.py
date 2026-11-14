"""instalar_proactivo.py: instala (o simula) el reenvío proactivo en UN switch, por fases, con comprobaciones que impiden crear una tormenta.

    python instalar_proactivo.py --dispositivo acc-1 --fase B                # simula: no toca nada
    python instalar_proactivo.py --dispositivo acc-1 --fase B --aplicar      # instala la tabla 60 (nada llega a ella todavía)
    python instalar_proactivo.py --dispositivo acc-1 --fase C --aplicar      # la tabla 0 manda el ARP a la tabla 60 (empieza a actuar)
    python instalar_proactivo.py --fase quitar --aplicar                     # borra TODO lo que instaló este script (cualquier switch)

Fase B: tabla 60 (unicast por destino + difusión ARP por árbol + descarte). Ningún paquete llega a la tabla 60 mientras la tabla 0 no
la use, así que no cambia el comportamiento. Fase C: una regla en la tabla 0 (prioridad 40003) que manda el ARP a la tabla 60 en lugar
de al controlador; exige que la fase B ya esté instalada en ese switch. Se usa la API REST de ONOS; todo lo instalado lleva el appId
`r2.proactivo`, así que `--fase quitar` lo deshace por completo.

Requisitos para que no haya tormenta (se comprueban y, si alguno falla, el script se detiene sin instalar):
  - ONOS ve exactamente los enlaces esperados (--enlaces 14 = los 7 cables en los dos sentidos);
  - todos los árboles son válidos (sin ciclos) y ningún flujo generado inunda (NORMAL/FLOOD/ALL);
  - la fase C solo se aplica si la fase B de ese switch ya está en ONOS.
"""
import argparse
import base64
import json
import sys
import urllib.request

import caminos as c
import flujos_proactivos as fp
from plan_arboles import NOMBRES, PREFERIDOS
from vista_previa import hosts_de_onos, puertos_acceso_de

APP_ID = "r2.proactivo"
PRIO_ARP_TABLA0 = 40003          # sobre la sesión (40001) y el portal (40002); bajo el DHCP (40500). Ver R5/deploy/prioridades.yaml


class Detener(Exception):
    """Una comprobación de seguridad falló: no se instala nada."""


def resolver_dispositivo(texto: str) -> str:
    por_nombre = {v: k for k, v in NOMBRES.items()}
    return por_nombre.get(texto, texto)


def flujo_arp_tabla0(dpid: str) -> dict:
    """Tabla 0: todo el ARP va a la tabla 60 (ni al controlador ni a NORMAL)."""
    return {"priority": PRIO_ARP_TABLA0, "timeout": 0, "isPermanent": True, "deviceId": dpid, "tableId": 0,
            "treatment": {"instructions": [{"type": "TABLE", "tableId": fp.TABLA_REENVIO}]},
            "selector": {"criteria": [{"type": "ETH_TYPE", "ethType": "0x0806"}]}}


def construir_plan(links, hosts, acceso, dpid, fase, enlaces_esperados=14):
    """Los flujos que se instalarían en `dpid` en esa fase. Lanza Detener si alguna comprobación de seguridad falla."""
    grafo = c.grafo_desde_links(links)
    dirigidos = sum(len(v) for v in grafo.values())
    if dirigidos != enlaces_esperados:
        raise Detener(f"ONOS ve {dirigidos} enlaces y se esperaban {enlaces_esperados}: un enlace caído convierte sus puertos en 'de acceso' "
                      "y la difusión podría entrar en un lazo. No se instala nada.")
    if dpid not in grafo:
        raise Detener(f"{dpid} no está en la topología de ONOS.")
    for destino in grafo:
        arbol = c.arbol_hacia(grafo, destino, PREFERIDOS)
        if not c.es_arbol(arbol, destino) or len(arbol) != len(grafo) - 1:
            raise Detener(f"El árbol hacia {destino} no es válido.")
    troncales = {(d, p) for d, v in grafo.items() for _, p in v}
    validos = [h for h in hosts if (h["device"], h["port"]) not in troncales]
    todos = (fp.flujos_unicast(validos, grafo, grafo.keys(), PREFERIDOS) + fp.flujos_descarte(grafo.keys())
             + fp.flujos_difusion(grafo, PREFERIDOS[0], acceso, PREFERIDOS))
    flujos = [f for f in todos if f["deviceId"] == dpid]
    if fase == "C":
        flujos = [flujo_arp_tabla0(dpid)]
        todos = todos + flujos
    inunda = c.flujos_que_inundan(todos)
    if inunda:
        raise Detener(f"{len(inunda)} flujos inundarían (NORMAL/FLOOD/ALL). No se instala nada.")
    return flujos


class Onos:
    def __init__(self, url, usuario):
        self.url = url.rstrip("/") + "/onos/v1"
        self.auth = "Basic " + base64.b64encode(usuario.encode()).decode()

    def _pedir(self, metodo, ruta, cuerpo=None):
        req = urllib.request.Request(self.url + ruta, method=metodo, data=None if cuerpo is None else json.dumps(cuerpo).encode(),
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            t = r.read().decode()
            return json.loads(t) if t else {}

    def get(self, ruta):
        return self._pedir("GET", ruta)

    def instalar(self, flujos):
        return self._pedir("POST", f"/flows?appId={APP_ID}", {"flows": flujos})

    def quitar_todo(self):
        return self._pedir("DELETE", f"/flows/application/{APP_ID}")


def fase_b_instalada(onos, dpid, esperados):
    """¿Están ya en ONOS los flujos de la fase B de este switch (mismo appId, tabla 60)?"""
    actuales = [f for f in onos.get(f"/flows/{dpid}").get("flows", [])
                if f.get("tableId") == fp.TABLA_REENVIO and f.get("appId") == APP_ID]
    return len(actuales) >= esperados


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--fase", choices=["B", "C", "quitar"], required=True)
    a.add_argument("--dispositivo", help="nombre (acc-1, acc-2, core-1, core-2, borde) o deviceId; obligatorio salvo en 'quitar'")
    a.add_argument("--onos", default="http://localhost:8181")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--edge", default="edge_ports.txt", help="salida del comando `edge-ports` de la consola de ONOS")
    a.add_argument("--enlaces", type=int, default=14, help="entradas de /links esperadas (7 cables x 2 sentidos)")
    a.add_argument("--aplicar", action="store_true", help="sin esta bandera solo se simula")
    arg = a.parse_args(argv)
    onos = Onos(arg.onos, arg.usuario)
    try:
        if arg.fase == "quitar":
            print("Se borrarían TODOS los flujos del appId", APP_ID)
            if arg.aplicar:
                onos.quitar_todo()
                print("Borrados. Comprueba con el diagnóstico 95.")
            return 0
        if not arg.dispositivo:
            raise Detener("--dispositivo es obligatorio: se instala en un solo switch a la vez.")
        dpid = resolver_dispositivo(arg.dispositivo)
        plan = construir_plan(onos.get("/links")["links"], hosts_de_onos(onos.get("/hosts")), puertos_acceso_de(arg.edge), dpid, arg.fase,
                              arg.enlaces)
        if arg.fase == "C":
            b = construir_plan(onos.get("/links")["links"], hosts_de_onos(onos.get("/hosts")), puertos_acceso_de(arg.edge), dpid, "B", arg.enlaces)
            if not fase_b_instalada(onos, dpid, len(b)):
                raise Detener(f"La fase B de {dpid} no está instalada ({len(b)} flujos en la tabla 60). Instálala antes de la C.")
        print(f"Fase {arg.fase} en {dpid}: {len(plan)} flujos ({'SE INSTALAN' if arg.aplicar else 'simulación'})")
        for f in plan:
            print("  tabla", f["tableId"], "prio", f["priority"], json.dumps(f["selector"]["criteria"]), "->", json.dumps(f["treatment"]["instructions"]))
        if arg.aplicar:
            onos.instalar(plan)
            print("Instalado. Ahora ejecuta el diagnóstico 95 y mira la CPU de ovs-vswitchd.")
        return 0
    except Detener as e:
        print("DETENIDO:", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
