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
Espera corta ante una CAIDA (opcional): si entre dos ciclos se pierde un cable o un switch, lo que lo usaba ya no funciona y la diferencia se aplica tras `estable_caida_s`
(y respetando `min_intervalo_caida_s`) en vez de `estable_s` y `min_intervalo_s`. Una SUBIDA no es urgente (los caminos actuales siguen funcionando), asi que sigue con la espera
larga, que es la que protege contra un cable que parpadea. Sin esos parametros todo funciona exactamente como antes.
Memoria de hosts: un host que ONOS deja de listar NO se olvida (ONOS puede perder hosts un momento, p. ej. al reiniciar, y sus reglas no deben desaparecer). Solo se olvida en dos casos:
  - en capa 3, cuando OTRO host que ONOS si lista reclama la misma IP (la IP se reasigno: dos hosts recordados con la misma IP darian dos reglas /32 iguales para MAC distintas);
  - opcional, `olvidar_host_s`: el host lleva ese tiempo sin aparecer en ONOS (0 o sin valor = nunca). Los hosts fijos nunca se olvidan.
Los puertos de acceso salen de un archivo fijo (hecho con la red completa); además, cualquier puerto que alguna vez haya aparecido en un enlace
(activo o no) se trata siempre como troncal: un cable caído NUNCA convierte sus puertos en "de acceso".
"""
import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import caminos
import capa3
import estado
import flujos_proactivos as fp
import hosts_fijos
import recalculo as rc
from instalar_proactivo import MONITOREO, puertos_acceso_rest, sin_monitoreo
from onos_multi import OnosCluster, ONOSNoDisponible
from plan_arboles import NOMBRES, PREFERIDOS
from vista_previa import hosts_de_onos

NODOS_POR_DEFECTO = ["http://192.168.201.1:8181", "http://192.168.201.2:8181", "http://192.168.201.3:8181"]     # primero node-ctrl-1


def _nombre(d):
    return NOMBRES.get(d, d)


# ---- respaldo: salud del principal ----
def servir_salud(puerto, info, estado_json=None):
    """Servidor HTTP mínimo (GET /salud -> JSON de `info()`; GET /estado -> JSON de `estado_json()` si se da, para el panel), en un hilo. Devuelve el servidor; `puerto=0` elige uno libre (para las pruebas)."""
    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if estado_json is not None and self.path.split("?")[0] == "/estado":
                try:
                    cuerpo, codigo = json.dumps(estado_json(), ensure_ascii=False).encode("utf-8"), 200
                except Exception as e:  # noqa: BLE001  - el panel no debe poder romper al vigilante
                    cuerpo, codigo = json.dumps({"error": repr(e)}).encode(), 500
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)
                return
            cuerpo = json.dumps(info()).encode()
            self.send_response(200 if self.path.startswith("/salud") else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *args):                          # no ensuciar el journal con cada consulta
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def consultar_salud(url, timeout=3, max_edad_s=30):
    """¿Está vivo el principal? Responde y su último ciclo es reciente. Cualquier error cuenta como 'no responde'."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            datos = json.loads(r.read().decode())
        ultimo = datos.get("ultimo_ciclo")
        return ultimo is not None and time.time() - float(ultimo) <= max_edad_s
    except Exception:  # noqa: BLE001
        return False


class Vigilante:
    def __init__(self, onos, preferidos, acceso, aplicar=False, estable_s=15, min_intervalo_s=60, max_cambios=200,
                 interruptor=None, log=print, nombre=_nombre, reloj=time.monotonic, monitoreo=None, salud_principal=None, muerto_s=45, hosts_fijos=(), copiar_arp=False, plan=None, nucleo=None, tabla=fp.TABLA_REENVIO, retirar=False,
                 estable_caida_s=None, min_intervalo_caida_s=None, olvidar_host_s=None):
        self.onos, self.preferidos, self.aplicar = onos, list(preferidos), aplicar
        self.estable_s, self.min_intervalo_s, self.max_cambios = estable_s, min_intervalo_s, max_cambios
        # Ante una CAIDA (un cable o un switch menos desde el ciclo anterior) lo que lo usaba ya no funciona: se puede actuar antes y sin esperar el intervalo largo. Sin valor, igual que los generales.
        self.estable_caida_s = estable_s if estable_caida_s is None else estable_caida_s
        self.min_intervalo_caida_s = min_intervalo_s if min_intervalo_caida_s is None else min_intervalo_caida_s
        self._enlaces_prev, self._caida_pendiente = None, False        # enlaces vivos del ciclo anterior; ¿hubo una caida desde el ultimo cambio o desde que no hay diferencia?
        self.interruptor, self.log, self.nombre, self.reloj = interruptor, log, nombre, reloj
        self.acceso_fijo = sin_monitoreo(acceso or {}, monitoreo)        # capa 3 no necesita los puertos de acceso (acceso.json); sin el archivo llega None
        self.troncales = set()                 # (switch, puerto) que alguna vez salió en un enlace
        self.tabla, self.retirar = tabla, retirar         # tabla != 60: MODO AISLADO (solo con plan): trabaja en esa tabla y en sus reglas de entrada, nunca en la 60; retirar = dejar vacío lo que puso
        self.plan, self.nucleo = plan, (list(nucleo) if nucleo else None)     # capa 3: plan de subredes (capa3.Plan) y switches con rutas completas; sin plan, capa 2
        self.hosts_fijos, self.copiar_arp = list(hosts_fijos), copiar_arp     # hosts que ONOS no aprende solos; copia del ARP de acceso al controlador
        self.hosts = {}                        # MAC -> host; un host que ONOS deja de listar no se olvida, salvo los casos de _olvidar_hosts
        self.olvidar_host_s = olvidar_host_s or 0
        self._visto = {}                       # MAC -> ultima vez (reloj) que ONOS lo listo
        self._huella = self._desde = self._ultimo_cambio = None
        self._avisos = set()
        self.salud_principal, self.muerto_s = salud_principal, muerto_s     # solo en el respaldo: callable() -> ¿responde el principal?
        self._activo = salud_principal is None                             # el principal siempre actúa; el respaldo, solo si el principal se calla
        self._ultimo_ok = None
        self.ultimo_ciclo = None                                           # hora (epoch) del último ciclo: lo publica /salud
        self.ultimo_estado = "iniciando"
        self.historial = estado.Historial()                                # eventos para el panel (GET /estado): solo se registran, no deciden nada
        self._conocidos, self._foto_prev, self._topologia, self._partida_vista = {}, None, None, False

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
        self._caida_pendiente = False

    def _soy_activo(self):
        """El principal siempre actúa. El respaldo solo si el principal lleva `muerto_s` segundos sin responder; en cuanto vuelve, cede."""
        if self.salud_principal is None:
            return True
        ahora = self.reloj()
        if self.salud_principal():
            self._ultimo_ok = ahora
            if self._activo:
                self.log("El principal volvió a responder: dejo el mando y paso a espera.")
                self._activo = False
                self._reiniciar_espera()
            return False
        if self._ultimo_ok is None:
            self._ultimo_ok = ahora                       # empieza a contar desde que lo vimos callado por primera vez
        if not self._activo and ahora - self._ultimo_ok >= self.muerto_s:
            self.log(f"El principal no responde desde hace {int(ahora - self._ultimo_ok)} s: asumo el mando.")
            self._activo = True
        return self._activo

    # ---- un ciclo ----
    def ciclo(self):
        """Hace una comprobación y devuelve una palabra que dice qué pasó (para el log y para las pruebas)."""
        self.ultimo_ciclo = time.time()
        if self.interruptor and os.path.exists(self.interruptor):
            self._aviso("pausado", f"PAUSADO: existe {self.interruptor}; no se hace nada hasta que se borre.")
            return "pausado"
        self._olvidar_avisos("pausado")
        if not self._soy_activo():
            return "en espera"
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
        ahora_hosts = self.reloj()
        for h in hosts:
            self.hosts[h["mac"].upper()] = h
            self._visto[h["mac"].upper()] = ahora_hosts
        for h in self.hosts_fijos:                                # los fijos mandan sobre lo que diga ONOS
            self.hosts[h["mac"].upper()] = h
        self._olvidar_hosts(hosts, ahora_hosts)

        if dispositivos and not links:
            self._aviso("sin-enlaces", "AVISO: ONOS lista switches pero ningún enlace (¿reiniciando?). No se toca nada.")
            return "sospechoso"
        self._olvidar_avisos("sin-enlaces")

        grafo = rc.grafo_vivo(links, dispositivos)
        raiz = rc.raiz_viva(grafo, self.preferidos)
        self._registrar_topologia(grafo, dispositivos, raiz)
        if raiz is None or not rc.esta_conexa(grafo, raiz):
            aislados = sorted(self.nombre(d) for d in grafo if raiz is None or d not in set(caminos.niveles_hacia(grafo, raiz) if raiz is not None else {}))
            self._aviso("partida", f"AVISO: la red está partida (sin camino a la raíz: {', '.join(aislados) or 'todos'}). No se recalcula.")
            if not self._partida_vista:
                self._partida_vista = True
                self.historial.agregar(time.time(), "red_partida", f"Red partida: sin camino a la raíz ({', '.join(aislados) or 'todos'}); no se recalcula")
            self._reiniciar_espera()
            return "partida"
        self._olvidar_avisos("partida")
        if self._partida_vista:
            self._partida_vista = False
            self.historial.agregar(time.time(), "red_conexa", "La red volvió a estar conexa")

        enlaces = frozenset((d, vecino, puerto) for d, vecinos in grafo.items() for vecino, puerto in vecinos)
        if self._enlaces_prev is not None and self._enlaces_prev - enlaces:
            self._caida_pendiente = True              # se perdio un cable (o un switch) desde el ciclo anterior
        self._enlaces_prev = enlaces

        acceso = {d: [p for p in ps if (d, p) not in self.troncales] for d, ps in self.acceso_fijo.items() if d in grafo}
        if self.plan:                                             # capa 3: rutas por prefijo, entrega por host y ARP local (el ARP del gateway es estático, no lo gestiona el vigilante)
            deseados = [] if self.retirar else rc.flujos_deseados_capa3(grafo, list(self.hosts.values()), self.plan, self.preferidos, self.nucleo, self.tabla)
        else:
            deseados = rc.flujos_deseados(grafo, list(self.hosts.values()), acceso, self.preferidos, raiz, self.copiar_arp)
        try:
            instalados = [f for d in grafo for f in self.onos.flujos_de_app(d, self.tabla)]
            if self.plan and self.tabla != fp.TABLA_REENVIO:                       # modo aislado: además, SOLO las reglas de entrada de la tabla 0 (las demás de la tabla 0 no son del vigilante)
                instalados += [f for d in grafo for f in self.onos.flujos_de_app(d, 0) if rc.es_entrada(f, self.plan)]
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

        if self.aplicar and (rc.altas_puras_capa3(dif, self.tabla) if self.plan else rc.altas_puras(dif, instalados)):         # host nuevo: solo reglas de host; sin esperar ni respetar el intervalo
            self._aplicar(dif)
            self._reiniciar_espera()
            self.log(f"ALTA RÁPIDA: {rc.resumen(dif, self.nombre)} (host nuevo; no cambia ninguna regla existente).")
            self.historial.agregar(time.time(), "alta_rapida", f"Alta rápida de reglas de host: {rc.resumen(dif, self.nombre)}", estado.cambios_por_switch(dif, self.nombre))
            return "alta rápida"

        urgente = self._caida_pendiente
        estable = self.estable_caida_s if urgente else self.estable_s
        minimo = self.min_intervalo_caida_s if urgente else self.min_intervalo_s
        huella = hash(frozenset(rc.clave(f) for f in dif["borrar"]) | frozenset(("+",) + rc.clave(f) for f in dif["agregar"]))
        ahora = self.reloj()
        if huella != self._huella:
            self._huella, self._desde = huella, ahora
            self._olvidar_avisos("simulacion", "intervalo")
            self.log(f"CAMBIO DETECTADO ({rc.resumen(dif, self.nombre)}). Raíz: {self.nombre(raiz)}. Espero {estable:g}s a que se estabilice{' (hubo una caída: espera corta)' if urgente and estable != self.estable_s else ''}.")
            self.historial.agregar(time.time(), "cambio_detectado", f"Cambio detectado ({rc.resumen(dif, self.nombre)}); raíz {self.nombre(raiz)}; espera {estable:g} s{' (tras una caída)' if urgente else ''}",
                                   estado.cambios_por_switch(dif, self.nombre))
            for tipo, flujos in (("sobra", dif["borrar"]), ("falta", dif["agregar"])):          # qué flujos exactos difieren (máx. 6 de cada tipo)
                for f in flujos[:6]:
                    self.log(f"    {tipo}: {rc.describir(f, self.nombre)}")
                if len(flujos) > 6:
                    self.log(f"    ... y {len(flujos) - 6} más que {tipo}n")
            if estable > 0:
                return "esperando"
        if ahora - self._desde < estable:
            return "esperando"
        if self._ultimo_cambio is not None and ahora - self._ultimo_cambio < minimo:
            self._aviso("intervalo", f"Espero: hubo un cambio hace menos de {minimo:g}s.")
            return "intervalo"
        if not self.aplicar:
            self._aviso("simulacion", f"SIMULACIÓN (sin --aplicar): se cambiaría {rc.resumen(dif, self.nombre)}.")
            return "simulación"
        self._aplicar(dif)
        self._ultimo_cambio = self.reloj()
        self._reiniciar_espera()
        self.log(f"APLICADO: {rc.resumen(dif, self.nombre)}. Raíz: {self.nombre(raiz)}.")
        self.historial.agregar(time.time(), "aplicado", f"Recálculo aplicado ({rc.resumen(dif, self.nombre)}); raíz {self.nombre(raiz)}", estado.cambios_por_switch(dif, self.nombre))
        return "aplicado"

    def _registrar_topologia(self, grafo, dispositivos, raiz):
        """Para el panel: anota qué cables o switches cayeron o volvieron desde el ciclo anterior y deja la foto de la topología que publica /estado. Jamás debe afectar al ciclo: ante cualquier fallo solo avisa."""
        try:
            ahora = time.time()
            foto = (set(grafo), estado.pares_de_enlaces(grafo))
            for ev in estado.eventos_de_topologia(self._foto_prev, foto, self.nombre):
                self.historial.agregar(ahora, ev["tipo"], ev["detalle"])
            self._foto_prev = foto
            estado.recordar_enlaces(self._conocidos, grafo)
            salida = self.plan.salida.dispositivo if self.plan is not None and getattr(self.plan, "salida", None) is not None else None
            top = estado.construir(grafo, self._conocidos, dispositivos, raiz, estado.destinos_de_arbol(self.plan, raiz), self.preferidos, self.nombre, salida)
            top["t"] = ahora
            self._topologia = top
            self._olvidar_avisos("estado")
        except Exception as e:  # noqa: BLE001
            self._aviso("estado", f"AVISO: no se pudo actualizar /estado ({e!r}); el vigilante sigue igual.")

    def estado_json(self, limite=50):
        """Lo que publica GET /estado: quién es, cómo está, la topología con el uso de cada cable y el historial de eventos (los nuevos primero). Sin hosts, MAC ni IP."""
        return {"t": time.time(), "rol": "respaldo" if self.salud_principal is not None else "principal", "activo": self._activo, "aplica": self.aplicar, "estado": self.ultimo_estado,
                "ultimo_ciclo": self.ultimo_ciclo, "modo": "capa3" if self.plan else "capa2", "estable_s": self.estable_s, "min_intervalo_s": self.min_intervalo_s,
                "topologia": self._topologia, "historial": self.historial.recientes(limite)}

    def _olvidar_hosts(self, listados, ahora):
        """Quita de la memoria a los hosts que ONOS ya no lista Y (a) en capa 3, otro host listado reclama su IP, o (b) llevan `olvidar_host_s` sin aparecer. Los fijos nunca.
        Al faltar de lo deseado, sus reglas pasan a `sobra` y se retiran con la espera de siempre."""
        vigentes = {h["mac"].upper() for h in listados}
        fijos = {h["mac"].upper() for h in self.hosts_fijos}
        duenos = {}
        if self.plan:
            for h in rc.hosts_con_ip(self.plan, listados):
                if h.get("ip"):
                    duenos.setdefault(h["ip"], h["mac"].upper())
        for mac in list(self.hosts):
            if mac in vigentes or mac in fijos:
                continue
            h, motivo = self.hosts[mac], None
            if self.plan:
                ip = rc.hosts_con_ip(self.plan, [h])[0].get("ip")
                if ip and duenos.get(ip, mac) != mac:
                    motivo = f"su IP {ip} la usa ahora {duenos[ip]}"
            if motivo is None and self.olvidar_host_s:
                ausente = ahora - self._visto.get(mac, ahora)
                if ausente >= self.olvidar_host_s:
                    motivo = f"ONOS no lo lista desde hace {int(ausente)} s"
            if motivo:
                del self.hosts[mac]
                self._visto.pop(mac, None)
                self.log(f"OLVIDO al host {mac} ({self.nombre(h.get('device'))}/{h.get('port')}): {motivo}.")

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
    a.add_argument("--estable-caida", type=float, default=None, help="como --estable pero cuando el cambio viene de una CAIDA de cable o de switch (por defecto, igual que --estable)")
    a.add_argument("--min-intervalo-caida", type=float, default=None, help="como --min-intervalo pero tras una caida (por defecto, igual que --min-intervalo)")
    a.add_argument("--olvidar-host", type=float, default=0, help="segundos sin aparecer en ONOS tras los cuales se olvida un host y se retiran sus reglas (0 = nunca; un host cuya IP pasa a otro host listado se olvida siempre, en capa 3)")
    a.add_argument("--max-cambios", type=int, default=200)
    a.add_argument("--hosts-fijos", metavar="ARCHIVO", help="JSON con hosts que ONOS no aprende (server, portal); se suman a los de ONOS")
    a.add_argument("--subredes", metavar="ARCHIVO", help="MODO CAPA 3: plan de subredes (JSON de capa3.cargar_plan, con nombres de switch como core-1, acc-1...)")
    a.add_argument("--nucleo", metavar="SWITCHES", help="con --subredes: switches con una ruta por cada prefijo (p. ej. core-1,core-2,borde); los demás acceso llevan solo la ruta por defecto hacia arriba")
    a.add_argument("--tabla", type=int, default=fp.TABLA_REENVIO, help="con --subredes: tabla de trabajo. Distinta de 60 (61 a 98) = MODO AISLADO: solo toca esa tabla y sus reglas de entrada en la tabla 0 (para probar con subredes de prueba sin tocar producción)")
    a.add_argument("--retirar", action="store_true", help="modo aislado: borra lo que el vigilante puso en esa tabla y sus reglas de entrada (con --aplicar)")
    a.add_argument("--copiar-arp", action="store_true", help="copia al controlador el ARP que entra por puertos de acceso (apagado por defecto)")
    a.add_argument("--interruptor", default="/etc/vigilante/desactivar")
    a.add_argument("--aplicar", action="store_true", help="sin esta bandera solo avisa")
    a.add_argument("--una-vez", action="store_true", help="hace un solo ciclo y termina (para pruebas)")
    a.add_argument("--salud-puerto", type=int, default=0, help="publica GET /salud en este puerto (0 = no) para que el respaldo vea si el principal vive")
    a.add_argument("--respaldo-de", metavar="URL", help="modo RESPALDO: solo actúa si esta URL /salud del principal deja de responder")
    a.add_argument("--muerto", type=float, default=45, help="segundos sin respuesta del principal antes de que el respaldo asuma el mando")
    arg = a.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    onos = OnosCluster([u.strip() for u in arg.onos.split(",") if u.strip()], arg.usuario)

    def log(texto):
        print(time.strftime("%H:%M:%S"), texto, flush=True)

    onos.log = log                                              # avisos de vista mala de un nodo de ONOS (onos_multi)

    if arg.generar_acceso:
        acc = generar_acceso(onos, arg.enlaces, arg.generar_acceso)
        log(f"Puertos de acceso guardados en {arg.generar_acceso}: { {_nombre(d): p for d, p in acc.items()} }")
        return 0
    try:
        with open(arg.acceso, encoding="utf-8") as f:
            acceso = json.load(f)
    except OSError:
        acceso = None
    if arg.tabla != fp.TABLA_REENVIO and (not arg.subredes or not 61 <= arg.tabla <= 98):
        log(f"NO ARRANCO: --tabla {arg.tabla} (modo aislado) exige --subredes y una tabla entre 61 y 98; la 60 es la de producción.")
        return 2
    if arg.retirar and arg.tabla == fp.TABLA_REENVIO:
        log("NO ARRANCO: --retirar solo se permite en modo aislado (--tabla distinta de 60): nunca vacía la tabla de producción.")
        return 2
    plan = None
    if arg.subredes:
        try:
            with open(arg.subredes, encoding="utf-8") as f:
                plan = capa3.cargar_plan(json.load(f), nombres=NOMBRES)
        except (OSError, ValueError) as e:                   # PlanInvalido es un ValueError
            log(f"NO ARRANCO: {e}")
            return 2
        log(f"MODO CAPA 3{' AISLADO en la tabla ' + str(arg.tabla) if arg.tabla != fp.TABLA_REENVIO else ''}{' (RETIRAR)' if arg.retirar else ''}: {len(plan.subredes)} subredes ({', '.join(s.nombre for s in plan.subredes)}).")
        if plan.salida is not None:
            log(f"Salida al exterior: ruta por defecto por {NOMBRES.get(plan.salida.dispositivo, plan.salida.dispositivo)}/{plan.salida.puerto} hacia el siguiente salto {plan.salida.mac}; "
                f"no salen: {', '.join(str(p) for p in plan.salida.bloquear)}.")
    nucleo = [{v: k for k, v in NOMBRES.items()}.get(x.strip(), x.strip()) for x in arg.nucleo.split(",") if x.strip()] if arg.nucleo else None
    if not acceso and not plan:
        if arg.aplicar:
            log(f"NO ARRANCO con --aplicar: falta {arg.acceso}. Créalo con --generar-acceso (con la red completa).")
            return 2
        log(f"AVISO: no hay {arg.acceso}; sin él no se pueden calcular los flujos de difusión. Solo se vigila la conexión.")
        acceso = {}
    fijos = []
    if arg.hosts_fijos:
        try:
            fijos = hosts_fijos.cargar(arg.hosts_fijos)
        except (OSError, ValueError) as e:
            log(f"NO ARRANCO: {e}")
            return 2
        log(f"Hosts fijos: {len(fijos)} ({', '.join(h['nombre'] for h in fijos)}).")
    salud = (lambda: consultar_salud(arg.respaldo_de)) if arg.respaldo_de else None
    v = Vigilante(onos, PREFERIDOS, acceso, aplicar=arg.aplicar, estable_s=arg.estable, min_intervalo_s=arg.min_intervalo, estable_caida_s=arg.estable_caida, min_intervalo_caida_s=arg.min_intervalo_caida, olvidar_host_s=arg.olvidar_host,
                  max_cambios=arg.max_cambios, interruptor=arg.interruptor, log=log, salud_principal=salud, muerto_s=arg.muerto,
                  hosts_fijos=fijos, copiar_arp=arg.copiar_arp, plan=plan, nucleo=nucleo, tabla=arg.tabla, retirar=arg.retirar)
    rol = f"RESPALDO de {arg.respaldo_de} (asume el mando tras {arg.muerto:g}s sin respuesta)" if arg.respaldo_de else "PRINCIPAL"
    if arg.salud_puerto:
        servir_salud(arg.salud_puerto, lambda: {"rol": "respaldo" if arg.respaldo_de else "principal", "ultimo_ciclo": v.ultimo_ciclo,
                                                  "estado": v.ultimo_estado, "aplica": arg.aplicar, "activo": v._activo}, v.estado_json)
    log(f"Vigilante iniciado ({rol}): nodos {arg.onos}; {'APLICA CAMBIOS' if arg.aplicar else 'solo avisa'}; cada {arg.intervalo}s.")
    try:
        while True:
            try:
                estado = v.ciclo()
            except Exception as e:  # noqa: BLE001  - un fallo inesperado no debe matar al servicio
                log(f"ERROR inesperado en el ciclo: {e!r}")
                estado = "error"
            v.ultimo_estado = estado
            if arg.una_vez:
                log(f"estado: {estado}")
                return 0
            time.sleep(arg.intervalo)
    except KeyboardInterrupt:
        log("Detenido.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
