"""Pruebas de las plantillas del clúster (atomix.conf, cluster.json y compose) con un inventario de ejemplo.

Ejecutar:  python -m pytest test_plantillas.py -q      (necesita jinja2 y PyYAML; sin ellos se omite)
"""
import json
import os
import re

import pytest

jinja2 = pytest.importorskip("jinja2")
yaml = pytest.importorskip("yaml")

BASE = os.path.dirname(os.path.abspath(__file__))
IPS = {"node-ctrl-1": "192.168.201.1", "node-ctrl-2": "192.168.201.2", "node-ctrl-3": "192.168.201.3"}


def _entorno():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(BASE, "templates")), trim_blocks=True, keep_trailing_newline=True)
    env.filters["to_json"] = lambda v: json.dumps(v)                       # los de Ansible que usan las plantillas
    env.filters["regex_replace"] = lambda s, a, b: re.sub(a, b, str(s))
    return env


def _variables(nodo):
    gv = yaml.safe_load(open(os.path.join(BASE, "group_vars", "all.yml"), encoding="utf-8"))
    base = {k: v for k, v in gv.items() if not (isinstance(v, str) and "{{" in v)}   # las que dependen del inventario se fijan abajo
    base.update(onos_nodos=list(IPS.values()), groups={"ctrl": list(IPS)}, hostvars={h: {"ansible_host": ip} for h, ip in IPS.items()},
                inventory_hostname=nodo, ansible_host=IPS[nodo])
    return base


@pytest.mark.parametrize("nodo", list(IPS))
def test_las_plantillas_del_cluster_se_renderizan_bien(nodo):
    env, ctx, n = _entorno(), _variables(nodo), list(IPS).index(nodo) + 1
    atomix = json.loads(env.get_template("atomix.conf.j2").render(ctx))
    onos = json.loads(env.get_template("cluster.json.j2").render(ctx))
    compose = yaml.safe_load(env.get_template("cluster-compose.yml.j2").render(ctx))

    assert atomix["cluster"]["node"] == {"id": f"atomix-{n}", "address": f"{IPS[nodo]}:5679"}
    assert [x["address"] for x in atomix["cluster"]["discovery"]["nodes"]] == [f"{ip}:5679" for ip in IPS.values()]
    miembros = ["atomix-1", "atomix-2", "atomix-3"]
    assert atomix["managementGroup"]["members"] == miembros == atomix["partitionGroups"]["raft"]["members"]
    assert atomix["partitionGroups"]["raft"]["partitionSize"] == 3          # Raft de 3: tolera la caída de un miembro

    assert onos["name"] == atomix["cluster"]["clusterId"] == "onos"
    assert onos["node"] == {"id": IPS[nodo], "ip": IPS[nodo], "port": 9876}  # el id del nodo ONOS es su IP
    assert [s["ip"] for s in onos["storage"]] == list(IPS.values()) and all(s["port"] == 5679 for s in onos["storage"])

    for servicio in ("atomix", "onos"):
        assert compose["services"][servicio]["network_mode"] == "host"
    assert compose["services"]["onos"]["volumes"][0].endswith(":/root/onos/config/cluster.json:ro")
    assert compose["services"]["atomix"]["volumes"][0].endswith(":/opt/atomix/conf/atomix.conf:ro")


def test_los_tres_controladores_openflow_salen_de_los_tres_nodos():
    env = _entorno()
    t = env.from_string("{{ onos_nodos | map('regex_replace', '^', 'tcp:') | map('regex_replace', '$', ':' ~ onos_port) | list }}")
    assert t.render(onos_nodos=list(IPS.values()), onos_port=6653) == str([f"tcp:{ip}:6653" for ip in IPS.values()])


def test_todos_los_playbooks_y_las_variables_son_yaml_valido():
    for f in sorted(os.listdir(BASE)):
        if f.endswith(".yml"):
            yaml.safe_load(open(os.path.join(BASE, f), encoding="utf-8"))
    yaml.safe_load(open(os.path.join(BASE, "group_vars", "all.yml"), encoding="utf-8"))
