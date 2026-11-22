package org.campus.dhcp;

import org.junit.Test;
import org.onlab.packet.DHCP;
import org.onlab.packet.DHCP.MsgType;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

/**
 * Las concesiones compartidas entre instancias (mapa del cluster): lo que la app guarda al confirmar una IP y lo que restaura al arrancar de nuevo. Aqui el mapa es un HashMap
 * (MAC -> texto de RegistroConcesion); la parte de ONOS (ConsistentMap, listener, maestro del switch) solo se comprueba en vivo.
 * El caso que motivo todo: tras reinstalar la app el ambito empezaba vacio y le ofrecia a otro equipo la IP de un host vivo.
 */
public class PersistenciaTest {

    static final String ACC1 = ServidorTest.ACC1;
    static final String ACC2 = ServidorTest.ACC2;
    static final long T0 = ServidorTest.T0;

    static Servidor nuevo() {
        return ServidorTest.servidor(Politica.porDefecto(),
                ServidorTest.subred("acc-1", ACC1, "10.0.1.0/24", "10.0.1.254", "10.0.1.100", "10.0.1.199", "10.0.1.250"),
                ServidorTest.subred("acc-2", ACC2, "10.0.2.0/24", "10.0.2.254", "10.0.2.100", "10.0.2.199", "10.0.2.250"));
    }

    /** DISCOVER y REQUEST completos: devuelve el evento CONCEDIDA del ACK. */
    static Servidor.Evento conceder(Servidor s, MacAddress mac, String device, long ahora) throws Exception {
        Servidor.Salida oferta = s.procesar(ServidorTest.discover(mac), device, 1, ahora);
        String ip = Ip4Address.valueOf(ServidorTest.dhcpDe(oferta.respuesta).getYourIPAddress()).toString();
        String srv = device.equals(ACC1) ? "10.0.1.250" : "10.0.2.250";
        Servidor.Salida ack = s.procesar(ServidorTest.cliente(mac, MsgType.DHCPREQUEST, ip, srv, mac), device, 1, ahora + 100);
        assertNotNull(ack.evento);
        return ack.evento;
    }

    static void guardar(Map<String, String> mapa, Servidor.Evento e) {
        mapa.put(e.mac.toString(), RegistroConcesion.de(e).codificar());
    }

    static int restaurarTodo(Servidor s, Map<String, String> mapa, long ahora) {
        int n = 0;
        for (Map.Entry<String, String> entrada : mapa.entrySet()) {
            RegistroConcesion r = RegistroConcesion.decodificar(entrada.getValue());
            if (r != null && s.restaurar(MacAddress.valueOf(entrada.getKey()), r, ahora)) {
                n++;
            }
        }
        return n;
    }

    static int ipOfrecida(Servidor s, MacAddress mac, String device, long ahora) throws Exception {
        return ServidorTest.dhcpDe(s.procesar(ServidorTest.discover(mac), device, 1, ahora).respuesta).getYourIPAddress();
    }

    // ---- el caso que motivo el cambio ----
    @Test
    public void sinRestaurarUnaAppNuevaOfreceLaIpDeUnEquipoVivo() throws Exception {
        Servidor antes = nuevo();
        Servidor.Evento e = conceder(antes, ServidorTest.mac(1), ACC1, T0);
        assertEquals("10.0.1.100", e.ip.toString());
        Servidor reinstalada = nuevo();                                       // la app reinstalada: ambito vacio
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), ipOfrecida(reinstalada, ServidorTest.mac(2), ACC1, T0 + 1000));   // el fallo que se vio en vivo con h1
    }

    @Test
    public void conLasConcesionesRestauradasNoSeOfreceLaIpDeUnEquipoVivo() throws Exception {
        Map<String, String> mapa = new HashMap<>();
        guardar(mapa, conceder(nuevo(), ServidorTest.mac(1), ACC1, T0));
        Servidor reinstalada = nuevo();
        assertEquals(1, restaurarTodo(reinstalada, mapa, T0 + 1000));
        assertEquals(Ip4Address.valueOf("10.0.1.101").toInt(), ipOfrecida(reinstalada, ServidorTest.mac(2), ACC1, T0 + 1000));
    }

    @Test
    public void elEquipoRestauradoRenuevaSuIpSinPerderla() throws Exception {
        Map<String, String> mapa = new HashMap<>();
        guardar(mapa, conceder(nuevo(), ServidorTest.mac(1), ACC1, T0));
        Servidor reinstalada = nuevo();
        restaurarTodo(reinstalada, mapa, T0 + 1000);
        Servidor.Salida s = reinstalada.procesar(ServidorTest.cliente(ServidorTest.mac(1), MsgType.DHCPREQUEST, "10.0.1.100", "10.0.1.250", ServidorTest.mac(1)), ACC1, 1, T0 + 200_000);
        assertEquals(MsgType.DHCPACK, ServidorTest.dhcpDe(s.respuesta).getPacketType());
        assertEquals("10.0.1.100", s.evento.ip.toString());
    }

    @Test
    public void laPresenciaVeLasConcesionesRestauradas() throws Exception {
        Map<String, String> mapa = new HashMap<>();
        guardar(mapa, conceder(nuevo(), ServidorTest.mac(1), ACC1, T0));
        Servidor reinstalada = nuevo();
        assertEquals(0, reinstalada.objetivos().size());
        restaurarTodo(reinstalada, mapa, T0 + 1000);
        assertEquals(1, reinstalada.objetivos().size());
    }

    // ---- lo que no se restaura ----
    @Test
    public void unaConcesionVencidaNoSeRestaura() throws Exception {
        Map<String, String> mapa = new HashMap<>();
        Servidor.Evento e = conceder(nuevo(), ServidorTest.mac(1), ACC1, T0);
        guardar(mapa, e);
        Servidor reinstalada = nuevo();
        assertEquals(0, restaurarTodo(reinstalada, mapa, e.expira + 1));
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), ipOfrecida(reinstalada, ServidorTest.mac(2), ACC1, e.expira + 1));
    }

    @Test
    public void unRegistroQueNoEncajaConLaConfiguracionSeIgnora() throws Exception {
        Servidor s = nuevo();
        MacAddress mac = ServidorTest.mac(1);
        long futuro = T0 + 900_000L;
        int ip = Ip4Address.valueOf("10.0.1.100").toInt();
        assertFalse(s.restaurar(mac, new RegistroConcesion("acc-9", ip, ACC1 + "/1", futuro), T0));                                    // subred que no existe
        assertFalse(s.restaurar(mac, new RegistroConcesion("acc-1", ip, ACC2 + "/1", futuro), T0));                                    // puerto de otro switch
        assertFalse(s.restaurar(mac, new RegistroConcesion("acc-1", ip, ACC1 + "/7", futuro), T0));                                    // puerto que no es de acceso
        assertFalse(s.restaurar(mac, new RegistroConcesion("acc-1", Ip4Address.valueOf("10.0.2.100").toInt(), ACC1 + "/1", futuro), T0));  // IP de otra subred
        assertFalse(s.restaurar(mac, new RegistroConcesion("acc-1", Ip4Address.valueOf("10.0.1.5").toInt(), ACC1 + "/1", futuro), T0));    // fuera del ambito dinamico
        assertFalse(s.restaurar(mac, new RegistroConcesion("acc-1", ip, "sin-barra", futuro), T0));
        assertEquals(0, s.objetivos().size());
    }

    // ---- idempotencia y choques ----
    @Test
    public void restaurarDosVecesEsLoMismoYOlvidarLaQuita() throws Exception {
        Map<String, String> mapa = new HashMap<>();
        Servidor.Evento e = conceder(nuevo(), ServidorTest.mac(1), ACC1, T0);
        guardar(mapa, e);
        Servidor s = nuevo();
        restaurarTodo(s, mapa, T0 + 1000);
        restaurarTodo(s, mapa, T0 + 1000);
        assertEquals(1, s.objetivos().size());
        assertEquals(1, s.concedidas().size());
        s.olvidar(ServidorTest.mac(1));
        assertEquals(0, s.objetivos().size());
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), ipOfrecida(s, ServidorTest.mac(2), ACC1, T0 + 2000));   // la IP quedo libre otra vez
    }

    @Test
    public void siDosEquiposReclamanLaMismaIpGanaLaConcesionMasNueva() {
        Servidor s = nuevo();
        int ip = Ip4Address.valueOf("10.0.1.100").toInt();
        assertTrue(s.restaurar(ServidorTest.mac(1), new RegistroConcesion("acc-1", ip, ACC1 + "/1", T0 + 100_000), T0));
        assertTrue(s.restaurar(ServidorTest.mac(2), new RegistroConcesion("acc-1", ip, ACC1 + "/1", T0 + 500_000), T0));
        assertEquals(1, s.concedidas().size());
        assertEquals(ServidorTest.mac(2), s.concedidas().get(0).mac);
        assertFalse(s.restaurar(ServidorTest.mac(1), new RegistroConcesion("acc-1", ip, ACC1 + "/1", T0 + 100_000), T0));   // la vieja ya no pisa a la nueva
        assertEquals(ServidorTest.mac(2), s.concedidas().get(0).mac);
    }

    @Test
    public void unAvisoAtrasadoNoRetrocedeUnaRenovacion() {
        Servidor s = nuevo();
        int ip = Ip4Address.valueOf("10.0.1.100").toInt();
        s.restaurar(ServidorTest.mac(1), new RegistroConcesion("acc-1", ip, ACC1 + "/1", T0 + 500_000), T0);
        assertTrue(s.restaurar(ServidorTest.mac(1), new RegistroConcesion("acc-1", ip, ACC1 + "/1", T0 + 100_000), T0));
        assertEquals(T0 + 500_000, s.concedidas().get(0).expira);
    }

    @Test
    public void unEquipoQueCambioDeIpSeQuedaConLaDelMapa() {
        Servidor s = nuevo();
        MacAddress mac = ServidorTest.mac(1);
        s.restaurar(mac, new RegistroConcesion("acc-1", Ip4Address.valueOf("10.0.1.100").toInt(), ACC1 + "/1", T0 + 100_000), T0);
        s.restaurar(mac, new RegistroConcesion("acc-1", Ip4Address.valueOf("10.0.1.150").toInt(), ACC1 + "/1", T0 + 200_000), T0);
        assertEquals(1, s.concedidas().size());
        assertEquals("10.0.1.150", s.concedidas().get(0).ip.toString());
    }

    // ---- solo el maestro de un switch sondea sus equipos ----
    @Test
    public void elFiltroDeObjetivosDejaSoloLosEquiposDeLosSwitchesPropios() throws Exception {
        Servidor s = nuevo();
        conceder(s, ServidorTest.mac(1), ACC1, T0);
        conceder(s, ServidorTest.mac(2), ACC2, T0);
        assertEquals(2, s.objetivos().size());
        List<Presencia.Objetivo> soloAcc1 = s.objetivos(puerto -> puerto.startsWith(ACC1 + "/"));
        assertEquals(1, soloAcc1.size());
        assertEquals(ServidorTest.mac(1), soloAcc1.get(0).mac);
        assertEquals(0, s.objetivos(puerto -> false).size());                   // un nodo que no es maestro de ninguno no sondea nada
    }

    // ---- el evento y el texto guardado ----
    @Test
    public void elEventoDelAckTraeElVencimientoYSeListaEnConcedidas() throws Exception {
        Servidor s = nuevo();
        Servidor.Evento e = conceder(s, ServidorTest.mac(1), ACC1, T0);
        assertEquals(T0 + 100 + 3600 * 1000L, e.expira);
        assertEquals(1, s.concedidas().size());
        assertEquals(e.expira, s.concedidas().get(0).expira);
        assertEquals(Servidor.Evento.Tipo.CONCEDIDA, s.concedidas().get(0).tipo);
    }

    @Test
    public void elRegistroSeCodificaYSeLeeDeVuelta() {
        RegistroConcesion r = new RegistroConcesion("acc-1", Ip4Address.valueOf("10.0.1.100").toInt(), ACC1 + "/1", 1763830000000L);
        assertEquals("10.0.1.100|acc-1|of:0000e6925d87354b/1|1763830000000", r.codificar());
        RegistroConcesion leido = RegistroConcesion.decodificar(r.codificar());
        assertNotNull(leido);
        assertEquals(r.subred, leido.subred);
        assertEquals(r.ip, leido.ip);
        assertEquals(r.puerto, leido.puerto);
        assertEquals(r.expira, leido.expira);
    }

    @Test
    public void unTextoCorruptoOdeOtraVersionSeIgnoraSinFallar() {
        assertNull(RegistroConcesion.decodificar(null));
        assertNull(RegistroConcesion.decodificar(""));
        assertNull(RegistroConcesion.decodificar("10.0.1.100|acc-1"));
        assertNull(RegistroConcesion.decodificar("no-es-ip|acc-1|of:1/1|5"));
        assertNull(RegistroConcesion.decodificar("10.0.1.100|acc-1|of:1/1|no-es-numero"));
        assertNull(RegistroConcesion.decodificar("10.0.1.100||of:1/1|5"));
        assertNull(RegistroConcesion.decodificar("10.0.1.100|acc-1|of:1/1|5|sobra"));
    }

    @Test
    public void laOfertaPendienteDeOtroEquipoCedeAnteUnaConcesionRestaurada() throws Exception {
        Servidor s = nuevo();
        DHCP oferta = ServidorTest.dhcpDe(s.procesar(ServidorTest.discover(ServidorTest.mac(2)), ACC1, 1, T0).respuesta);   // mac2 tiene .100 ofrecida (sin confirmar)
        assertEquals(Ip4Address.valueOf("10.0.1.100").toInt(), oferta.getYourIPAddress());
        int ip = Ip4Address.valueOf("10.0.1.100").toInt();
        assertTrue(s.restaurar(ServidorTest.mac(1), new RegistroConcesion("acc-1", ip, ACC1 + "/1", T0 + 900_000), T0 + 10));
        assertEquals(ServidorTest.mac(1), s.concedidas().get(0).mac);
    }
}
