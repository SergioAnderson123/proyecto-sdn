"""Funciones puras para medir los criterios cuantitativos de R5 que faltaban (sin red ni VM, para poder probarlas):

  * tasa de falsos positivos POR ALERTAS  -> leer_alertas, clasificar, sumar_clasificaciones
  * escalabilidad de eventos              -> resumen_escala, percentil
  * consumo de recursos y ancho de banda  -> jiffies_de_stat, rss_kb_de_status, cpu_porcentaje, mbps, resumen_serie

Las usan medicion_r5.py (orquestador, se ejecuta en switch-borde) y medir_recursos.py (muestreador, se ejecuta en cada sensor).
"""
import datetime
import json
import math


# ---------------------------------------------------------------- estadistica basica
def percentil(valores, p):
    """Percentil `p` (0-100) con interpolacion lineal; None si no hay valores."""
    if not valores:
        return None
    v = sorted(valores)
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * p / 100.0
    i, f = int(math.floor(k)), k - math.floor(k)
    return v[i] if i + 1 >= len(v) else v[i] + (v[i + 1] - v[i]) * f


def resumen_serie(valores):
    """{media, p95, max} de una serie de muestras (None si esta vacia)."""
    if not valores:
        return {"media": None, "p95": None, "max": None}
    return {"media": sum(valores) / len(valores), "p95": percentil(valores, 95), "max": max(valores)}


# ---------------------------------------------------------------- falsos positivos por alertas
def iso_a_epoch(ts):
    """'2026-11-23T15:22:00.123456+0000' -> epoch (float)."""
    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()


def leer_alertas(texto):
    """Alertas de un eve-alerts.json (una por linea). Descarta lo que no sea una alerta valida. Devuelve dicts con epoch, src, dst, direction, sid, firma, sev."""
    res = []
    for linea in (texto or "").splitlines():
        try:
            ev = json.loads(linea)
            if ev.get("event_type") != "alert":
                continue
            a = ev["alert"]
            res.append({"epoch": iso_a_epoch(ev["timestamp"]), "src": ev["src_ip"], "dst": ev["dest_ip"], "direction": ev.get("direction"),
                        "sid": a.get("signature_id"), "firma": a.get("signature"), "sev": a.get("severity")})
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return res


def atacante_de(alerta):
    """IP que Suricata senala como atacante: el origen, o el destino si la firma salto sobre la respuesta (to_client)."""
    return alerta["dst"] if alerta.get("direction") == "to_client" else alerta["src"]


def clasificar(alertas, ip_malo, ip_normal, desde=None, hasta=None):
    """Cuenta alertas verdaderas (el atacante es la IP maliciosa), falsas (el atacante es la IP del trafico legitimo) y otras (ninguna de las dos).

    `desde`/`hasta` (epoch) acotan la ventana. Devuelve totales y el desglose por firma. Una alerta FALSA es la que Suricata genera sobre trafico
    legitimo: es el numerador de la tasa de falsos positivos del proyecto (alertas incorrectas / alertas generadas)."""
    out = {"total": 0, "verdaderas": 0, "falsas": 0, "otras": 0, "por_firma": {}}
    for a in alertas:
        if (desde is not None and a["epoch"] < desde) or (hasta is not None and a["epoch"] > hasta):
            continue
        quien = atacante_de(a)
        clase = "verdaderas" if quien == ip_malo else ("falsas" if quien == ip_normal else "otras")
        out["total"] += 1
        out[clase] += 1
        f = out["por_firma"].setdefault(a["sid"], {"firma": a["firma"], "verdaderas": 0, "falsas": 0, "otras": 0})
        f[clase] += 1
    return out


def sumar_clasificaciones(lista):
    """Suma varias corridas de `clasificar`."""
    tot = {"total": 0, "verdaderas": 0, "falsas": 0, "otras": 0, "por_firma": {}}
    for c in lista:
        for k in ("total", "verdaderas", "falsas", "otras"):
            tot[k] += c[k]
        for sid, f in c["por_firma"].items():
            t = tot["por_firma"].setdefault(sid, {"firma": f["firma"], "verdaderas": 0, "falsas": 0, "otras": 0})
            for k in ("verdaderas", "falsas", "otras"):
                t[k] += f[k]
    return tot


def tasa_falsos_positivos(c):
    """(% estricto, % conservador). Estricto: solo las alertas sobre trafico legitimo. Conservador: tambien cuenta las no atribuibles (otras)."""
    if not c["total"]:
        return None, None
    return 100.0 * c["falsas"] / c["total"], 100.0 * (c["falsas"] + c["otras"]) / c["total"]


# ---------------------------------------------------------------- escalabilidad
ORIGEN_INICIO = 104       # primer origen falso: 203.0.113.104 (la .103 es la IP real de vm-atacante y la .254, el gateway)
MAX_ORIGENES = 150        # hasta 203.0.113.253


def origenes_externos(k):
    """K IPs de origen para los ataques simulados, dentro de 203.0.113.0/24.

    Tienen que ser de ese rango (o de 10.0.0.0/16) porque es lo unico que el espejo del borde copia al IDS: un origen de otro rango nunca llegaria
    a Suricata y el experimento daria 0 bloqueos. Se reutilizan entre corridas, por eso hay que desbloquearlas antes de cada una."""
    if not 1 <= k <= MAX_ORIGENES:
        raise ValueError(f"K debe estar entre 1 y {MAX_ORIGENES} (hay {MAX_ORIGENES} origenes disponibles en 203.0.113.0/24)")
    return [f"203.0.113.{ORIGEN_INICIO + i}" for i in range(k)]


def resumen_escala(bloqueos, esperadas, t0):
    """Resultado de una corrida con K origenes simultaneos.

    bloqueos: [(epoch, ip, mitigacion_ms, sensor)] leidos del integrador; esperadas: IPs que debian bloquearse; t0: epoch del primer paquete.
    Devuelve cuantas se bloquearon, el tiempo hasta el primer y el ultimo bloqueo, bloqueos por segundo y la mitigacion (p50/p95/max)."""
    esperadas = set(esperadas)
    propios = [b for b in bloqueos if b[1] in esperadas]
    ips = {b[1] for b in propios}
    if not propios:
        return {"esperadas": len(esperadas), "bloqueadas": 0, "primero_s": None, "ultimo_s": None, "bloqueos_por_s": None,
                "mitigacion_p50_ms": None, "mitigacion_p95_ms": None, "mitigacion_max_ms": None}
    primer = {}
    for ts, ip, ms, _ in sorted(propios):
        primer.setdefault(ip, (ts, ms))                            # una IP puede bloquearla cada sensor: se cuenta el primero
    tiempos = [v[0] for v in primer.values()]
    mit = [v[1] for v in primer.values()]
    t_primero, t_ultimo = min(tiempos) - t0, max(tiempos) - t0
    lapso = max(max(tiempos) - min(tiempos), 0.001)
    return {"esperadas": len(esperadas), "bloqueadas": len(ips), "primero_s": t_primero, "ultimo_s": t_ultimo,
            "bloqueos_por_s": len(ips) / lapso if len(ips) > 1 else None,
            "mitigacion_p50_ms": percentil(mit, 50), "mitigacion_p95_ms": percentil(mit, 95), "mitigacion_max_ms": max(mit)}


# ---------------------------------------------------------------- recursos (lectura de /proc y /sys)
def jiffies_de_stat(texto):
    """utime + stime (en jiffies) de un /proc/<pid>/stat. El nombre del proceso puede traer espacios y parentesis: se corta tras el ultimo ')'."""
    resto = texto[texto.rindex(")") + 2:].split()
    return int(resto[11]) + int(resto[12])


def rss_kb_de_status(texto):
    """VmRSS (kB) de un /proc/<pid>/status; 0 si no aparece."""
    for linea in texto.splitlines():
        if linea.startswith("VmRSS:"):
            return int(linea.split()[1])
    return 0


def cpu_porcentaje(j0, j1, dt, hz):
    """CPU (% de un nucleo) entre dos lecturas de jiffies tomadas con `dt` segundos de diferencia."""
    return 100.0 * (j1 - j0) / (hz * dt) if dt > 0 else 0.0


def mbps(bytes0, bytes1, dt):
    """Mbit/s entre dos lecturas de un contador de bytes; un contador que se reinicia (valor menor) se trata como 0."""
    return max(bytes1 - bytes0, 0) * 8 / dt / 1e6 if dt > 0 else 0.0
