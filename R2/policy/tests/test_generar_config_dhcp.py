import json
import os
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import capa3 as l3  # noqa: E402
import generar_config_dhcp as g  # noqa: E402
from plan_arboles import NOMBRES  # noqa: E402

PLAN = os.path.join(os.path.dirname(AQUI), "planes", "vnrt_topologia14.json")


class TestConfigDhcp(unittest.TestCase):
    def setUp(self):
        with open(PLAN, encoding="utf-8") as f:
            self.plan = l3.cargar_plan(json.load(f), nombres=NOMBRES)

    def test_por_defecto_solo_las_subredes_de_acceso_con_su_ambito(self):
        c = g.construir(self.plan)["campusdhcp"]
        self.assertEqual([s["nombre"] for s in c["subredes"]], ["acc-1", "acc-2"])
        s = c["subredes"][0]
        self.assertEqual((s["prefijo"], s["gateway"], s["inicio"], s["fin"], s["servidor"]), ("10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250"))
        self.assertTrue(s["device"].startswith("of:") and s["puertos"] == [1])

    def test_el_ambito_no_incluye_el_gateway_ni_el_servidor_ni_las_ip_fijas(self):
        for s in g.construir(self.plan)["campusdhcp"]["subredes"]:
            base = s["prefijo"].rsplit(".", 1)[0]
            ultimo = lambda ip: int(ip.rsplit(".", 1)[1])  # noqa: E731
            self.assertTrue(ultimo(s["inicio"]) <= ultimo(s["fin"]) < ultimo(s["gateway"]))
            self.assertFalse(ultimo(s["inicio"]) <= ultimo(s["servidor"]) <= ultimo(s["fin"]))
            self.assertTrue(s["servidor"].startswith(base + "."))

    def test_se_pueden_elegir_subredes_y_la_politica(self):
        c = g.construir(self.plan, subredes=["acc-2"], max_por_puerto=5, dns=("1.1.1.1", "8.8.8.8"))["campusdhcp"]
        self.assertEqual([s["nombre"] for s in c["subredes"]], ["acc-2"])
        self.assertEqual(c["politica"]["maxPorPuerto"], 5)
        self.assertEqual(c["subredes"][0]["dns"], ["1.1.1.1", "8.8.8.8"])

    def test_una_subred_que_no_esta_en_el_plan_se_rechaza(self):
        with self.assertRaises(l3.PlanInvalido):
            g.construir(self.plan, subredes=["no-existe"])

    def test_json_serializable(self):
        json.dumps(g.construir(self.plan))


if __name__ == "__main__":
    unittest.main()
