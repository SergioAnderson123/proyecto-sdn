import json
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import capa3 as l3  # noqa: E402
import generar_config_meters as g  # noqa: E402
from plan_arboles import NOMBRES  # noqa: E402

PLAN = os.path.join(os.path.dirname(AQUI), "planes", "vnrt_topologia14.json")
RAIZ = os.path.join(os.path.dirname(AQUI), "..", "..")


class TestConfigMeters(unittest.TestCase):
    def setUp(self):
        with open(PLAN, encoding="utf-8") as f:
            self.plan = l3.cargar_plan(json.load(f), nombres=NOMBRES)

    def test_por_defecto_los_switches_de_acceso_con_las_dos_clases_del_diseno_de_r1(self):
        c = g.construir(self.plan)["campusmeters"]
        self.assertEqual([d["nombre"] for d in c["dispositivos"]], ["acc-1", "acc-2"])
        self.assertTrue(all(d["device"].startswith("of:") and d["puertos"] == [1] for d in c["dispositivos"]))
        self.assertEqual(c["clases"]["eapol"], {"rate": 20, "burst": 20, "prioridad": 50010})
        self.assertEqual(c["clases"]["dhcp"], {"rate": 10, "burst": 10, "prioridad": 40510})
        self.assertEqual(c["tabla"], 0)

    def test_solo_un_switch_y_una_clase(self):
        c = g.construir(self.plan, solo=["acc-2"], clases=["dhcp"])["campusmeters"]
        self.assertEqual([d["nombre"] for d in c["dispositivos"]], ["acc-2"])
        self.assertEqual(list(c["clases"]), ["dhcp"])

    def test_los_valores_se_pueden_cambiar(self):
        c = g.construir(self.plan, valores={"dhcp": (5, 8)})["campusmeters"]
        self.assertEqual((c["clases"]["dhcp"]["rate"], c["clases"]["dhcp"]["burst"]), (5, 8))
        self.assertEqual(c["clases"]["eapol"]["rate"], 20)

    def test_rechaza_lo_invalido(self):
        with self.assertRaises(l3.PlanInvalido):
            g.construir(self.plan, solo=["no-existe"])
        with self.assertRaises(l3.PlanInvalido):
            g.construir(self.plan, clases=["mab"])
        with self.assertRaises(l3.PlanInvalido):
            g.construir(self.plan, valores={"dhcp": (0, 5)})

    def test_las_prioridades_coinciden_con_el_mapa_de_r5(self):
        import yaml
        with open(os.path.join(RAIZ, "R5", "deploy", "prioridades.yaml"), encoding="utf8") as f:
            mapa = yaml.safe_load(f)["r2"]
        self.assertEqual(g.CLASES["dhcp"][2], mapa["meter_dhcp_puerto"])
        self.assertEqual(g.CLASES["eapol"][2], mapa["meter_eapol_puerto"])
        self.assertGreater(mapa["meter_dhcp_puerto"], mapa["dhcp"])             # sobre la regla general de DHCP
        self.assertLess(mapa["meter_dhcp_puerto"], mapa["dhcp_externo"])        # bajo el DROP del DHCP externo
        self.assertGreater(mapa["meter_eapol_puerto"], 50000)                   # sobre la sesion de R1

    def test_la_linea_de_comandos_escribe_el_archivo(self):
        salida = os.path.join(tempfile.mkdtemp(), "m.json")
        self.assertEqual(g.main(["--plan", PLAN, "--solo", "acc-2", "--clases", "dhcp", "--dhcp", "12/15", "--salida", salida]), 0)
        with open(salida, encoding="utf-8") as f:
            c = json.load(f)["campusmeters"]
        self.assertEqual(c["clases"]["dhcp"]["rate"], 12)
        self.assertEqual(g.main(["--plan", PLAN, "--solo", "nada"]), 2)


if __name__ == "__main__":
    unittest.main()
