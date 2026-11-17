package org.campus.dhcp;

import com.google.common.collect.ImmutableSet;
import org.onlab.packet.ARP;
import org.onlab.packet.Ethernet;
import org.onlab.packet.IPv4;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;
import org.onlab.packet.TpPort;
import org.onlab.packet.VlanId;
import org.onosproject.core.ApplicationId;
import org.onosproject.core.CoreService;
import org.onosproject.net.ConnectPoint;
import org.onosproject.net.DeviceId;
import org.onosproject.net.HostId;
import org.onosproject.net.HostLocation;
import org.onosproject.net.Port;
import org.onosproject.net.PortNumber;
import org.onosproject.net.config.ConfigFactory;
import org.onosproject.net.config.NetworkConfigEvent;
import org.onosproject.net.config.NetworkConfigListener;
import org.onosproject.net.config.NetworkConfigRegistry;
import org.onosproject.net.config.basics.SubjectFactories;
import org.onosproject.net.device.DeviceEvent;
import org.onosproject.net.device.DeviceListener;
import org.onosproject.net.device.DeviceService;
import org.onosproject.net.flow.DefaultFlowRule;
import org.onosproject.net.flow.DefaultTrafficSelector;
import org.onosproject.net.flow.FlowRule;
import org.onosproject.net.flow.FlowRuleEvent;
import org.onosproject.net.flow.FlowRuleListener;
import org.onosproject.net.flow.FlowRuleService;
import org.onosproject.net.flow.criteria.Criterion;
import org.onosproject.net.flow.criteria.EthCriterion;
import org.onosproject.net.flow.DefaultTrafficTreatment;
import org.onosproject.net.flow.TrafficSelector;
import org.onosproject.net.host.DefaultHostDescription;
import org.onosproject.net.host.HostProvider;
import org.onosproject.net.host.HostProviderRegistry;
import org.onosproject.net.host.HostProviderService;
import org.onosproject.net.packet.DefaultOutboundPacket;
import org.onosproject.net.packet.InboundPacket;
import org.onosproject.net.packet.PacketContext;
import org.onosproject.net.packet.PacketPriority;
import org.onosproject.net.packet.PacketProcessor;
import org.onosproject.net.packet.PacketService;
import org.onosproject.net.provider.AbstractProvider;
import org.onosproject.net.provider.ProviderId;
import org.osgi.service.component.annotations.Activate;
import org.osgi.service.component.annotations.Component;
import org.osgi.service.component.annotations.Deactivate;
import org.osgi.service.component.annotations.Reference;
import org.osgi.service.component.annotations.ReferenceCardinality;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * DHCP por subred (ver docs/DISENO_DHCP_POR_SUBRED.md): atiende el DHCP del cliente que R2 sube al controlador, da la IP del ambito del switch y puerto por el que llego, aplica los limites contra
 * el agotamiento (cuota por puerto, tasa, validacion; ver Servidor) y avisa a ONOS de la IP de cada host para que el vigilante de la capa 3 la conozca. La app DHCP estandar de ONOS
 * (org.onosproject.dhcp) debe estar DESACTIVADA: dos servidores contestando al mismo paquete se pelean.
 *
 * Primera version: las concesiones viven en la memoria de este nodo (un cambio de nodo maestro del switch las pierde hasta que los clientes renueven).
 */
@Component(immediate = true)
public class CampusDhcpApp {

    private static final String APP = "org.campus.dhcp";
    private static final String CLAVE_CONFIG = "campusdhcp";
    private static final long AVISO_CADA_MS = 10_000;
    private static final int TABLA_REENVIO = 60;
    private static final int PRIORIDAD_RESPUESTA_ARP = 310;      // ver R5/deploy/prioridades.yaml
    private static final int PRIO_SESION = 40001;               // el flujo de sesion de R2 (tabla 0, in_port + eth_src)

    private final Logger log = LoggerFactory.getLogger(getClass());

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected CoreService coreService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected PacketService packetService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected NetworkConfigRegistry configRegistry;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected HostProviderRegistry hostProviderRegistry;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected DeviceService deviceService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected FlowRuleService flowRuleService;

    private ApplicationId appId;
    private volatile Servidor servidor;
    private HostProviderService hostProviderService;
    private ScheduledExecutorService reloj;
    private ScheduledExecutorService avisos;                                   // un solo hilo para hablar con el Policy Mapper sin frenar al latido
    private volatile Presencia presencia;
    private volatile MacAddress gwMac;
    private volatile String pmUrl = "";
    private volatile String pmToken = "";
    private final Map<String, Long> ultimoAviso = new HashMap<>();

    /** El proveedor con el que esta app le dice a ONOS que host tiene que IP. */
    private static final class ProveedorDhcp extends AbstractProvider implements HostProvider {
        ProveedorDhcp() {
            super(new ProviderId("dhcp", APP));
        }

        @Override
        public void triggerProbe(org.onosproject.net.Host host) {
            // no se sondea nada: la IP la conocemos por la concesion
        }
    }

    private final ProveedorDhcp proveedor = new ProveedorDhcp();

    private final ConfigFactory<ApplicationId, DhcpConfig> fabricaConfig =
            new ConfigFactory<ApplicationId, DhcpConfig>(SubjectFactories.APP_SUBJECT_FACTORY, DhcpConfig.class, CLAVE_CONFIG) {
                @Override
                public DhcpConfig createConfig() {
                    return new DhcpConfig();
                }
            };

    private final NetworkConfigListener oyenteConfig = new NetworkConfigListener() {
        @Override
        public void event(NetworkConfigEvent evento) {
            if (evento.configClass() != null && evento.configClass().equals(DhcpConfig.class)) {
                log.info("DHCP por subred: cambio la configuracion, se recargan los ambitos (las concesiones en memoria se pierden)");
                cargarConfig();
            }
        }
    };

    /** Un puerto sin enlace (o un switch caido) libera al instante las concesiones que tenia: el equipo ya no esta, su IP y su regla /32 no deben quedar esperando a que venza la concesion. */
    private final DeviceListener oyenteDispositivos = new DeviceListener() {
        @Override
        public void event(DeviceEvent evento) {
            if (evento.type() == DeviceEvent.Type.DEVICE_ADDED || evento.type() == DeviceEvent.Type.DEVICE_UPDATED
                    || evento.type() == DeviceEvent.Type.DEVICE_AVAILABILITY_CHANGED) {
                instalarFlujosRespuesta();
            }
            Servidor s = servidor;
            if (s == null) {
                return;
            }
            String device = evento.subject().id().toString();
            java.util.List<Servidor.Evento> liberadas = java.util.Collections.emptyList();
            if (evento.type() == DeviceEvent.Type.PORT_UPDATED || evento.type() == DeviceEvent.Type.PORT_REMOVED) {
                Port puerto = evento.port();
                if (puerto != null && (evento.type() == DeviceEvent.Type.PORT_REMOVED || !puerto.isEnabled())) {
                    liberadas = s.liberarPuerto(device, puerto.number().toLong());
                }
            } else if (evento.type() == DeviceEvent.Type.DEVICE_AVAILABILITY_CHANGED && !deviceService.isAvailable(evento.subject().id())) {
                liberadas = s.liberarPuerto(device, -1);
            }
            for (Servidor.Evento e : liberadas) {
                hostProviderService.removeIpFromHost(HostId.hostId(e.mac, VlanId.NONE), e.ip);
                log.info("DHCP por subred: {} sin enlace en {}, se libera {} ({})", e.mac, e.puerto, e.ip, e.subred);
            }
        }
    };

    /**
     * R1 (Policy Mapper) borra el flujo de sesion 40001 cuando termina una sesion: logoff del agente, revocacion de R5, cierre del portal. Esa baja es una SOSPECHA de que el equipo ya no esta:
     * se le sondea por ARP y solo se libera su IP si no contesta (un equipo vivo, p. ej. revocado por R5 o con un ascenso del portal, que borra y vuelve a poner el flujo, conserva su concesion).
     */
    private final FlowRuleListener oyenteFlujos = new FlowRuleListener() {
        @Override
        public void event(FlowRuleEvent evento) {
            if (evento.type() != FlowRuleEvent.Type.RULE_REMOVED) {
                return;
            }
            FlowRule regla = evento.subject();
            Presencia p = presencia;
            if (p == null || regla.priority() != PRIO_SESION || regla.tableId() != 0) {
                return;
            }
            Criterion c = regla.selector().getCriterion(Criterion.Type.ETH_SRC);
            if (c instanceof EthCriterion && regla.selector().getCriterion(Criterion.Type.IN_PORT) != null) {
                p.sospechar(((EthCriterion) c).mac(), System.currentTimeMillis());
            }
        }
    };

    private final PacketProcessor procesador = new PacketProcessor() {
        @Override
        public void process(PacketContext contexto) {
            atender(contexto);
        }
    };

    private final TrafficSelector selectorDhcp = DefaultTrafficSelector.builder()
            .matchEthType(Ethernet.TYPE_IPV4)
            .matchIPProtocol(IPv4.PROTOCOL_UDP)
            .matchUdpSrc(TpPort.tpPort(68))
            .matchUdpDst(TpPort.tpPort(67))
            .build();

    @Activate
    protected void activate() {
        appId = coreService.registerApplication(APP);
        configRegistry.registerConfigFactory(fabricaConfig);
        configRegistry.addListener(oyenteConfig);
        hostProviderService = hostProviderRegistry.register(proveedor);
        deviceService.addListener(oyenteDispositivos);
        flowRuleService.addListener(oyenteFlujos);
        cargarConfig();
        packetService.addProcessor(procesador, PacketProcessor.director(1));
        packetService.requestPackets(selectorDhcp, PacketPriority.CONTROL, appId);
        reloj = Executors.newSingleThreadScheduledExecutor();
        reloj.scheduleWithFixedDelay(this::vencer, 10, 10, TimeUnit.SECONDS);
        reloj.scheduleWithFixedDelay(this::latido, 1, 1, TimeUnit.SECONDS);
        avisos = Executors.newSingleThreadScheduledExecutor();
        log.info("DHCP por subred iniciado (appId {})", appId.id());
    }

    @Deactivate
    protected void deactivate() {
        reloj.shutdownNow();
        avisos.shutdownNow();
        flowRuleService.removeListener(oyenteFlujos);
        flowRuleService.removeFlowRulesById(appId);
        deviceService.removeListener(oyenteDispositivos);
        packetService.cancelPackets(selectorDhcp, PacketPriority.CONTROL, appId);
        packetService.removeProcessor(procesador);
        hostProviderRegistry.unregister(proveedor);
        configRegistry.removeListener(oyenteConfig);
        configRegistry.unregisterConfigFactory(fabricaConfig);
        log.info("DHCP por subred detenido");
    }

    private void cargarConfig() {
        DhcpConfig cfg = configRegistry.getConfig(appId, DhcpConfig.class);
        if (cfg == null || !cfg.isValid()) {
            servidor = null;
            log.warn("DHCP por subred: sin configuracion valida (clave '{}' en apps/{}); no se atiende ningun puerto", CLAVE_CONFIG, APP);
            return;
        }
        servidor = new Servidor(cfg.subredes(), cfg.politica());
        gwMac = cfg.gwMac();
        presencia = gwMac == null ? null : cfg.presencia();
        pmUrl = cfg.policyMapperUrl();
        pmToken = cfg.policyMapperToken();
        log.info("DHCP por subred: {} ambitos cargados; sondeo de presencia {}; aviso al Policy Mapper {}", cfg.subredes().size(),
                presencia == null ? "desactivado (falta gwMac)" : "activo", pmUrl.isEmpty() ? "desactivado" : pmUrl);
        instalarFlujosRespuesta();
    }

    /** Un paquete del controlador: si es DHCP de un puerto con ambito, contesta y avisa a ONOS; los demas se dejan pasar a otras apps. */
    private void atender(PacketContext contexto) {
        Servidor s = servidor;
        if (s == null || contexto.isHandled()) {
            return;
        }
        InboundPacket paquete = contexto.inPacket();
        Ethernet eth = paquete.parsed();
        if (eth != null && eth.getEtherType() == Ethernet.TYPE_ARP) {
            respuestaDeSondeo(contexto, eth);
            return;
        }
        if (eth == null || eth.getEtherType() != Ethernet.TYPE_IPV4) {
            return;
        }
        ConnectPoint origen = paquete.receivedFrom();
        String device = origen.deviceId().toString();
        long puerto = origen.port().toLong();
        if (s.subredDe(device, puerto) == null) {
            return;                                                          // no es un puerto nuestro: otra app puede atenderlo
        }
        Servidor.Salida salida;
        try {
            salida = s.procesar(eth, device, puerto, System.currentTimeMillis());
        } catch (RuntimeException e) {
            log.error("DHCP por subred: error procesando un paquete de {}/{}", device, puerto, e);
            return;
        }
        contexto.block();                                                    // este puerto es nuestro: que ninguna otra app conteste el mismo DHCP
        if (salida.respuesta != null) {
            enviar(origen, salida.respuesta);
        } else {
            avisar(salida.motivo, device + "/" + puerto);
        }
        if (salida.evento != null) {
            avisarOnos(salida.evento, origen);
        }
    }

    private void enviar(ConnectPoint destino, Ethernet respuesta) {
        packetService.emit(new DefaultOutboundPacket(destino.deviceId(),
                DefaultTrafficTreatment.builder().setOutput(destino.port()).build(), ByteBuffer.wrap(respuesta.serialize())));
    }

    /** Le dice a ONOS que host tiene que IP (o que la dejo): asi `/hosts` trae la IP y el vigilante de capa 3 monta la entrega /32. */
    private void avisarOnos(Servidor.Evento e, ConnectPoint punto) {
        HostId id = HostId.hostId(e.mac, VlanId.NONE);
        if (e.tipo == Servidor.Evento.Tipo.CONCEDIDA) {
            hostProviderService.hostDetected(id,
                    new DefaultHostDescription(e.mac, VlanId.NONE, new HostLocation(punto, System.currentTimeMillis()), ImmutableSet.of(e.ip), false), false);
            log.info("DHCP por subred: {} -> {} ({} en {})", e.mac, e.ip, e.subred, e.puerto);
        } else {
            hostProviderService.removeIpFromHost(id, e.ip);
            log.info("DHCP por subred: {} libera {} ({})", e.mac, e.ip, e.subred);
        }
    }

    // ------------------------------------------------------------------ presencia (sondeo ARP)

    /** Flujo propio en la tabla 60 de cada switch con ambito: las respuestas ARP dirigidas a la MAC del gateway suben al controlador (las del sondeo). Idempotente; ONOS lo repone si el switch vuelve. */
    private void instalarFlujosRespuesta() {
        MacAddress gw = gwMac;
        Servidor s = servidor;
        if (gw == null || s == null) {
            return;
        }
        for (String id : s.dispositivos()) {
            DeviceId dispositivo = DeviceId.deviceId(id);
            if (deviceService.getDevice(dispositivo) == null || !deviceService.isAvailable(dispositivo)) {
                continue;
            }
            FlowRule regla = DefaultFlowRule.builder()
                    .forDevice(dispositivo)
                    .fromApp(appId)
                    .withSelector(DefaultTrafficSelector.builder().matchEthType(Ethernet.TYPE_ARP).matchArpOp(ARP.OP_REPLY).matchEthDst(gw).build())
                    .withTreatment(DefaultTrafficTreatment.builder().setOutput(PortNumber.CONTROLLER).build())
                    .withPriority(PRIORIDAD_RESPUESTA_ARP)
                    .forTable(TABLA_REENVIO)
                    .makePermanent()
                    .build();
            flowRuleService.applyFlowRules(regla);
        }
    }

    /** Una respuesta ARP dirigida al gateway: si es de un equipo vigilado, esta vivo. */
    private void respuestaDeSondeo(PacketContext contexto, Ethernet eth) {
        Presencia p = presencia;
        MacAddress gw = gwMac;
        if (p == null || gw == null || !(eth.getPayload() instanceof ARP)) {
            return;
        }
        ARP arp = (ARP) eth.getPayload();
        if (arp.getOpCode() != ARP.OP_REPLY || !gw.equals(eth.getDestinationMAC())) {
            return;
        }
        p.respuesta(eth.getSourceMAC(), System.currentTimeMillis());
        contexto.block();
    }

    /** Cada segundo: los sondeos que toquen y los equipos que no contestaron. */
    private void latido() {
        try {
            Servidor s = servidor;
            Presencia p = presencia;
            if (s == null || p == null) {
                return;
            }
            for (Presencia.Orden orden : p.tick(System.currentTimeMillis(), s.objetivos())) {
                if (orden.tipo == Presencia.Tipo.SONDEAR) {
                    sondear(orden.objetivo);
                } else {
                    ausente(s, orden.objetivo);
                }
            }
        } catch (RuntimeException ex) {
            log.error("DHCP por subred: error en el sondeo de presencia", ex);
        }
    }

    /** ARP unicast "quien tiene la IP del equipo?" desde el gateway, por el puerto del equipo. El nucleo de cualquier equipo contesta aunque tenga firewall. */
    private void sondear(Presencia.Objetivo o) {
        MacAddress gw = gwMac;
        int corte = o.puerto.lastIndexOf('/');
        if (gw == null || corte < 0) {
            return;
        }
        ARP arp = new ARP();
        arp.setHardwareType(ARP.HW_TYPE_ETHERNET);
        arp.setProtocolType(ARP.PROTO_TYPE_IP);
        arp.setHardwareAddressLength((byte) Ethernet.DATALAYER_ADDRESS_LENGTH);
        arp.setProtocolAddressLength((byte) Ip4Address.BYTE_LENGTH);
        arp.setOpCode(ARP.OP_REQUEST);
        arp.setSenderHardwareAddress(gw.toBytes());
        arp.setSenderProtocolAddress(Ip4Address.valueOf(o.gateway).toOctets());
        arp.setTargetHardwareAddress(MacAddress.ZERO.toBytes());
        arp.setTargetProtocolAddress(Ip4Address.valueOf(o.ip).toOctets());
        Ethernet eth = new Ethernet();
        eth.setSourceMACAddress(gw);
        eth.setDestinationMACAddress(o.mac);
        eth.setEtherType(Ethernet.TYPE_ARP);
        eth.setPayload(arp);
        packetService.emit(new DefaultOutboundPacket(DeviceId.deviceId(o.puerto.substring(0, corte)),
                DefaultTrafficTreatment.builder().setOutput(PortNumber.portNumber(Long.parseLong(o.puerto.substring(corte + 1)))).build(),
                ByteBuffer.wrap(eth.serialize())));
    }

    /** No contesto: se libera su IP (ONOS pierde la IP y el vigilante retira su regla /32) y se avisa al Policy Mapper para que cierre su sesion y su rol. */
    private void ausente(Servidor s, Presencia.Objetivo o) {
        Servidor.Evento e = s.liberarMac(o.mac);
        if (e != null) {
            hostProviderService.removeIpFromHost(HostId.hostId(e.mac, VlanId.NONE), e.ip);
        }
        log.info("DHCP por subred: {} ({}) no contesta al sondeo ARP; se libera su concesion y se avisa al Policy Mapper", o.mac, Ip4Address.valueOf(o.ip));
        avisarPolicyMapper(o.mac);
    }

    private void avisarPolicyMapper(MacAddress mac) {
        String url = pmUrl;
        String token = pmToken;
        if (url.isEmpty() || token.isEmpty()) {
            return;
        }
        avisos.execute(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url.replaceAll("/+$", "") + "/sessions/ausente").openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(3000);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setRequestProperty("X-Presencia-Token", token);
                byte[] cuerpo = ("{\"mac\":\"" + mac + "\",\"motivo\":\"sin respuesta al sondeo ARP\"}").getBytes(StandardCharsets.UTF_8);
                try (OutputStream salida = c.getOutputStream()) {
                    salida.write(cuerpo);
                }
                log.info("DHCP por subred: el Policy Mapper contesto {} al cierre de la sesion de {}", c.getResponseCode(), mac);
                c.disconnect();
            } catch (Exception ex) {
                log.warn("DHCP por subred: no se pudo avisar al Policy Mapper del cierre de {}: {}", mac, ex.toString());
            }
        });
    }

    private void vencer() {
        try {
            Servidor s = servidor;
            if (s == null) {
                return;
            }
            for (Servidor.Evento e : s.vencer(System.currentTimeMillis())) {
                hostProviderService.removeIpFromHost(HostId.hostId(e.mac, VlanId.NONE), e.ip);
                log.info("DHCP por subred: vencio la concesion de {} ({}, {})", e.mac, e.ip, e.subred);
            }
        } catch (RuntimeException ex) {
            log.error("DHCP por subred: error al vencer concesiones", ex);
        }
    }

    /** Registro de lo ignorado, como mucho una vez cada 10 s por motivo y puerto (una inundacion no llena el log). */
    private void avisar(String motivo, String puerto) {
        if (motivo == null || motivo.equals("oferta") || motivo.equals("liberada") || motivo.equals("decline")) {
            return;
        }
        String clave = motivo + "@" + puerto;
        long ahora = System.currentTimeMillis();
        synchronized (ultimoAviso) {
            Long antes = ultimoAviso.get(clave);
            if (antes != null && ahora - antes < AVISO_CADA_MS) {
                return;
            }
            ultimoAviso.put(clave, ahora);
            if (ultimoAviso.size() > 1000) {
                ultimoAviso.values().removeIf(t -> ahora - t > 60_000);
            }
        }
        log.warn("DHCP por subred: no se atiende un paquete de {}: {}", puerto, motivo);
    }
}
