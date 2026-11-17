package org.campus.meters;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

/** La parte pura de campus-meters: de la configuracion a la lista de medidores y reglas que debe haber. */
public class PlanMetersTest {

    static final ObjectMapper JSON = new ObjectMapper();
    static final String ACC2 = "of:0000766a0c84c741";
    static final String ACC1 = "of:0000e6925d87354b";

    static JsonNode json(String texto) throws Exception {
        return JSON.readTree(texto);
    }

    static Especificacion buscar(List<Especificacion> lista, String clave) {
        for (Especificacion e : lista) {
            if (e.clave().equals(clave)) {
                return e;
            }
        }
        return null;
    }

    @Test
    public void sinClasesSeActivanLasDosConLosValoresDelDisenoDeR1() throws Exception {
        PlanMeters p = PlanMeters.desde(json("{\"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}"));
        assertEquals(2, p.especificaciones().size());
        Especificacion eap = buscar(p.especificaciones(), ACC2 + "/1/eapol");
        Especificacion dhcp = buscar(p.especificaciones(), ACC2 + "/1/dhcp");
        assertEquals(20, eap.rate);
        assertEquals(20, eap.rafaga);
        assertEquals(50010, eap.prioridad);
        assertEquals(10, dhcp.rate);
        assertEquals(10, dhcp.rafaga);
        assertEquals(40510, dhcp.prioridad);
        assertEquals(0, p.tabla());
    }

    @Test
    public void conClasesSoloSeAplicanLasListadas() throws Exception {
        PlanMeters p = PlanMeters.desde(json("{\"clases\": {\"dhcp\": {}}, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1, 2]}]}"));
        assertEquals(2, p.especificaciones().size());
        for (Especificacion e : p.especificaciones()) {
            assertEquals(Clase.DHCP, e.clase);
            assertEquals(10, e.rate);                                                                      // valor ausente: el del diseño
        }
    }

    @Test
    public void unMedidorPorPuertoYClase() throws Exception {
        PlanMeters p = PlanMeters.desde(json("{\"dispositivos\": [{\"device\": \"" + ACC1 + "\", \"puertos\": [1, 2, 3]}, {\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}"));
        assertEquals((3 + 1) * 2, p.especificaciones().size());                                            // lineal en puertos: 2 por puerto
        Set<String> claves = new HashSet<>();
        for (Especificacion e : p.especificaciones()) {
            assertTrue("clave repetida " + e.clave(), claves.add(e.clave()));
        }
        assertEquals(6, p.de(ACC1).size());
        assertEquals(2, p.de(ACC2).size());
        assertEquals(new HashSet<>(java.util.Arrays.asList(ACC1, ACC2)), p.dispositivos());
    }

    @Test
    public void unSwitchNuevoEsUnaEntradaMasYNoCambiaLosDemas() throws Exception {
        PlanMeters antes = PlanMeters.desde(json("{\"dispositivos\": [{\"device\": \"" + ACC1 + "\", \"puertos\": [1]}]}"));
        PlanMeters despues = PlanMeters.desde(json("{\"dispositivos\": [{\"device\": \"" + ACC1 + "\", \"puertos\": [1]}, {\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}"));
        assertEquals(2, antes.especificaciones().size());
        assertEquals(4, despues.especificaciones().size());
        assertEquals(antes.de(ACC1).get(0).clave(), despues.de(ACC1).get(0).clave());
    }

    @Test
    public void losValoresDeLaConfiguracionMandanSobreLosDelDiseno() throws Exception {
        PlanMeters p = PlanMeters.desde(json("{\"tabla\": 5, \"clases\": {\"eapol\": {\"rate\": 50, \"burst\": 60, \"prioridad\": 50020}}, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}"));
        Especificacion e = p.especificaciones().get(0);
        assertEquals(50, e.rate);
        assertEquals(60, e.rafaga);
        assertEquals(50020, e.prioridad);
        assertEquals(5, p.tabla());
    }

    @Test
    public void lasPrioridadesDeDisenoQuedanEntreLasReglasVecinas() {
        assertTrue(Clase.DHCP.prioridadDefecto > 40500);        // sobre la regla general de DHCP de R2
        assertTrue(Clase.DHCP.prioridadDefecto < 40600);        // bajo el DROP del DHCP externo
        assertTrue(Clase.EAPOL.prioridadDefecto > 50000);       // sobre la de sesion de R1
    }

    @Test
    public void rechazaConfiguracionesInvalidas() throws Exception {
        String[] malas = {
                "{}",
                "{\"dispositivos\": []}",
                "{\"dispositivos\": [{\"device\": \"" + ACC2 + "\"}]}",
                "{\"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": []}]}",
                "{\"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [0]}]}",
                "{\"clases\": {}, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}",
                "{\"clases\": {\"mab\": {}}, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}",
                "{\"clases\": {\"dhcp\": {\"rate\": 0}}, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}",
                "{\"clases\": {\"dhcp\": {\"prioridad\": 70000}}, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}",
                "{\"tabla\": 300, \"dispositivos\": [{\"device\": \"" + ACC2 + "\", \"puertos\": [1]}]}"
        };
        for (String m : malas) {
            try {
                PlanMeters.desde(json(m));
                fail("debia rechazar: " + m);
            } catch (IllegalArgumentException esperado) {
                // bien
            }
        }
    }
}
