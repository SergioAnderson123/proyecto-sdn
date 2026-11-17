package org.campus.dhcp;

import org.junit.Test;
import org.onlab.packet.DHCP.MsgType;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

/** El sondeo de presencia: cuando preguntar por ARP y cuando dar a un equipo por ausente (la parte pura, con el tiempo como parametro). */
public class PresenciaTest {

    static final long S = 1000L;
    static final long T0 = 5_000_000L;
    static final MacAddress A = MacAddress.valueOf("aa:00:00:00:00:01");
    static final MacAddress B = MacAddress.valueOf("aa:00:00:00:00:02");

    static Presencia.Objetivo obj(MacAddress mac, String ip) {
        return new Presencia.Objetivo(mac, Ip4Address.valueOf(ip).toInt(), "of:0000e6925d87354b/1", Ip4Address.valueOf("10.0.1.254").toInt());
    }

    static long cuantos(List<Presencia.Orden> ordenes, Presencia.Tipo tipo) {
        return ordenes.stream().filter(o -> o.tipo == tipo).count();
    }

    @Test
    public void sinSospechaNiSondeoLentoNoSeHaceNada() {
        Presencia p = new Presencia(5, 3, 0);
        for (int t = 0; t < 1000; t++) {
            assertTrue(p.tick(T0 + t * S, Collections.singletonList(obj(A, "10.0.1.100"))).isEmpty());
        }
    }

    @Test
    public void unaSospechaSondeaTresVecesCadaCincoSegundosYALosQuinceDaPorAusente() {
        Presencia p = new Presencia(5, 3, 0);
        List<Presencia.Objetivo> o = Collections.singletonList(obj(A, "10.0.1.100"));
        p.tick(T0, o);
        p.sospechar(A, T0);
        assertEquals(1, cuantos(p.tick(T0, o), Presencia.Tipo.SONDEAR));                    // t = 0
        assertTrue(p.tick(T0 + 4 * S, o).isEmpty());                                        // aun no toca
        assertEquals(1, cuantos(p.tick(T0 + 5 * S, o), Presencia.Tipo.SONDEAR));            // t = 5
        assertEquals(1, cuantos(p.tick(T0 + 10 * S, o), Presencia.Tipo.SONDEAR));           // t = 10
        List<Presencia.Orden> fin = p.tick(T0 + 15 * S, o);                                 // t = 15: sin respuesta al tercero
        assertEquals(1, cuantos(fin, Presencia.Tipo.AUSENTE));
        assertEquals(0, cuantos(fin, Presencia.Tipo.SONDEAR));
        assertEquals(A, fin.get(0).objetivo.mac);
    }

    @Test
    public void unaRespuestaTerminaElCicloYElEquipoSeQuedaConSuIp() {
        Presencia p = new Presencia(5, 3, 0);
        List<Presencia.Objetivo> o = Collections.singletonList(obj(A, "10.0.1.100"));
        p.tick(T0, o);
        p.sospechar(A, T0);
        p.tick(T0, o);
        p.respuesta(A, T0 + S);
        assertFalse(p.enCicloRapido(A));
        for (int t = 2; t < 200; t++) {
            assertEquals(0, cuantos(p.tick(T0 + t * S, o), Presencia.Tipo.AUSENTE));
        }
    }

    @Test
    public void unaRespuestaTardiaAntesDelUltimoSondeoSalvaAlEquipo() {
        Presencia p = new Presencia(5, 3, 0);
        List<Presencia.Objetivo> o = Collections.singletonList(obj(A, "10.0.1.100"));
        p.tick(T0, o);
        p.sospechar(A, T0);
        p.tick(T0, o);
        p.tick(T0 + 5 * S, o);
        p.tick(T0 + 10 * S, o);
        p.respuesta(A, T0 + 12 * S);                                                        // contesta el tercer sondeo
        assertEquals(0, cuantos(p.tick(T0 + 15 * S, o), Presencia.Tipo.AUSENTE));
    }

    @Test
    public void elSondeoLentoUnEquipoVivoCierraElCicloConSuPrimeraRespuesta() {
        Presencia p = new Presencia(5, 3, 120);
        List<Presencia.Objetivo> o = Collections.singletonList(obj(A, "10.0.1.100"));
        assertTrue(p.tick(T0, o).isEmpty());                                                // primera vez que se ve: el proximo sondeo es a los 120 s
        assertTrue(p.tick(T0 + 119 * S, o).isEmpty());
        assertEquals(1, cuantos(p.tick(T0 + 120 * S, o), Presencia.Tipo.SONDEAR));
        p.respuesta(A, T0 + 120 * S + 200);
        assertTrue(p.tick(T0 + 121 * S, o).isEmpty());
        assertEquals(1, cuantos(p.tick(T0 + 240 * S + 200, o), Presencia.Tipo.SONDEAR));    // y otra vez a los 120 s
    }

    @Test
    public void elSondeoLentoDeUnEquipoApagadoLoDaPorAusente() {
        Presencia p = new Presencia(5, 3, 120);
        List<Presencia.Objetivo> o = Collections.singletonList(obj(A, "10.0.1.100"));
        p.tick(T0, o);
        long ausentes = 0;
        for (int t = 120; t <= 140; t++) {
            ausentes += cuantos(p.tick(T0 + t * S, o), Presencia.Tipo.AUSENTE);
        }
        assertEquals(1, ausentes);
    }

    @Test
    public void unaSospechaRepetidaNoReiniciaElCiclo() {
        Presencia p = new Presencia(5, 3, 0);
        List<Presencia.Objetivo> o = Collections.singletonList(obj(A, "10.0.1.100"));
        p.tick(T0, o);
        p.sospechar(A, T0);
        p.tick(T0, o);
        p.sospechar(A, T0 + 6 * S);                                                         // otra baja de flujo en pleno ciclo
        p.tick(T0 + 5 * S, o);
        p.tick(T0 + 10 * S, o);
        assertEquals(1, cuantos(p.tick(T0 + 15 * S, o), Presencia.Tipo.AUSENTE));
    }

    @Test
    public void unEquipoSinConcesionSeOlvidaYNoSeSondea() {
        Presencia p = new Presencia(5, 3, 0);
        p.sospechar(A, T0);
        assertTrue(p.tick(T0, Collections.emptyList()).isEmpty());
        assertFalse(p.enCicloRapido(A));
        assertTrue(p.tick(T0 + 20 * S, Collections.singletonList(obj(A, "10.0.1.100"))).isEmpty());   // la sospecha anterior se perdio con su concesion
    }

    @Test
    public void cadaEquipoLlevaSuPropioCiclo() {
        Presencia p = new Presencia(5, 3, 0);
        List<Presencia.Objetivo> o = Arrays.asList(obj(A, "10.0.1.100"), obj(B, "10.0.1.101"));
        p.tick(T0, o);
        p.sospechar(A, T0);
        List<Presencia.Orden> ordenes = p.tick(T0, o);
        assertEquals(1, ordenes.size());
        assertEquals(A, ordenes.get(0).objetivo.mac);
        assertFalse(p.enCicloRapido(B));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rechazaParametrosInvalidos() {
        new Presencia(0, 3, 0);
    }

    // ---- con el servidor: lo que la app hace con las ordenes ----
    @Test
    public void elServidorEntregaSusConcesionesComoObjetivosYLasLibera() throws Exception {
        Servidor s = ServidorTest.servidor(Politica.porDefecto(),
                ServidorTest.subred("acc-1", ServidorTest.ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250"));
        MacAddress mac = ServidorTest.mac(1);
        assertTrue(s.objetivos().isEmpty());
        Servidor.Salida oferta = s.procesar(ServidorTest.discover(mac), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertTrue(s.objetivos().isEmpty());                                                // una oferta no se sondea
        s.procesar(ServidorTest.cliente(mac, MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac), ServidorTest.ACC1, 1, ServidorTest.T0 + 1);
        assertNotNull(oferta.respuesta);
        List<Presencia.Objetivo> objetivos = s.objetivos();
        assertEquals(1, objetivos.size());
        assertEquals(mac, objetivos.get(0).mac);
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), objetivos.get(0).ip);
        assertEquals(Ip4Address.valueOf("10.0.1.254").toInt(), objetivos.get(0).gateway);
        assertEquals(ServidorTest.ACC1 + "/1", objetivos.get(0).puerto);
        assertTrue(s.dispositivos().contains(ServidorTest.ACC1));

        Servidor.Evento e = s.liberarMac(mac);
        assertNotNull(e);
        assertEquals(Servidor.Evento.Tipo.LIBERADA, e.tipo);
        assertEquals(Ip4Address.valueOf("10.0.1.100"), e.ip);
        assertTrue(s.objetivos().isEmpty());
        assertNull(s.liberarMac(mac));                                                      // ya no tiene nada que liberar
    }
}
