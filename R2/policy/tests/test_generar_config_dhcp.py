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

    def test_lleva_la_mac_del_gateway_y_el_sondeo_de_presencia_por_defecto(self):
        c = g.construir(self.plan)["campusdhcp"]
        self.assertEqual(c["gwMac"], self.plan.gw_mac)
        self.assertEqual(c["presencia"], {"intervaloSeg": 5, "intentos": 3, "sondeoLentoSeg": 120})
        self.assertNotIn("policyMapperToken", c["presencia"])                       # la clave nunca viene del plan: solo del entorno al generar

    def test_la_url_y_la_clave_del_policy_mapper_vienen_de_la_linea_de_comandos_y_del_entorno(self):
        import tempfile
        salida = os.path.join(tempfile.mkdtemp(), "c.json")
        os.environ["PRESENCIA_TOKEN"] = "clave-de-prueba"
        try:
            self.assertEqual(g.main(["--plan", PLAN, "--pm-url", "http://192.168.201.1:5000", "--sondeo-lento", "60", "--salida", salida]), 0)
        finally:
            del os.environ["PRESENCIA_TOKEN"]
        with open(salida, encoding="utf-8") as f:
            pres = json.load(f)["campusdhcp"]["presencia"]
        self.assertEqual((pres["policyMapperUrl"], pres["policyMapperToken"], pres["sondeoLentoSeg"]), ("http://192.168.201.1:5000", "clave-de-prueba", 60))

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


class TestBloqueDhcpDelPlan(unittest.TestCase):
    def setUp(self):
        with open(PLAN, encoding="utf-8") as f:
            self.datos = json.load(f)
        self.plan = l3.cargar_plan(self.datos, nombres=NOMBRES)

    def test_el_plan_del_slice_fija_la_concesion_de_las_subredes_de_acceso(self):
        extras = {s["nombre"]: s["dhcp"] for s in self.datos["subredes"] if "dhcp" in s}
        c = g.construir(self.plan, extras=extras)["campusdhcp"]
        self.assertEqual([s["leaseSeg"] for s in c["subredes"]], [900, 900])

    def test_sin_bloque_dhcp_vale_el_valor_por_defecto(self):
        c = g.construir(self.plan, lease=1234)["campusdhcp"]
        self.assertEqual([s["leaseSeg"] for s in c["subredes"]], [1234, 1234])

    def test_el_bloque_puede_cambiar_el_ambito_y_el_dns(self):
        c = g.construir(self.plan, extras={"acc-1": {"inicio": "10.0.1.20", "fin": "10.0.1.60", "servidor": "10.0.1.200", "dns": ["1.1.1.1"]}})["campusdhcp"]
        s = c["subredes"][0]
        self.assertEqual((s["inicio"], s["fin"], s["servidor"], s["dns"]), ("10.0.1.20", "10.0.1.60", "10.0.1.200", ["1.1.1.1"]))

    def test_un_bloque_incoherente_se_rechaza(self):
        for malo in ({"inicio": "10.0.1.100", "fin": "10.0.1.50"},                  # inicio mayor que fin
                     {"inicio": "10.0.2.5"},                                         # fuera de la subred
                     {"inicio": "10.0.1.200", "fin": "10.0.1.254"},                  # incluye el gateway
                     {"servidor": "10.0.1.150"},                                     # el servidor cae en el ambito
                     {"leaseSeg": 10},                                               # concesion demasiado corta
                     {"leaseSeg_mal": 5}):                                           # campo desconocido
            with self.assertRaises(l3.PlanInvalido, msg=str(malo)):
                g.construir(self.plan, extras={"acc-1": malo})


if __name__ == "__main__":
    unittest.main()
