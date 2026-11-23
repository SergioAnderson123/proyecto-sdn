"""Guardrails for HA-aware VNRT playbooks: stable mapper URL and one supplicant per run."""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "R1" / "deploy"
VNRT = DEPLOY / "topologia14"


def test_playbook_88_uses_the_mapper_gateway_for_reads_and_presence():
    plays = yaml.safe_load((VNRT / "88_prueba_sesion_ausente.yml").read_text())
    vars_ = plays[0]["vars"]
    assert "192.168.201.254" in vars_["mapper_gateway"]
    assert "mapper_gateway" in vars_["ver_sesion"]
    tasks = plays[0]["tasks"]
    post = next(t for t in tasks if t.get("name") == "POST /sessions/ausente")
    assert "{{ mapper_gateway }}/sessions/ausente" in post["ansible.builtin.shell"]["cmd"]
    assert "127.0.0.1:5000" not in (VNRT / "88_prueba_sesion_ausente.yml").read_text()


def test_playbook_72_defaults_to_h1_and_targets_only_the_selected_host():
    plays = yaml.safe_load((VNRT / "72_suplicante.yml").read_text())
    token_play = plays[0]
    assert "h1-profesor" in token_play["vars"]["host_objetivo"]
    set_fact = next(t for t in token_play["tasks"] if t.get("name") == "Pasarla a los hosts con 802.1X")
    assert set_fact["delegate_to"] == "{{ host_objetivo }}"
    imported = plays[1]
    assert imported["vars"]["hosts_objetivo"] == "{{ objetivo_host | default('h1-profesor') }}"


def test_playbook_compartido_conserva_el_grupo_completo_como_default():
    play = yaml.safe_load((DEPLOY / "hosts" / "hosts.yml").read_text())[0]
    assert play["hosts"] == "{{ hosts_objetivo | default('hosts') }}"
