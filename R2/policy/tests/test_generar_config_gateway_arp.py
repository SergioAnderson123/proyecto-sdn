import json
import os
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import capa3 as l3  # noqa: E402
import generar_config_gateway_arp as g  # noqa: E402
from plan_arboles import NOMBRES  # noqa: E402

PLAN = os.path.join(os.path.dirname(AQUI), "planes", "vnrt_topologia14.json")


class TestConfigGatewayArp(unittest.TestCase):
    def setUp(self):
        with open(PLAN, encoding="utf-8") as f:
            self.plan = l3.cargar_plan(json.load(f), nombres=NOMBRES)

    def test_una_entrada_por_subred_con_deviceid(self):
        c = g.construir(self.plan)["gatewayarp"]
        self.assertEqual(len(c["gateways"]), len(self.plan.subredes))
        for e in c["gateways"]:
            self.assertTrue(e["device"].startswith("of:"))
            self.assertTrue(e["ip"].endswith(".254"))
            self.assertTrue(e["ports"])

    def test_campos_que_lee_la_app(self):
        c = g.construir(self.plan, copiar_arp=True)["gatewayarp"]
        self.assertEqual(c["gwMac"], self.plan.gw_mac)
        self.assertEqual((c["tabla"], c["prioridad"], c["copiarArp"]), (60, l3.PRIO_ARP_GATEWAY, True))

    def test_json_serializable(self):
        json.dumps(g.construir(self.plan))



class TestPlanDePrueba(unittest.TestCase):
    def test_los_gateways_de_prueba_llevan_su_propia_mac(self):
        with open(PLAN, encoding="utf-8") as f:
            plan = l3.cargar_plan(json.load(f), nombres=NOMBRES)
        with open(os.path.join(os.path.dirname(AQUI), "planes", "prueba_aislada_capa3.json"), encoding="utf-8") as f:
            prueba = l3.cargar_plan(json.load(f), nombres=NOMBRES)
        c = g.construir(plan, plan_prueba=prueba)["gatewayarp"]
        self.assertEqual(len(c["gateways"]), len(plan.subredes) + len(prueba.subredes))
        propios = [e for e in c["gateways"] if "gwMac" in e]
        self.assertEqual(len(propios), len(prueba.subredes))
        self.assertTrue(all(e["ip"].startswith("10.99.") and e["gwMac"] == prueba.gw_mac for e in propios))
        self.assertTrue(all("gwMac" not in e for e in c["gateways"] if not e["ip"].startswith("10.99.")))
        self.assertNotEqual(prueba.gw_mac, plan.gw_mac)


if __name__ == "__main__":
    unittest.main()
