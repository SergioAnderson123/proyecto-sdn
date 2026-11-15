"""vigilante.py: vigila la topología de ONOS y, si cae un enlace o un switch (o aparece un host), recalcula la tabla 60 de los switches.

    python3 vigilante.py --acceso /etc/vigilante/acceso.json                  # SOLO AVISA: calcula y escribe lo que haría
    python3 vigilante.py --acceso /etc/vigilante/acceso.json --aplicar        # además cambia los flujos
    python3 vigilante.py --generar-acceso /etc/vigilante/acceso.json          # una vez, con la red completa: guarda los puertos de acceso

Cada ciclo compara los flujos que DEBERÍA haber (con la topología viva que ve ONOS) con los que HAY. Si difieren de forma estable, aplica la
diferencia con "romper antes de montar": primero borra los flujos que sobran y después añade los nuevos (ver recalculo.py).

Cuándo NO hace nada (y lo escribe en el log):
  - existe el archivo interruptor (por defecto /etc/vigilante/desactivar);
  - ningún ONOS responde, o /links viene vacío con switches vivos (ONOS reiniciando: no es una caída de la red);
  - la red quedó partida (algún switch vivo no llega a la raíz);
  - la diferencia aún no lleva `estable_s` segundos sin cambiar, o ya hubo un cambio hace menos de `min_intervalo_s`;
  - el cambio supera `max_cambios` flujos (señal de datos incompletos);
  - sin --aplicar.
Los puertos de acceso salen de un archivo fijo (hecho con la red completa); además, cualquier puerto que alguna vez haya aparecido en un enlace
(activo o no) se trata siempre como troncal: un cable caído NUNCA convierte sus puertos en "de acceso".
"""
import argparse
import json
import os
import sys
import time
import urllib.error

import caminos
import flujos_proactivos as fp
import recalculo as rc
from instalar_proactivo import MONITOREO, puertos_acceso_rest, sin_monitoreo
from onos_multi import OnosCluster, ONOSNoDisponible
from plan_arboles import NOMBRES, PREFERIDOS
from vista_previa import hosts_de_onos

NODOS_POR_DEFECTO = ["http://192.168.201.1:8181", "http://192.168.201.2:8181", "http://192.168.201.3:8181"]     # primero node-ctrl-1


def _nombre(d):
    return NOMBRES.get(d, d)


class Vigilante:
    def __init__(self, onos, preferidos, acceso, aplicar=False, estable_s=15, min_intervalo_s=60, max_cambios=200,
                 interruptor=None, log=print, nombre=_nombre, reloj=time.monotonic, monitoreo=None):
        self.onos, self.preferidos, self.aplicar = onos, list(preferidos), aplicar
        self.estable_s, self.min_intervalo_s, self.max_cambios = estable_s, min_intervalo_s, max_cambios
        self.interruptor, self.log, self.nombre, self.reloj = interruptor, log, nombre, reloj
        self.acceso_fijo = sin_monitoreo(acceso, monitoreo)
        self.troncales = set()                 # (switch, puerto) que alguna vez salió en un enlace
        self.hosts = {}                        # MAC -> host; un host que ONOS deja de listar no se olvida
        self._huella = self._desde = self._ultimo_cambio = None
        self._avisos = set()

    # ---- ayudas ----
    def _aviso(self, clave, texto):
        """Escribe el aviso solo cuando cambia (para no llenar el log cada pocos segundos)."""
        if clave not in self._avisos:
            self._avisos.add(clave)
            self.log(texto)

    def _olvidar_avisos(self, *claves):
        for k in claves:
            self._avisos.discard(k)

    def _reiniciar_espera(self):
        self._huella = self._desde = None

    # ---- un ciclo ----
    def ciclo(self):
        """Hace una comprobación y devuelve una palabra que dice qué pasó (para el log y para las pruebas)."""
        if self.interruptor and os.path.exists(self.interruptor):
            self._aviso("pausado", f"PAUSADO: existe {self.interruptor}; no se hace nada hasta que se borre.")
            return "pausado"
        self._olvidar_avisos("pausado")
        try:
            dispositivos = self.onos.get("/devices")["devices"]
            links = self.onos.get("/links")["links"]
            hosts = hosts_de_onos(self.onos.get("/hosts"))
        except ONOSNoDisponible as e:
            self._aviso("sin-onos", f"AVISO: ningún ONOS responde ({e}). No se toca nada.")
            return "sin ONOS"
        except (urllib.error.HTTPError, ValueError, KeyError) as e:
            self._aviso("error-lectura", f"AVISO: respuesta inesperada de ONOS ({e}). No se toca nada.")
            return "error"
        self._olvidar_avisos("sin-onos", "error-lectura")

        for e in links:                                           # memoria de puertos troncales, activos o no
            self.troncales.add((e["src"]["device"], int(e["src"]["port"])))
            self.troncales.add((e["dst"]["device"], int(e["dst"]["port"])))
        for h in hosts:
            self.hosts[h["mac"].upper()] = h

        if dispositivos and not links:
            self._aviso("sin-enlaces", "AVISO: ONOS lista switches pero ningún enlace (¿reiniciando?). No se toca nada.")
            return "sospechoso"
        self._olvidar_avisos("sin-enlaces")

        grafo = rc.grafo_vivo(links, dispositivos)
        raiz = rc.raiz_viva(grafo, self.preferidos)
        if raiz is None or not rc.esta_conexa(grafo, raiz):
            aislados = sorted(self.nombre(d) for d in grafo if raiz is None or d not in set(caminos.niveles_hacia(grafo, raiz) if raiz is not None else {}))
            self._aviso("partida", f"AVISO: la red está partida (sin camino a la raíz: {', '.join(aislados) or 'todos'}). No se recalcula.")
            self._reiniciar_espera()
            return "partida"
        self._olvidar_avisos("partida")

        acceso = {d: [p for p in ps if (d, p) not in self.troncales] for d, ps in self.acceso_fijo.items() if d in grafo}
        deseados = rc.flujos_deseados(grafo, list(self.hosts.values()), acceso, self.preferidos, raiz)
        try:
            instalados = [f for d in grafo for f in self.onos.flujos_de_app(d, fp.TABLA_REENVIO)]
        except ONOSNoDisponible as e:
            self._aviso("sin-onos", f"AVISO: ningún ONOS responde ({e}). No se toca nada.")
            return "sin ONOS"
        dif = rc.diferencia(deseados, instalados)
        if not dif["borrar"] and not dif["agregar"]:
            if self._huella is not None:
                self.log("OK: los flujos instalados ya coinciden con la topología viva.")
            self._reiniciar_espera()
            self._olvidar_avisos("simulacion", "demasiados", "intervalo")
            return "sin cambios"

        total = len(dif["borrar"]) + len(dif["agregar"])
        if total > self.max_cambios:
            self._aviso("demasiados", f"AVISO: el cambio es de {total} flujos (límite {self.max_cambios}): parece un dato incompleto. No se aplica.")
            return "demasiados cambios"

        huella = hash(frozenset(rc.clave(f) for f in dif["borrar"]) | frozenset(("+",) + rc.clave(f) for f in dif["agregar"]))
        ahora = self.reloj()
        if huella != self._huella:
            self._huella, self._desde = huella, ahora
            self._olvidar_avisos("simulacion", "intervalo")
            self.log(f"CAMBIO DETECTADO ({rc.resumen(dif, self.nombre)}). Raíz: {self.nombre(raiz)}. Espero {self.estable_s}s a que se estabilice.")
            return "esperando"
        if ahora - self._desde < self.estable_s:
            return "esperando"
        if self._ultimo_cambio is not None and ahora - self._ultimo_cambio < self.min_intervalo_s:
            self._aviso("intervalo", f"Espero: hubo un cambio hace menos de {self.min_intervalo_s}s.")
            return "intervalo"
        if not self.aplicar:
            self._aviso("simulacion", f"SIMULACIÓN (sin --aplicar): se cambiaría {rc.resumen(dif, self.nombre)}.")
            return "simulación"
        self._aplicar(dif)
        self._ultimo_cambio = self.reloj()
        self._reiniciar_espera()
        self.log(f"APLICADO: {rc.resumen(dif, self.nombre)}. Raíz: {self.nombre(raiz)}.")
        return "aplicado"

    def _aplicar(self, dif):
        """Borra primero y añade después. Si falla a medias, el siguiente ciclo recalcula la diferencia y termina el trabajo."""
        for tipo, flujos in rc.pasos_de_cambio(dif):
            if tipo == "borrar":
                for f in flujos:
                    try:
                        self.onos.borrar(f["deviceId"], f["id"])
                    except urllib.error.HTTPError as e:
                        if e.code != 404:                          # 404: ya no estaba
                            raise
            else:
                self.onos.instalar(flujos)


def generar_acceso(onos, enlaces_esperados, destino):
    """Guarda los puertos de acceso (sin los de monitoreo). Exige la red completa: con un cable caído sus puertos parecerían de acceso."""
    links = onos.get("/links")["links"]
    dirigidos = sum(len(v) for v in caminos.grafo_desde_links(links).values())
    if dirigidos != enlaces_esperados:
        raise SystemExit(f"ONOS ve {dirigidos} enlaces y se esperaban {enlaces_esperados}: no se genera {destino} con la red incompleta.")
    acceso = sin_monitoreo(puertos_acceso_rest(onos, links))
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(acceso, f, indent=1, sort_keys=True)
    return acceso


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--onos", default=",".join(NODOS_POR_DEFECTO), help="URLs de ONOS separadas por comas; se prueban en este orden")
    a.add_argument("--usuario", default="onos:rocks")
    a.add_argument("--acceso", default="/etc/vigilante/acceso.json", help="puertos de acceso (hecho con --generar-acceso)")
    a.add_argument("--generar-acceso", metavar="ARCHIVO")
    a.add_argument("--enlaces", type=int, default=14, help="entradas de /links con la red completa (solo para --generar-acceso)")
    a.add_argument("--intervalo", type=float, default=5, help="segundos entre comprobaciones")
    a.add_argument("--estable", type=float, default=15, help="segundos que debe durar una diferencia antes de actuar")
    a.add_argument("--min-intervalo", type=float, default=60, help="segundos mínimos entre dos cambios")
    a.add_argument("--max-cambios", type=int, default=200)
    a.add_argument("--interruptor", default="/etc/vigilante/desactivar")
    a.add_argument("--aplicar", action="store_true", help="sin esta bandera solo avisa")
    a.add_argument("--una-vez", action="store_true", help="hace un solo ciclo y termina (para pruebas)")
    arg = a.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    onos = OnosCluster([u.strip() for u in arg.onos.split(",") if u.strip()], arg.usuario)

    def log(texto):
        print(time.strftime("%H:%M:%S"), texto, flush=True)

    if arg.generar_acceso:
        acc = generar_acceso(onos, arg.enlaces, arg.generar_acceso)
        log(f"Puertos de acceso guardados en {arg.generar_acceso}: { {_nombre(d): p for d, p in acc.items()} }")
        return 0
    try:
        with open(arg.acceso, encoding="utf-8") as f:
            acceso = json.load(f)
    except OSError:
        acceso = None
    if not acceso:
        if arg.aplicar:
            log(f"NO ARRANCO con --aplicar: falta {arg.acceso}. Créalo con --generar-acceso (con la red completa).")
            return 2
        log(f"AVISO: no hay {arg.acceso}; sin él no se pueden calcular los flujos de difusión. Solo se vigila la conexión.")
        acceso = {}
    v = Vigilante(onos, PREFERIDOS, acceso, aplicar=arg.aplicar, estable_s=arg.estable, min_intervalo_s=arg.min_intervalo,
                  max_cambios=arg.max_cambios, interruptor=arg.interruptor, log=log)
    log(f"Vigilante iniciado: nodos {arg.onos}; {'APLICA CAMBIOS' if arg.aplicar else 'solo avisa'}; cada {arg.intervalo}s.")
    try:
        while True:
            try:
                estado = v.ciclo()
            except Exception as e:  # noqa: BLE001  - un fallo inesperado no debe matar al servicio
                log(f"ERROR inesperado en el ciclo: {e!r}")
                estado = "error"
            if arg.una_vez:
                log(f"estado: {estado}")
                return 0
            time.sleep(arg.intervalo)
    except KeyboardInterrupt:
        log("Detenido.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
