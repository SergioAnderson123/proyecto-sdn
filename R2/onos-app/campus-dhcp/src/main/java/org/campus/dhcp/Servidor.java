package org.campus.dhcp;

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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * La logica del servidor DHCP por subred, sin servicios de ONOS: recibe la trama Ethernet de un DHCP del cliente y el (switch, puerto) por el que entro, y devuelve la
 * respuesta a enviar por ese mismo puerto y el evento que debe conocer ONOS (concesion confirmada o liberada). El tiempo entra como parametro.
 *
 * Defensas (ver docs/DISENO_DHCP_POR_SUBRED.md): solo se atiende lo que entra por un puerto de un ambito; la MAC del cliente (chaddr) debe ser la MAC de origen de la trama;
 * limite de tasa por puerto; una concesion por MAC; cuota por puerto; ofertas que vencen pronto; se ignoran las solicitudes dirigidas a otro servidor.
 */
public final class Servidor {

    public static final class Evento {
        public enum Tipo { CONCEDIDA, LIBERADA }

        public final Tipo tipo;
        public final MacAddress mac;
        public final Ip4Address ip;
        public final String subred;
        public final String puerto;

        Evento(Tipo tipo, MacAddress mac, int ip, String subred, String puerto) {
            this.tipo = tipo;
            this.mac = mac;
            this.ip = Ip4Address.valueOf(ip);
            this.subred = subred;
            this.puerto = puerto;
        }
    }

    /** Lo que hay que hacer con un paquete: contestar (o no) y/o avisar a ONOS; `motivo` explica por que no se contesto. */
    public static final class Salida {
        public final Ethernet respuesta;
        public final Evento evento;
        public final String motivo;

        Salida(Ethernet respuesta, Evento evento, String motivo) {
            this.respuesta = respuesta;
            this.evento = evento;
            this.motivo = motivo;
        }

        static Salida ignorar(String motivo) {
            return new Salida(null, null, motivo);
        }
    }

    private final List<Subred> subredes;
    private final Map<String, Pool> pools = new HashMap<>();
    private final Limitador limitador;

    public Servidor(List<Subred> subredes, Politica politica) {
        this.subredes = new ArrayList<>(subredes);
        for (Subred s : subredes) {
            pools.put(s.nombre, new Pool(s, politica));
        }
        this.limitador = new Limitador(politica.solicitudesPorSeg, politica.rafaga);
    }

    public Pool pool(String subred) {
        return pools.get(subred);
    }

    public Subred subredDe(String device, long puerto) {
        for (Subred s : subredes) {
            if (s.atiende(device, puerto)) {
                return s;
            }
        }
        return null;
    }

    public Salida procesar(Ethernet eth, String device, long puerto, long ahora) {
        Subred subred = subredDe(device, puerto);
        if (subred == null) {
            return Salida.ignorar("puerto sin ambito");
        }
        if (eth.getEtherType() != Ethernet.TYPE_IPV4 || !(eth.getPayload() instanceof IPv4)) {
            return Salida.ignorar("no es IPv4");
        }
        IPv4 ip = (IPv4) eth.getPayload();
        if (ip.getProtocol() != IPv4.PROTOCOL_UDP || !(ip.getPayload() instanceof UDP)) {
            return Salida.ignorar("no es UDP");
        }
        UDP udp = (UDP) ip.getPayload();
        if (udp.getSourcePort() != 68 || udp.getDestinationPort() != 67 || !(udp.getPayload() instanceof DHCP)) {
            return Salida.ignorar("no es DHCP de cliente");
        }
        DHCP dhcp = (DHCP) udp.getPayload();
        if (dhcp.getOpCode() != DHCP.OPCODE_REQUEST || dhcp.getHardwareType() != DHCP.HWTYPE_ETHERNET
                || dhcp.getClientHardwareAddress() == null || dhcp.getClientHardwareAddress().length < 6) {
            return Salida.ignorar("DHCP mal formado");
        }
        MacAddress mac = MacAddress.valueOf(Arrays.copyOf(dhcp.getClientHardwareAddress(), 6));
        if (!mac.equals(eth.getSourceMAC()) || mac.isMulticast() || mac.equals(MacAddress.ZERO)) {
            return Salida.ignorar("chaddr distinto de la MAC de origen (o invalida)");
        }
        MsgType tipo = dhcp.getPacketType();
        if (tipo == null) {
            return Salida.ignorar("sin tipo de mensaje");
        }
        String clavePuerto = device + "/" + puerto;
        Pool pool = pools.get(subred.nombre);

        switch (tipo) {
            case DHCPDISCOVER:
                return discover(subred, pool, dhcp, mac, clavePuerto, ahora);
            case DHCPREQUEST:
                return request(subred, pool, dhcp, mac, clavePuerto, ahora);
            case DHCPRELEASE: {
                Pool.Concesion c = pool.liberar(mac);
                return c == null ? Salida.ignorar("release sin concesion")
                        : new Salida(null, new Evento(Evento.Tipo.LIBERADA, mac, c.ip, subred.nombre, c.puerto), "liberada");
            }
            case DHCPDECLINE: {
                int rechazada = opcionIp(dhcp, DHCPOptionCode.OptionCode_RequestedIP);
                if (rechazada != 0) {
                    pool.declinar(mac, rechazada, ahora);
                }
                return Salida.ignorar("decline");
            }
            default:
                return Salida.ignorar("tipo no atendido " + tipo);
        }
    }

    /** Hace vencer las concesiones de todos los ambitos; devuelve un evento LIBERADA por cada una. */
    public List<Evento> vencer(long ahora) {
        List<Evento> eventos = new ArrayList<>();
        for (Subred s : subredes) {
            for (Pool.Concesion c : pools.get(s.nombre).vencer(ahora)) {
                eventos.add(new Evento(Evento.Tipo.LIBERADA, c.mac, c.ip, s.nombre, c.puerto));
            }
        }
        return eventos;
    }

    /** El puerto perdio el enlace: libera lo de ese puerto y devuelve un evento LIBERADA por cada concesion confirmada. `puerto` < 0 = todo el switch (cayo o se desconecto). */
    public List<Evento> liberarPuerto(String device, long puerto) {
        List<Evento> eventos = new ArrayList<>();
        for (Subred s : subredes) {
            if (!s.device.equals(device) || (puerto >= 0 && !s.puertos.contains(puerto))) {
                continue;
            }
            for (Pool.Concesion c : pools.get(s.nombre).liberarPuerto(puerto >= 0 ? device + "/" + puerto : device + "/")) {
                eventos.add(new Evento(Evento.Tipo.LIBERADA, c.mac, c.ip, s.nombre, c.puerto));
            }
        }
        return eventos;
    }

    /** Los switches que tienen algun ambito (para instalar ahi el flujo que sube las respuestas del sondeo). */
    public java.util.Set<String> dispositivos() {
        java.util.Set<String> ids = new java.util.TreeSet<>();
        for (Subred s : subredes) {
            ids.add(s.device);
        }
        return ids;
    }

    /** Las concesiones confirmadas de todos los ambitos, listas para el sondeo de presencia. */
    public List<Presencia.Objetivo> objetivos() {
        List<Presencia.Objetivo> lista = new ArrayList<>();
        for (Subred s : subredes) {
            for (Pool.Concesion c : pools.get(s.nombre).concesiones()) {
                lista.add(new Presencia.Objetivo(c.mac, c.ip, c.puerto, s.gateway));
            }
        }
        return lista;
    }

    /** Libera la concesion de un equipo que se dio por ausente. Devuelve el evento LIBERADA, o null si no tenia concesion confirmada. */
    public Evento liberarMac(MacAddress mac) {
        for (Subred s : subredes) {
            Pool.Concesion c = pools.get(s.nombre).liberar(mac);
            if (c != null) {
                return new Evento(Evento.Tipo.LIBERADA, c.mac, c.ip, s.nombre, c.puerto);
            }
        }
        return null;
    }

    private Salida discover(Subred subred, Pool pool, DHCP dhcp, MacAddress mac, String clavePuerto, long ahora) {
        if (!limitador.permitir(clavePuerto, ahora)) {
            return Salida.ignorar("limite de tasa del puerto " + clavePuerto);
        }
        Pool.Oferta oferta = pool.ofrecer(mac, clavePuerto, opcionIp(dhcp, DHCPOptionCode.OptionCode_RequestedIP), ahora);
        if (oferta.ip == null) {
            return Salida.ignorar(oferta.rechazo == Pool.Rechazo.CUOTA_PUERTO ? "cuota del puerto " + clavePuerto + " agotada" : "ambito agotado");
        }
        return new Salida(respuesta(subred, dhcp, mac, MsgType.DHCPOFFER, oferta.ip), null, "oferta");
    }

    private Salida request(Subred subred, Pool pool, DHCP dhcp, MacAddress mac, String clavePuerto, long ahora) {
        int servidor = opcionIp(dhcp, DHCPOptionCode.OptionCode_DHCPServerIp);
        if (servidor != 0 && servidor != subred.servidorIp) {
            return Salida.ignorar("el cliente eligio otro servidor");
        }
        if (!limitador.permitir(clavePuerto, ahora)) {
            return Salida.ignorar("limite de tasa del puerto " + clavePuerto);
        }
        int pedida = opcionIp(dhcp, DHCPOptionCode.OptionCode_RequestedIP);
        if (pedida == 0) {
            pedida = dhcp.getClientIPAddress();
        }
        if (pedida == 0) {
            return Salida.ignorar("REQUEST sin IP");
        }
        Pool.Concesion c = subred.contiene(pedida) ? pool.confirmar(mac, clavePuerto, pedida, ahora) : null;
        if (c == null) {
            return new Salida(respuesta(subred, dhcp, mac, MsgType.DHCPNAK, 0), null, "nak");
        }
        return new Salida(respuesta(subred, dhcp, mac, MsgType.DHCPACK, c.ip),
                new Evento(Evento.Tipo.CONCEDIDA, mac, c.ip, subred.nombre, c.puerto), "ack");
    }

    // ---- construccion de la respuesta ----
    private Ethernet respuesta(Subred s, DHCP pedido, MacAddress mac, MsgType tipo, int yiaddr) {
        DHCP r = new DHCP();
        r.setOpCode(DHCP.OPCODE_REPLY);
        r.setHardwareType(DHCP.HWTYPE_ETHERNET);
        r.setHardwareAddressLength((byte) 6);
        r.setTransactionId(pedido.getTransactionId());
        r.setFlags(pedido.getFlags());
        r.setYourIPAddress(yiaddr);
        r.setServerIPAddress(s.servidorIp);
        r.setGatewayIPAddress(pedido.getGatewayIPAddress());
        r.setClientHardwareAddress(pedido.getClientHardwareAddress());
        List<DhcpOption> opciones = new ArrayList<>();
        opciones.add(opcion(DHCPOptionCode.OptionCode_MessageType, new byte[]{(byte) tipo.getValue()}));
        opciones.add(opcion(DHCPOptionCode.OptionCode_DHCPServerIp, entero(s.servidorIp)));
        if (tipo != MsgType.DHCPNAK) {
            opciones.add(opcion(DHCPOptionCode.OptionCode_LeaseTime, entero(s.leaseSeg)));
            opciones.add(opcion(DHCPOptionCode.OptionCode_RenewalTime, entero(s.leaseSeg / 2)));
            opciones.add(opcion(DHCPOptionCode.OPtionCode_RebindingTime, entero((int) (s.leaseSeg * 7L / 8))));
            opciones.add(opcion(DHCPOptionCode.OptionCode_SubnetMask, entero(s.mascara)));
            opciones.add(opcion(DHCPOptionCode.OptionCode_RouterAddress, entero(s.gateway)));
            opciones.add(opcion(DHCPOptionCode.OptionCode_BroadcastAddress, entero(s.broadcast())));
            if (!s.dns.isEmpty()) {
                ByteBuffer b = ByteBuffer.allocate(4 * s.dns.size());
                for (int d : s.dns) {
                    b.putInt(d);
                }
                opciones.add(opcion(DHCPOptionCode.OptionCode_DomainServer, b.array()));
            }
        }
        DhcpOption fin = new DhcpOption();
        fin.setCode(DHCPOptionCode.OptionCode_END.getValue());
        fin.setLength((byte) 0);
        opciones.add(fin);
        r.setOptions(opciones);

        UDP udp = new UDP();
        udp.setSourcePort(67);
        udp.setDestinationPort(68);
        udp.setPayload(r);
        IPv4 ip = new IPv4();
        ip.setSourceAddress(s.servidorIp);
        ip.setDestinationAddress(Ip4Address.valueOf("255.255.255.255").toInt());
        ip.setTtl((byte) 64);
        ip.setProtocol(IPv4.PROTOCOL_UDP);
        ip.setPayload(udp);
        Ethernet eth = new Ethernet();
        eth.setSourceMACAddress(s.servidorMac);
        eth.setDestinationMACAddress(mac);
        eth.setEtherType(Ethernet.TYPE_IPV4);
        eth.setPayload(ip);
        return eth;
    }

    private static DhcpOption opcion(DHCPOptionCode codigo, byte[] datos) {
        DhcpOption o = new DhcpOption();
        o.setCode(codigo.getValue());
        o.setLength((byte) datos.length);
        o.setData(datos);
        return o;
    }

    private static byte[] entero(int v) {
        return ByteBuffer.allocate(4).putInt(v).array();
    }

    private static int opcionIp(DHCP dhcp, DHCPOptionCode codigo) {
        DhcpOption o = dhcp.getOption(codigo);
        return o == null || o.getData() == null || o.getData().length != 4 ? 0 : ByteBuffer.wrap(o.getData()).getInt();
    }
}
