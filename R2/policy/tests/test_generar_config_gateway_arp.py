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


if __name__ == "__main__":
    unittest.main()
