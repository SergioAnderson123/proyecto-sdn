package org.campus.dhcp;

import org.junit.Test;
import org.onlab.packet.DHCP;
import org.onlab.packet.DHCP.MsgType;
import org.onlab.packet.Ethernet;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;

/**
 * Las tramas REALES que produce R1/deploy/topologia14/files/cliente_dhcp_prueba.py (el cliente de 83_prueba_dhcp.yml), copiadas byte a byte: el servidor las entiende, contesta
 * OFFER y ACK con la IP esperada y procesa el RELEASE. Asi se sabe que lo que saldra de h1 es lo que el servidor interpreta, sin depender del cliente dhclient.
 */
public class ClientePruebaTest {

    static final String DISCOVER = "ffffffffffff02aabb00010108004500011600000000401179d800000000ffffffff00440043010200000101060000001234000080000000000000000000000000000000000002aabb0001010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000063825363350101370401030633ff";
    static final String REQUEST = "ffffffffffff02aabb00010108004500012200000000401179cc00000000ffffffff00440043010e00000101060000001234000080000000000000000000000000000000000002aabb000101000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000006382536335010332040a00016436040a0001fa370401030633ff";
    static final String RELEASE = "02005e0000fd02aabb00010108004500011c00000000401162740a0001640a0001fa00440043010800000101060000009999000000000a00016400000000000000000000000002aabb000101000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000006382536335010736040a0001fa370401030633ff";
    static final String ACC1 = "of:0000e6925d87354b";

    static Ethernet leer(String hex) throws Exception {
        byte[] b = new byte[hex.length() / 2];
        for (int i = 0; i < b.length; i++) {
            b[i] = (byte) Integer.parseInt(hex.substring(2 * i, 2 * i + 2), 16);
        }
        return Ethernet.deserializer().deserialize(b, 0, b.length);
    }

    @Test
    public void elClienteDePruebaRecibeOfertaAckYPuedeLiberar() throws Exception {
        Servidor servidor = new Servidor(Arrays.asList(new Subred("acc-1", ACC1, new HashSet<>(Collections.singletonList(1L)), "10.0.1.0/24", "10.0.1.254",
                "10.0.1.100", "10.0.1.199", "10.0.1.250", MacAddress.valueOf("02:00:5E:00:00:FD"), Arrays.asList("8.8.8.8"), 3600)), Politica.porDefecto());
        long t = 1_000_000L;
        Servidor.Salida oferta = servidor.procesar(leer(DISCOVER), ACC1, 1, t);
        assertNotNull(oferta.respuesta);
        DHCP o = (DHCP) ((org.onlab.packet.UDP) ((org.onlab.packet.IPv4) oferta.respuesta.getPayload()).getPayload()).getPayload();
        assertEquals(MsgType.DHCPOFFER, o.getPacketType());
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), o.getYourIPAddress());
        Servidor.Salida ack = servidor.procesar(leer(REQUEST), ACC1, 1, t + 100);
        DHCP a = (DHCP) ((org.onlab.packet.UDP) ((org.onlab.packet.IPv4) ack.respuesta.getPayload()).getPayload()).getPayload();
        assertEquals(MsgType.DHCPACK, a.getPacketType());
        assertEquals(Servidor.Evento.Tipo.CONCEDIDA, ack.evento.tipo);
        assertEquals(Ip4Address.valueOf("10.0.1.100"), ack.evento.ip);
        Servidor.Salida liberado = servidor.procesar(leer(RELEASE), ACC1, 1, t + 200);
        assertNull(liberado.respuesta);
        assertEquals(Servidor.Evento.Tipo.LIBERADA, liberado.evento.tipo);
        assertEquals(0, servidor.pool("acc-1").ocupadas());
    }
}
