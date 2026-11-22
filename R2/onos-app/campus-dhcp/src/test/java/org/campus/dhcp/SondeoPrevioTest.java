package org.campus.dhcp;

import org.junit.Before;
import org.junit.Test;
import org.onlab.packet.DHCP;
import org.onlab.packet.DHCP.MsgType;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;

import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

/**
 * Sondeo ARP previo a ofrecer una IP (opcion B del diseno): una IP nueva no se ofrece hasta que, durante `sondeoPrevioMs`, nadie contesta al ARP "quien la tiene?".
 * Si alguien contesta (un equipo con IP fija dentro del ambito dinamico) la IP se aparta y se busca otra. Todo con el tiempo como parametro, sin ONOS.
 */
public class SondeoPrevioTest {

    static final int ESPERA = 800;
    static final MacAddress FIJO = MacAddress.valueOf("fa:16:3e:00:00:99");      // el equipo con IP fija que contesta al sondeo
    Servidor servidor;

    static Servidor con(int sondeoMs, int intentos, int maxPorPuerto) {
        return ServidorTest.servidor(new Politica(maxPorPuerto, 15, 100.0, 100, 300, sondeoMs, intentos),
                ServidorTest.subred("acc-1", ServidorTest.ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250"));
    }

    @Before
    public void preparar() {
        servidor = con(ESPERA, 5, 3);
    }

    static int ip(String s) {
        return Ip4Address.valueOf(s).toInt();
    }

    static String ipDeOferta(Servidor.Salida s) {
        return Ip4Address.valueOf(ServidorTest.dhcpDe(s.respuesta).getYourIPAddress()).toString();
    }

    @Test
    public void unaIPNuevaNoSeOfreceDeInmediatoSinoQueSePideElSondeo() throws Exception {
        Servidor.Salida s = servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertNull("todavia no hay OFFER", s.respuesta);
        assertEquals("sondeo previo", s.motivo);
        assertEquals(1, s.sondeos.size());
        assertEquals(ip("10.0.1.100"), s.sondeos.get(0).ip);
        assertEquals("acc-1", s.sondeos.get(0).subred.nombre);
        assertEquals(ip("10.0.1.254"), s.sondeos.get(0).subred.gateway);          // el emisor del ARP es el gateway
    }

    @Test
    public void sinRespuestaAlPlazoSeOfreceLaIPSondeada() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertTrue("antes del plazo no se concluye nada", servidor.tick(ServidorTest.T0 + ESPERA - 1).isEmpty());
        List<Servidor.Salida> listas = servidor.tick(ServidorTest.T0 + ESPERA);
        assertEquals(1, listas.size());
        Servidor.Salida oferta = listas.get(0);
        assertNotNull(oferta.respuesta);
        assertEquals(MsgType.DHCPOFFER, ServidorTest.dhcpDe(oferta.respuesta).getPacketType());
        assertEquals("10.0.1.100", ipDeOferta(oferta));
        assertEquals(ServidorTest.ACC1, oferta.device);                            // la app sabe por que puerto contestar
        assertEquals(1L, oferta.puerto);
        assertEquals(ServidorTest.mac(1), oferta.respuesta.getDestinationMAC());
        assertTrue("una vez concluido no vuelve a salir", servidor.tick(ServidorTest.T0 + 5 * ESPERA).isEmpty());
    }

    @Test
    public void siContestaOtroEquipoLaIPSeAPartaYSeOfreceLaSiguiente() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        Servidor.Salida otra = servidor.respuestaArp(ip("10.0.1.100"), FIJO, ServidorTest.T0 + 20);
        assertNotNull(otra);
        assertNull("todavia no hay OFFER: la nueva IP tambien se sondea", otra.respuesta);
        assertEquals(ip("10.0.1.101"), otra.sondeos.get(0).ip);
        assertTrue(otra.detalle.contains("10.0.1.100") && otra.detalle.contains(FIJO.toString()));
        Servidor.Salida oferta = servidor.tick(ServidorTest.T0 + 20 + ESPERA).get(0);
        assertEquals("10.0.1.101", ipDeOferta(oferta));
        assertEquals("la IP del equipo fijo queda apartada y no se le ofrece a nadie mas", ip("10.0.1.102"),
                servidor.procesar(ServidorTest.discover(ServidorTest.mac(2)), ServidorTest.ACC1, 1, ServidorTest.T0 + 2000).sondeos.get(0).ip);
    }

    @Test
    public void quienContestaNoEsElPropioClienteSiNoSeIgnora() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertNull("si contesta el propio cliente (ya usa esa IP) no hay conflicto", servidor.respuestaArp(ip("10.0.1.100"), ServidorTest.mac(1), ServidorTest.T0 + 20));
        assertEquals("10.0.1.100", ipDeOferta(servidor.tick(ServidorTest.T0 + ESPERA).get(0)));
    }

    @Test
    public void unaRespuestaDeOtraIPONadieEsperandoSeIgnora() throws Exception {
        assertNull(servidor.respuestaArp(ip("10.0.1.150"), FIJO, ServidorTest.T0));
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertNull("contesta por una IP que nadie estaba esperando", servidor.respuestaArp(ip("10.0.1.150"), FIJO, ServidorTest.T0 + 20));
        assertEquals("10.0.1.100", ipDeOferta(servidor.tick(ServidorTest.T0 + ESPERA).get(0)));
    }

    @Test
    public void unDiscoverRepetidoMientrasSeEsperaNoAbreOtroSondeoNiGastaLimite() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        Servidor.Salida repetido = servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0 + 300);
        assertNull(repetido.respuesta);
        assertTrue(repetido.sondeos.isEmpty());
        assertEquals("sondeo previo en curso", repetido.motivo);
        assertEquals(1, servidor.tick(ServidorTest.T0 + ESPERA).size());
    }

    @Test
    public void despuesDeLaOfertaUnDiscoverNuevoRecibeLaMismaIPSinSondear() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        servidor.tick(ServidorTest.T0 + ESPERA);
        Servidor.Salida otra = servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0 + 2000);
        assertNotNull("la oferta ya estaba comprobada: se repite sin esperar", otra.respuesta);
        assertTrue(otra.sondeos.isEmpty());
        assertEquals("10.0.1.100", ipDeOferta(otra));
    }

    @Test
    public void elRequestDeLaIPOfrecidaSeConfirmaSinSondear() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        servidor.tick(ServidorTest.T0 + ESPERA);
        Servidor.Salida ack = servidor.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", ServidorTest.mac(1)),
                ServidorTest.ACC1, 1, ServidorTest.T0 + ESPERA + 50);
        assertEquals(MsgType.DHCPACK, ServidorTest.dhcpDe(ack.respuesta).getPacketType());
        assertNotNull(ack.evento);
        assertTrue(ack.sondeos.isEmpty());
    }

    @Test
    public void unaRenovacionDeUnaConcesionConfirmadaNoSeSondea() throws Exception {
        servidor.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        servidor.tick(ServidorTest.T0 + ESPERA);
        servidor.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0 + 1000);
        Servidor.Salida renueva = servidor.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.100", null, ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0 + 400_000);
        assertEquals(MsgType.DHCPACK, ServidorTest.dhcpDe(renueva.respuesta).getPacketType());
        assertTrue(renueva.sondeos.isEmpty());
    }

    @Test
    public void sinSondeoPrevioTodoSigueComoAntes() throws Exception {
        Servidor sin = con(0, 5, 3);
        Servidor.Salida s = sin.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertNotNull(s.respuesta);
        assertTrue(s.sondeos.isEmpty());
        assertEquals("10.0.1.100", ipDeOferta(s));
        assertTrue(sin.tick(ServidorTest.T0 + 10_000).isEmpty());
        assertNull(sin.respuestaArp(ip("10.0.1.100"), FIJO, ServidorTest.T0));
    }

    @Test
    public void siTodasLasCandidatasEstanOcupadasSeDesisteSinOferta() throws Exception {
        Servidor corto = con(ESPERA, 2, 3);
        corto.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        Servidor.Salida segunda = corto.respuestaArp(ip("10.0.1.100"), FIJO, ServidorTest.T0 + 10);
        assertEquals(ip("10.0.1.101"), segunda.sondeos.get(0).ip);
        Servidor.Salida fin = corto.respuestaArp(ip("10.0.1.101"), FIJO, ServidorTest.T0 + 20);
        assertNull(fin.respuesta);
        assertTrue(fin.sondeos.isEmpty());
        assertTrue(fin.motivo.contains("sin IP libre comprobada"));
        assertEquals("no queda reserva colgada", 0, corto.pool("acc-1").ocupadas());
        Servidor.Salida otro = corto.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0 + 5000);
        assertEquals("el cliente reintenta y recibe una IP que si esta libre", ip("10.0.1.102"), otro.sondeos.get(0).ip);
    }

    @Test
    public void laCuotaDelPuertoSigueMandandoAunConSondeo() throws Exception {
        int ofertasEnEspera = 0;
        for (int n = 1; n <= 6; n++) {
            Servidor.Salida s = servidor.procesar(ServidorTest.discover(ServidorTest.mac(n)), ServidorTest.ACC1, 1, ServidorTest.T0 + n);
            if (!s.sondeos.isEmpty()) {
                ofertasEnEspera++;
            } else {
                assertTrue(s.motivo.contains("cuota"));
            }
        }
        assertEquals("maxPorPuerto = 3: un atacante con MAC falsas no llena el ambito ni la lista de espera", 3, ofertasEnEspera);
        assertEquals(3, servidor.tick(ServidorTest.T0 + 10 * ESPERA).size());
    }

    @Test
    public void unRequestDeUnEquipoSinRegistroSeSondeaYSeConfirmaSiNadieContesta() throws Exception {
        Servidor.Salida s = servidor.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.150", null, ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertNull(s.respuesta);
        assertEquals(ip("10.0.1.150"), s.sondeos.get(0).ip);
        Servidor.Salida ack = servidor.tick(ServidorTest.T0 + ESPERA).get(0);
        assertEquals(MsgType.DHCPACK, ServidorTest.dhcpDe(ack.respuesta).getPacketType());
        assertEquals(ip("10.0.1.150"), ack.evento.ip.toInt());
        assertEquals(Servidor.Evento.Tipo.CONCEDIDA, ack.evento.tipo);
    }

    @Test
    public void unRequestDeUnEquipoSinRegistroRecibeNakSiLaIPYaLaUsaOtro() throws Exception {
        servidor.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.150", null, ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        Servidor.Salida nak = servidor.respuestaArp(ip("10.0.1.150"), FIJO, ServidorTest.T0 + 30);
        assertEquals(MsgType.DHCPNAK, ServidorTest.dhcpDe(nak.respuesta).getPacketType());
        assertNull(nak.evento);
        assertEquals(0, servidor.pool("acc-1").ocupadas());
        assertTrue("ya no queda nada esperando", servidor.tick(ServidorTest.T0 + 10 * ESPERA).isEmpty());
        Servidor.Salida nuevo = servidor.procesar(ServidorTest.discover(ServidorTest.mac(2)), ServidorTest.ACC1, 1, ServidorTest.T0 + 1000);
        assertEquals("la IP del equipo fijo sigue apartada tambien para otros clientes", ip("10.0.1.100"), nuevo.sondeos.get(0).ip);
        Servidor.Salida pidenOtraVez = servidor.procesar(ServidorTest.cliente(ServidorTest.mac(3), MsgType.DHCPREQUEST, "10.0.1.150", null, ServidorTest.mac(3)), ServidorTest.ACC1, 1, ServidorTest.T0 + 2000);
        assertEquals("apartada: se contesta NAK sin sondear", MsgType.DHCPNAK, ServidorTest.dhcpDe(pidenOtraVez.respuesta).getPacketType());
    }

    @Test
    public void unRequestFueraDelAmbitoOConLaIPDeOtroNoSeSondea() throws Exception {
        Servidor.Salida fuera = servidor.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.50", null, ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);
        assertEquals("fuera del ambito dinamico: NAK directo", MsgType.DHCPNAK, ServidorTest.dhcpDe(fuera.respuesta).getPacketType());
        assertTrue(fuera.sondeos.isEmpty());
    }

    @Test
    public void unaReservaVencidaDuranteLaEsperaSeSondeaDeNuevoSiCambiaLaIP() throws Exception {
        Servidor corto = ServidorTest.servidor(new Politica(3, 3, 100.0, 100, 300, 5000, 5),
                ServidorTest.subred("acc-1", ServidorTest.ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250"));
        corto.procesar(ServidorTest.discover(ServidorTest.mac(1)), ServidorTest.ACC1, 1, ServidorTest.T0);                  // oferta de 3 s, espera de 5 s: la reserva vence antes del plazo
        corto.procesar(ServidorTest.discover(ServidorTest.mac(2)), ServidorTest.ACC1, 1, ServidorTest.T0 + 4000);          // otro cliente toma .100 (vencida la reserva) y espera su sondeo
        List<Servidor.Salida> salidas = corto.tick(ServidorTest.T0 + 5000);
        assertEquals(1, salidas.size());
        Servidor.Salida s = salidas.get(0);
        assertNull("la IP de la oferta ya no es suya: no se ofrece, se sondea la nueva", s.respuesta);
        assertEquals(ip("10.0.1.101"), s.sondeos.get(0).ip);
    }

    @Test
    public void laPoliticaSinSondeoPrevioConservaTodoLoDemas() {
        Politica con = new Politica(4, 20, 3.0, 9, 120, 800, 7);
        Politica sin = con.sinSondeoPrevio();
        assertEquals(0, sin.sondeoPrevioMs);
        assertEquals(4, sin.maxPorPuerto);
        assertEquals(20, sin.ofertaSeg);
        assertEquals(120, sin.declinadaSeg);
        assertEquals(7, sin.sondeoIntentos);
        assertEquals("por defecto no hay sondeo previo: lo activa la configuracion", 0, Politica.porDefecto().sondeoPrevioMs);
        assertFalse(Politica.porDefecto().sondeoIntentos < 1);
    }
}
