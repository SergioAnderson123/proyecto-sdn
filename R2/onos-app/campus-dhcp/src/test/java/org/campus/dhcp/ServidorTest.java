package org.campus.dhcp;

import org.junit.Before;
import org.junit.Test;
import org.onlab.packet.DHCP;
import org.onlab.packet.DHCP.DHCPOptionCode;
import org.onlab.packet.DHCP.MsgType;
import org.onlab.packet.Ethernet;
import org.onlab.packet.IPv4;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;
import org.onlab.packet.UDP;
import org.onlab.packet.dhcp.DhcpOption;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class ServidorTest {

    static final String ACC1 = "of:0000e6925d87354b";
    static final String ACC2 = "of:0000766a0c84c741";
    static final MacAddress SERVIDOR_MAC = MacAddress.valueOf("02:00:5E:00:00:FD");
    static final long T0 = 1_000_000L;

    Servidor servidor;

    static Subred subred(String nombre, String device, String prefijo, String gw, String ini, String fin, String srv) {
        return new Subred(nombre, device, new HashSet<>(Collections.singletonList(1L)), prefijo, gw, ini, fin, srv, SERVIDOR_MAC,
                Arrays.asList("8.8.8.8"), 3600);
    }

    static Servidor servidor(Politica p, Subred... subredes) {
        return new Servidor(Arrays.asList(subredes), p);
    }

    @Before
    public void preparar() {
        servidor = servidor(Politica.porDefecto(),
                subred("acc-1", ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250"),
                subred("acc-2", ACC2, "10.0.2.0/24", "10.0.2.254", "10.0.2.100", "10.0.2.199", "10.0.2.250"));
    }

    // ---- construccion de paquetes del cliente (se serializan y se vuelven a leer, como llegan de ONOS) ----
    static DhcpOption opcion(DHCPOptionCode c, byte[] d) {
        return new DhcpOption().setCode(c.getValue()).setLength((byte) d.length).setData(d);
    }

    static byte[] ip(String s) {
        return ByteBuffer.allocate(4).putInt(Ip4Address.valueOf(s).toInt()).array();
    }

    static Ethernet cliente(MacAddress mac, MsgType tipo, String pedida, String servidor, MacAddress origen) throws Exception {
        byte[] chaddr = new byte[16];
        System.arraycopy(mac.toBytes(), 0, chaddr, 0, 6);
        DHCP d = new DHCP();
        d.setOpCode(DHCP.OPCODE_REQUEST).setHardwareType(DHCP.HWTYPE_ETHERNET).setHardwareAddressLength((byte) 6).setTransactionId(0x1234).setFlags((short) 0x8000);
        d.setClientHardwareAddress(chaddr);
        List<DhcpOption> o = new ArrayList<>();
        o.add(opcion(DHCPOptionCode.OptionCode_MessageType, new byte[]{(byte) tipo.getValue()}));
        if (pedida != null) {
            o.add(opcion(DHCPOptionCode.OptionCode_RequestedIP, ip(pedida)));
        }
        if (servidor != null) {
            o.add(opcion(DHCPOptionCode.OptionCode_DHCPServerIp, ip(servidor)));
        }
        o.add(new DhcpOption().setCode(DHCPOptionCode.OptionCode_END.getValue()).setLength((byte) 0));
        d.setOptions(o);
        UDP u = new UDP();
        u.setSourcePort(68);
        u.setDestinationPort(67);
        u.setPayload(d);
        IPv4 i = new IPv4();
        i.setSourceAddress(0);
        i.setDestinationAddress(Ip4Address.valueOf("255.255.255.255").toInt());
        i.setTtl((byte) 64);
        i.setProtocol(IPv4.PROTOCOL_UDP);
        i.setPayload(u);
        Ethernet e = new Ethernet();
        e.setSourceMACAddress(origen).setDestinationMACAddress(MacAddress.BROADCAST).setEtherType(Ethernet.TYPE_IPV4).setPayload(i);
        byte[] bytes = e.serialize();
        return Ethernet.deserializer().deserialize(bytes, 0, bytes.length);
    }

    static Ethernet discover(MacAddress mac) throws Exception {
        return cliente(mac, MsgType.DHCPDISCOVER, null, null, mac);
    }

    static MacAddress mac(int n) {
        return MacAddress.valueOf(String.format("aa:00:00:00:00:%02x", n));
    }

    static DHCP dhcpDe(Ethernet r) {
        return (DHCP) ((UDP) ((IPv4) r.getPayload()).getPayload()).getPayload();
    }

    static int opcionIp(DHCP d, DHCPOptionCode c) {
        return ByteBuffer.wrap(d.getOption(c).getData()).getInt();
    }

    // ---- pruebas ----
    @Test
    public void discoverRecibeUnaOfertaConLasOpcionesDelAmbito() throws Exception {
        Servidor.Salida s = servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        assertNotNull(s.respuesta);
        DHCP r = dhcpDe(s.respuesta);
        assertEquals(MsgType.DHCPOFFER, r.getPacketType());
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), r.getYourIPAddress());
        assertEquals(Ip4Address.valueOf("255.255.255.0").toInt(), opcionIp(r, DHCPOptionCode.OptionCode_SubnetMask));
        assertEquals(Ip4Address.valueOf("10.0.1.254").toInt(), opcionIp(r, DHCPOptionCode.OptionCode_RouterAddress));
        assertEquals(Ip4Address.valueOf("10.0.1.250").toInt(), opcionIp(r, DHCPOptionCode.OptionCode_DHCPServerIp));
        assertEquals(3600, opcionIp(r, DHCPOptionCode.OptionCode_LeaseTime));
        assertEquals(0x1234, r.getTransactionId());
        assertEquals(SERVIDOR_MAC, s.respuesta.getSourceMAC());
        assertEquals(mac(1), s.respuesta.getDestinationMAC());
        assertNull(s.evento);
    }

    @Test
    public void laRespuestaSeSerializaConUDPDel67Al68YTerminaEnFin() throws Exception {
        Ethernet r = servidor.procesar(discover(mac(1)), ACC1, 1, T0).respuesta;
        byte[] bytes = r.serialize();
        int ultimo = bytes.length - 1;
        while (bytes[ultimo] == 0) {                                         // el serializador de ONOS rellena con ceros despues del FIN (legal: opcion 0 = relleno)
            ultimo--;
        }
        assertEquals((byte) 0xFF, bytes[ultimo]);                           // el ultimo byte con contenido es la opcion FIN
        Ethernet leido = Ethernet.deserializer().deserialize(bytes, 0, bytes.length);
        UDP udp = (UDP) ((IPv4) leido.getPayload()).getPayload();
        assertEquals(67, udp.getSourcePort());
        assertEquals(68, udp.getDestinationPort());
        assertEquals(Ip4Address.valueOf("10.0.1.250").toInt(), ((IPv4) leido.getPayload()).getSourceAddress());
    }

    @Test
    public void requestConfirmaYAvisaAOnos() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        assertEquals(MsgType.DHCPACK, dhcpDe(s.respuesta).getPacketType());
        assertNotNull(s.evento);
        assertEquals(Servidor.Evento.Tipo.CONCEDIDA, s.evento.tipo);
        assertEquals(Ip4Address.valueOf("10.0.1.100"), s.evento.ip);
        assertEquals(mac(1), s.evento.mac);
        assertEquals("acc-1", s.evento.subred);
        assertEquals(ACC1 + "/1", s.evento.puerto);
    }

    @Test
    public void cadaPuertoRecibeLaSubredDeSuSwitch() throws Exception {
        DHCP r = dhcpDe(servidor.procesar(discover(mac(2)), ACC2, 1, T0).respuesta);
        assertEquals(Ip4Address.valueOf("10.0.2.100").toInt(), r.getYourIPAddress());
        assertEquals(Ip4Address.valueOf("10.0.2.254").toInt(), opcionIp(r, DHCPOptionCode.OptionCode_RouterAddress));
    }

    @Test
    public void unPuertoSinAmbitoSeIgnora() throws Exception {
        assertNull(servidor.procesar(discover(mac(1)), ACC1, 7, T0).respuesta);
        assertNull(servidor.procesar(discover(mac(1)), "of:00000000000000ff", 1, T0).respuesta);
    }

    @Test
    public void siElChaddrNoEsLaMacDeOrigenSeIgnora() throws Exception {
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPDISCOVER, null, null, mac(9)), ACC1, 1, T0);
        assertNull(s.respuesta);
        assertTrue(s.motivo.contains("chaddr"));
    }

    @Test
    public void conMacsFalsasUnPuertoNoPasaDeSuCuota() throws Exception {
        int ofertas = 0;
        for (int n = 1; n <= 6; n++) {
            Servidor.Salida s = servidor.procesar(discover(mac(n)), ACC1, 1, T0 + n);
            if (s.respuesta != null) {
                ofertas++;
            } else {
                assertTrue(s.motivo, s.motivo.contains("cuota"));
            }
        }
        assertEquals(Politica.porDefecto().maxPorPuerto, ofertas);
        assertEquals(Politica.porDefecto().maxPorPuerto, servidor.pool("acc-1").ocupadas());
    }

    @Test
    public void laCuotaDeUnPuertoNoAfectaAOtroPuertoNiAOtroAmbito() throws Exception {
        for (int n = 1; n <= 6; n++) {
            servidor.procesar(discover(mac(n)), ACC1, 1, T0 + n);
        }
        assertNotNull(servidor.procesar(discover(mac(50)), ACC2, 1, T0 + 10).respuesta);
    }

    @Test
    public void elLimiteDeTasaFrenaUnaInundacionAunqueLaCuotaSeaGrande() throws Exception {
        Servidor s = servidor(new Politica(1000, 15, 2.0, 8, 300),
                subred("acc-1", ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.2", "10.0.1.99", "10.0.1.250"));
        int atendidas = 0;
        for (int n = 1; n <= 100; n++) {
            if (s.procesar(discover(MacAddress.valueOf(String.format("aa:00:00:00:01:%02x", n))), ACC1, 1, T0).respuesta != null) {
                atendidas++;
            }
        }
        assertEquals(8, atendidas);                                          // solo la rafaga; el resto se descarta
        int despues = 0;
        for (int n = 101; n <= 110; n++) {                                   // 5 s despues se han rellenado 10 fichas, tope = rafaga 8
            if (s.procesar(discover(MacAddress.valueOf(String.format("aa:00:00:00:02:%02x", n))), ACC1, 1, T0 + 5000).respuesta != null) {
                despues++;
            }
        }
        assertEquals(8, despues);
    }

    @Test
    public void unaOfertaSinConfirmarVenceYLiberaLaIpYLaCuota() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        assertEquals(1, servidor.pool("acc-1").ocupadas());
        servidor.vencer(T0 + 16_000);
        assertEquals(0, servidor.pool("acc-1").ocupadas());
        DHCP r = dhcpDe(servidor.procesar(discover(mac(2)), ACC1, 1, T0 + 17_000).respuesta);
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), r.getYourIPAddress());      // la IP volvio a estar libre
    }

    @Test
    public void unaMacSoloTieneUnaConcesionYReciclaSuIp() throws Exception {
        int a = dhcpDe(servidor.procesar(discover(mac(1)), ACC1, 1, T0).respuesta).getYourIPAddress();
        int b = dhcpDe(servidor.procesar(discover(mac(1)), ACC1, 1, T0 + 3000).respuesta).getYourIPAddress();
        assertEquals(a, b);
        assertEquals(1, servidor.pool("acc-1").ocupadas());
    }

    @Test
    public void requestDeUnaIpQueNoSeOfrecioRecibeNak() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.150", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        assertEquals(MsgType.DHCPNAK, dhcpDe(s.respuesta).getPacketType());
        assertNull(s.evento);
        Servidor.Salida fuera = servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.2.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 200);
        assertEquals(MsgType.DHCPNAK, dhcpDe(fuera.respuesta).getPacketType());           // una IP de otra subred nunca se confirma
    }

    @Test
    public void unRequestParaOtroServidorSeIgnora() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.77", mac(1)), ACC1, 1, T0 + 100);
        assertNull(s.respuesta);
        assertNull(s.evento);
    }

    @Test
    public void releaseLiberaLaIpYAvisaAOnos() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPRELEASE, null, "10.0.1.250", mac(1)), ACC1, 1, T0 + 200);
        assertNull(s.respuesta);
        assertEquals(Servidor.Evento.Tipo.LIBERADA, s.evento.tipo);
        assertEquals(0, servidor.pool("acc-1").ocupadas());
    }

    @Test
    public void unaConcesionVencidaAvisaAOnos() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        assertEquals(0, servidor.vencer(T0 + 3_000_000).size());                // la concesion dura 3600 s: a los 3000 s sigue viva
        assertEquals(1, servidor.vencer(T0 + 3_700_000).size());
        assertEquals(0, servidor.pool("acc-1").ocupadas());
    }

    @Test
    public void unAmbitoPequenoSeAgotaYNoOfreceMas() throws Exception {
        Servidor s = servidor(new Politica(50, 15, 100.0, 100, 300),
                subred("acc-1", ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.101", "10.0.1.250"));
        assertNotNull(s.procesar(discover(mac(1)), ACC1, 1, T0).respuesta);
        assertNotNull(s.procesar(discover(mac(2)), ACC1, 1, T0).respuesta);
        Servidor.Salida tercera = s.procesar(discover(mac(3)), ACC1, 1, T0);
        assertNull(tercera.respuesta);
        assertTrue(tercera.motivo.contains("agotado"));
    }

    @Test
    public void unaIpDeclinadaQuedaApartada() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        servidor.procesar(cliente(mac(1), MsgType.DHCPDECLINE, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 200);
        DHCP r = dhcpDe(servidor.procesar(discover(mac(2)), ACC1, 1, T0 + 300).respuesta);
        assertEquals(Ip4Address.valueOf("10.0.1.101").toInt(), r.getYourIPAddress());      // la .100 no se vuelve a dar enseguida
    }

    @Test
    public void unRequestEnArranqueConIpAnteriorLibreSeConfirma() throws Exception {
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.123", null, mac(1)), ACC1, 1, T0);
        assertEquals(MsgType.DHCPACK, dhcpDe(s.respuesta).getPacketType());
        assertFalse(servidor.pool("acc-1").confirmadas() == 0);
    }

    @Test(expected = IllegalArgumentException.class)
    public void unAmbitoConElGatewayDentroDelRangoSeRechaza() {
        subred("x", ACC1, "10.0.1.0/24", "10.0.1.150", "10.0.1.100", "10.0.1.199", "10.0.1.250");
    }

    @Test(expected = IllegalArgumentException.class)
    public void unAmbitoQueSeSaleDeLaSubredSeRechaza() {
        subred("x", ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.2.5", "10.0.1.250");
    }

    @Test
    public void sinEnlaceEnElPuertoSeLiberanSusConcesionesYLasDeOtrosPuertosQuedan() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        servidor.procesar(discover(mac(2)), ACC2, 1, T0);
        servidor.procesar(cliente(mac(2), MsgType.DHCPREQUEST, "10.0.2.100", "10.0.2.250", mac(2)), ACC2, 1, T0 + 100);
        List<Servidor.Evento> liberadas = servidor.liberarPuerto(ACC1, 1);
        assertEquals(1, liberadas.size());
        assertEquals(Servidor.Evento.Tipo.LIBERADA, liberadas.get(0).tipo);
        assertEquals(Ip4Address.valueOf("10.0.1.100"), liberadas.get(0).ip);
        assertEquals(0, servidor.pool("acc-1").ocupadas());
        assertEquals(1, servidor.pool("acc-2").confirmadas());
    }

    @Test
    public void unSwitchCaidoLiberaTodosSusPuertosYUnPuertoSinAmbitoNoHaceNada() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);                    // solo una oferta, sin confirmar
        servidor.procesar(discover(mac(2)), ACC2, 1, T0);
        servidor.procesar(cliente(mac(2), MsgType.DHCPREQUEST, "10.0.2.100", "10.0.2.250", mac(2)), ACC2, 1, T0 + 100);
        assertEquals(0, servidor.liberarPuerto(ACC1, -1).size());            // la oferta se descarta pero no hay concesion confirmada que avisar
        assertEquals(0, servidor.pool("acc-1").ocupadas());
        assertEquals(1, servidor.liberarPuerto(ACC2, -1).size());
        assertEquals(0, servidor.liberarPuerto(ACC1, 9).size());              // un puerto que no es de ningun ambito
    }

    @Test
    public void unEquipoQueVuelveTrasPerderElEnlaceRecuperaSuIpPorElArranqueConIpAnterior() throws Exception {
        servidor.procesar(discover(mac(1)), ACC1, 1, T0);
        servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100);
        servidor.liberarPuerto(ACC1, 1);
        Servidor.Salida s = servidor.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", null, mac(1)), ACC1, 1, T0 + 5000);
        assertEquals(MsgType.DHCPACK, dhcpDe(s.respuesta).getPacketType());
    }

    // ---- opcion 121 (rutas sin clase, RFC 3442) ----
    static Subred subredConRutas(String prefijo, String gw, String ini, String fin, String srv, String... rutas) {
        return new Subred("x", ACC1, new HashSet<>(Collections.singletonList(1L)), prefijo, gw, ini, fin, srv, SERVIDOR_MAC,
                Arrays.asList("8.8.8.8"), 3600, Arrays.asList(rutas));
    }

    static byte[] opcion121(DHCP d) {
        for (DhcpOption o : d.getOptions()) {
            if (o.getCode() == 121) {
                return o.getData();
            }
        }
        return null;
    }

    @Test
    public void laOfertaYElAckAnuncianElAgregadoDelCampusPorElGatewayDeLaSubred() throws Exception {
        Servidor s = servidor(Politica.porDefecto(), subredConRutas("10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250", "10.0.0.0/16"));
        DHCP oferta = dhcpDe(s.procesar(discover(mac(1)), ACC1, 1, T0).respuesta);
        // 10.0.0.0/16 por 10.0.1.254: longitud 16, dos octetos de red (10, 0) y el gateway (10, 0, 1, 254)
        assertArrayEquals(new byte[]{16, 10, 0, 10, 0, 1, (byte) 254}, opcion121(oferta));
        assertEquals(Ip4Address.valueOf("10.0.1.254").toInt(), opcionIp(oferta, DHCPOptionCode.OptionCode_RouterAddress));      // la opcion 3 sigue: la 121 se suma
        DHCP ack = dhcpDe(s.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", mac(1)), ACC1, 1, T0 + 100).respuesta);
        assertEquals(MsgType.DHCPACK, ack.getPacketType());
        assertArrayEquals(new byte[]{16, 10, 0, 10, 0, 1, (byte) 254}, opcion121(ack));
    }

    @Test
    public void sinRutasNoSeMandaLaOpcion121() throws Exception {
        assertNull(opcion121(dhcpDe(servidor.procesar(discover(mac(1)), ACC1, 1, T0).respuesta)));
    }

    @Test
    public void unNakNoLlevaRutas() throws Exception {
        Servidor s = servidor(Politica.porDefecto(), subredConRutas("10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250", "10.0.0.0/16"));
        DHCP nak = dhcpDe(s.procesar(cliente(mac(1), MsgType.DHCPREQUEST, "10.0.2.5", "10.0.1.250", mac(1)), ACC1, 1, T0).respuesta);
        assertEquals(MsgType.DHCPNAK, nak.getPacketType());
        assertNull(opcion121(nak));
    }

    @Test
    public void lasRutasSeCodificanConSusOctetosSignificativos() {
        // /24 -> 3 octetos, /12 -> 2, /32 -> 4, /0 -> ninguno (ruta por defecto)
        Subred s = subredConRutas("10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250", "192.168.7.0/24", "172.16.0.0/12", "10.9.9.9/32", "0.0.0.0/0");
        byte[] esperado = new byte[]{
                24, (byte) 192, (byte) 168, 7, 10, 0, 1, (byte) 254,
                12, (byte) 172, 16, 10, 0, 1, (byte) 254,
                32, 10, 9, 9, 9, 10, 0, 1, (byte) 254,
                0, 10, 0, 1, (byte) 254};
        assertArrayEquals(esperado, s.opcionRutasSinClase());
    }

    @Test
    public void unaSubredSinRutasDaUnaOpcionVacia() {
        assertEquals(0, subred("x", ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250").opcionRutasSinClase().length);
    }

    @Test(expected = IllegalArgumentException.class)
    public void unaRutaConBitsDeHostSeRechaza() {
        subredConRutas("10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250", "10.0.0.1/16");
    }

    @Test(expected = IllegalArgumentException.class)
    public void unaRutaMalFormadaSeRechaza() {
        subredConRutas("10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250", "10.0.0.0");
    }
}
