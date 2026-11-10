"""Topología de R5 en el slice de 14 VM (informe previo del Lab 6): lectura y validación de `topologia.yaml`.

Un solo archivo describe lo que cambia de un slice a otro: los nodos del clúster ONOS y los Policy Mapper, las redes del campus, los
dos cores con sus puertos de entrada y de espejo, y la MAC de la NIC de cada sensor que recibe la copia. Lo leen:
  - `lab/espejo.py`           instala el espejo selectivo en los cores
  - `codigo/integrador.py`    toma de aquí los cores donde bloquear, los nodos ONOS y los Policy Mapper
  - `cli/r5ctl.py`            nombres de los switches y estado del espejo por core
  - `deploy/ids/suricata/`    HOME_NET de Suricata

Los números de puerto OpenFlow NO se guardan: se escriben los nombres de interfaz (ens5, ens12...) y se resuelven contra ONOS
(`GET /devices/{id}/ports`, anotación `portName`), porque en cada slice VNRT el orden de los puertos cambia.

Uso:  python3 topologia.py <archivo> [--home-net]     valida el archivo (o imprime HOME_NET para Suricata)
"""
import ipaddress
import os
import re
import sys

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.I)
DEVICE_RE = re.compile(r"^of:[0-9a-f]{16}$", re.I)


class TopologiaInvalida(ValueError):
    """El archivo no describe una topología utilizable; el mensaje dice qué falta o sobra."""


def _yaml():
    try:
        import yaml
    except ImportError:                                           # pragma: no cover
        raise SystemExit("Falta PyYAML (pip install pyyaml) para leer la topología")
    return yaml


def cargar(ruta):
    """Lee y valida `ruta`. Devuelve el diccionario ya normalizado (listas y redes)."""
    with open(ruta, encoding="utf-8") as f:
        datos = _yaml().safe_load(f) or {}
    return validar(datos)


def _lista_str(valor, campo, obligatorio=False):
    if valor is None:
        valor = []
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list) or not all(isinstance(x, str) and x.strip() for x in valor):
        raise TopologiaInvalida(f"{campo}: debe ser una lista de textos")
    if obligatorio and not valor:
        raise TopologiaInvalida(f"{campo}: no puede estar vacío")
    return [x.strip() for x in valor]


def _redes(valor, campo, obligatorio=False):
    out = []
    for x in _lista_str(valor, campo, obligatorio):
        try:
            out.append(str(ipaddress.ip_network(x, strict=False)))
        except ValueError:
            raise TopologiaInvalida(f"{campo}: '{x}' no es una red IPv4/IPv6 válida")
    return out


def validar(d):
    if not isinstance(d, dict):
        raise TopologiaInvalida("el archivo debe ser un mapa YAML")
    res = {}
    onos = d.get("onos") or {}
    res["onos_urls"] = [u.rstrip("/") for u in _lista_str(onos.get("urls"), "onos.urls", obligatorio=True)]
    res["mapper_urls"] = [u.rstrip("/") for u in _lista_str((d.get("policy_mapper") or {}).get("urls"), "policy_mapper.urls")]
    redes = d.get("redes") or {}
    res["home"] = _redes(redes.get("home"), "redes.home", obligatorio=True)
    res["externa"] = _redes(redes.get("externa"), "redes.externa")
    res["lista_blanca"] = _redes(redes.get("lista_blanca"), "redes.lista_blanca")
    res["sensores"] = _lista_str(d.get("sensores"), "sensores", obligatorio=True)

    cores, vistos = [], set()
    for i, c in enumerate(d.get("cores") or [], 1):
        etiqueta = f"cores[{i}]"
        if not isinstance(c, dict):
            raise TopologiaInvalida(f"{etiqueta}: debe ser un mapa")
        nombre = str(c.get("nombre") or "").strip()
        device = str(c.get("device") or "").strip()
        if not nombre:
            raise TopologiaInvalida(f"{etiqueta}: falta 'nombre'")
        if not DEVICE_RE.match(device):
            raise TopologiaInvalida(f"{nombre}: 'device' debe ser un deviceId de ONOS (of:0000000000000001), no '{device}'")
        if device.lower() in vistos:
            raise TopologiaInvalida(f"{nombre}: el deviceId {device} está repetido")
        vistos.add(device.lower())
        entradas = _lista_str(c.get("entradas"), f"{nombre}.entradas", obligatorio=True)
        espejo = c.get("espejo") or {}
        if not isinstance(espejo, dict) or not espejo:
            raise TopologiaInvalida(f"{nombre}: falta 'espejo' (puerto y MAC hacia cada sensor)")
        sensores = {}
        for s in res["sensores"]:
            e = espejo.get(s)
            if not isinstance(e, dict) or not e.get("puerto"):
                raise TopologiaInvalida(f"{nombre}.espejo.{s}: falta 'puerto' (interfaz del core hacia ese sensor)")
            mac = str(e.get("mac") or "").strip()
            if mac and not MAC_RE.match(mac):
                raise TopologiaInvalida(f"{nombre}.espejo.{s}.mac: '{mac}' no es una MAC (aa:bb:cc:dd:ee:ff)")
            sensores[s] = {"puerto": str(e["puerto"]).strip(), "mac": mac.lower() or None}
        extra = set(espejo) - set(res["sensores"])
        if extra:
            raise TopologiaInvalida(f"{nombre}.espejo: sensores desconocidos {sorted(extra)} (declarados: {res['sensores']})")
        solapa = {s["puerto"] for s in sensores.values()} & set(entradas)
        if solapa:
            raise TopologiaInvalida(f"{nombre}: {sorted(solapa)} está a la vez en 'entradas' y como puerto de espejo")
        cores.append({"nombre": nombre, "device": device, "entradas": entradas, "espejo": sensores})
    if not cores:
        raise TopologiaInvalida("cores: define al menos un core")
    res["cores"] = cores
    res["accesos"] = [{"nombre": str(a.get("nombre")), "device": str(a.get("device"))}
                      for a in (d.get("accesos") or []) if isinstance(a, dict) and a.get("nombre") and a.get("device")]
    return res


def nombres_switches(topo):
    """{deviceId: nombre} de cores y accesos, para mostrar en r5ctl."""
    n = {c["device"]: c["nombre"] for c in topo["cores"]}
    n.update({a["device"]: a["nombre"] for a in topo.get("accesos", [])})
    return n


def aplicar_entorno(ruta=None):
    """Rellena variables de entorno que NO estén definidas con los valores de la topología.

    Así el integrador y r5ctl se configuran con un solo archivo y el entorno puede seguir sobrescribiendo cualquier valor.
    Devuelve la topología cargada o None si no hay archivo (modo de un solo core, como en el laboratorio de Docker)."""
    ruta = ruta or os.getenv("TOPOLOGIA")
    if not ruta:
        return None
    topo = cargar(ruta)
    por_defecto = {
        "ONOS_URL": ",".join(topo["onos_urls"]),
        "MAPPER_URL": ",".join(topo["mapper_urls"]),
        "HOME_NET": ",".join(topo["home"]),
        "LISTA_BLANCA": ",".join(topo["lista_blanca"]),
        "CORE_DEVICES": ",".join(c["device"] for c in topo["cores"]),
    }
    for clave, valor in por_defecto.items():
        if valor and not os.environ.get(clave):
            os.environ[clave] = valor
    return topo


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        t = cargar(sys.argv[1])
    except (TopologiaInvalida, OSError) as e:
        sys.exit(f"topología inválida: {e}")
    if "--home-net" in sys.argv:
        print(",".join(t["home"]))
    else:
        print(f"OK: {len(t['cores'])} core(s), {len(t['sensores'])} sensor(es), {len(t['onos_urls'])} nodo(s) ONOS, "
              f"home={t['home']}, externa={t['externa']}")
