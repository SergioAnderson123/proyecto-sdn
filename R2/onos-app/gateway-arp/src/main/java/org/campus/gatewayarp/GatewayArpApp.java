package org.campus.gatewayarp;

import org.onlab.packet.ARP;
import org.onlab.packet.Ethernet;
import org.onlab.packet.MacAddress;
import org.onosproject.core.ApplicationId;
import org.onosproject.core.CoreService;
import org.onosproject.net.Device;
import org.onosproject.net.DeviceId;
import org.onosproject.net.PortNumber;
import org.onosproject.net.behaviour.ExtensionTreatmentResolver;
import org.onosproject.net.config.ConfigFactory;
import org.onosproject.net.config.NetworkConfigEvent;
import org.onosproject.net.config.NetworkConfigListener;
import org.onosproject.net.config.NetworkConfigRegistry;
import org.onosproject.net.config.basics.SubjectFactories;
import org.onosproject.net.device.DeviceEvent;
import org.onosproject.net.device.DeviceListener;
import org.onosproject.net.device.DeviceService;
import org.onosproject.net.driver.DefaultDriverData;
import org.onosproject.net.driver.DefaultDriverHandler;
import org.onosproject.net.driver.Driver;
import org.onosproject.net.driver.DriverService;
import org.onosproject.net.flow.DefaultFlowRule;
import org.onosproject.net.flow.DefaultTrafficSelector;
import org.onosproject.net.flow.DefaultTrafficTreatment;
import org.onosproject.net.flow.FlowRule;
import org.onosproject.net.flow.FlowRuleService;
import org.onosproject.net.flow.TrafficSelector;
import org.onosproject.net.flow.TrafficTreatment;
import org.onosproject.net.flow.instructions.ExtensionTreatment;
import org.osgi.service.component.annotations.Activate;
import org.osgi.service.component.annotations.Component;
import org.osgi.service.component.annotations.Deactivate;
import org.osgi.service.component.annotations.Reference;
import org.osgi.service.component.annotations.ReferenceCardinality;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import static org.onosproject.net.flow.instructions.ExtensionTreatmentType.ExtensionTreatmentTypes.NICIRA_MOV_ARP_SHA_TO_THA;
import static org.onosproject.net.flow.instructions.ExtensionTreatmentType.ExtensionTreatmentTypes.NICIRA_MOV_ARP_SPA_TO_TPA;
import static org.onosproject.net.flow.instructions.ExtensionTreatmentType.ExtensionTreatmentTypes.NICIRA_MOV_ETH_SRC_TO_DST;

/**
 * Gateway ARP: en cada switch de acceso, contesta el ARP del gateway virtual de la subred SIN controlador (la petición se convierte en respuesta dentro del propio switch) con flujos que instala
 * ONOS. Así son flujos de ONOS (appId propio): no se borran como "extraños" (allowExtraneousRules puede seguir en false) y ONOS los reinstala cuando el switch se reconecta.
 *
 * Por cada (switch, puerto de acceso, gateway): coincidencia = ARP petición con IP objetivo = IP del gateway entrando por ese puerto; acciones = copiar eth_src a eth_dst, eth_src = MAC común,
 * arp_op = respuesta, copiar sha a tha y spa a tpa, sha = MAC común, spa = IP del gateway, salir por el puerto de entrada.
 * Se necesitan las extensiones de Nicira (copia de campos) y los criterios/instrucciones ARP, que el REST de ONOS 2.7 no acepta pero la API Java sí (así lo hace la app openstacknetworking).
 */
@Component(immediate = true)
public class GatewayArpApp {

    private static final String APP = "org.campus.gatewayarp";
    private static final String CLAVE_CONFIG = "gatewayarp";

    private final Logger log = LoggerFactory.getLogger(getClass());

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected CoreService coreService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected FlowRuleService flowRuleService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected DeviceService deviceService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected NetworkConfigRegistry configRegistry;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected DriverService driverService;

    private ApplicationId appId;

    private final ConfigFactory<ApplicationId, GatewayConfig> fabricaConfig =
            new ConfigFactory<ApplicationId, GatewayConfig>(SubjectFactories.APP_SUBJECT_FACTORY, GatewayConfig.class, CLAVE_CONFIG) {
                @Override
                public GatewayConfig createConfig() {
                    return new GatewayConfig();
                }
            };

    private final NetworkConfigListener oyenteConfig = new NetworkConfigListener() {
        @Override
        public void event(NetworkConfigEvent evento) {
            if (evento.configClass() != null && evento.configClass().equals(GatewayConfig.class)) {
                log.info("Gateway ARP: cambió la configuración, se reinstalan los flujos");
                reinstalar();
            }
        }
    };

    private final DeviceListener oyenteDispositivos = new DeviceListener() {
        @Override
        public void event(DeviceEvent evento) {
            DeviceEvent.Type t = evento.type();
            if (t == DeviceEvent.Type.DEVICE_ADDED || t == DeviceEvent.Type.DEVICE_AVAILABILITY_CHANGED || t == DeviceEvent.Type.DEVICE_UPDATED) {
                instalar();
            }
        }
    };

    @Activate
    protected void activate() {
        appId = coreService.registerApplication(APP);
        configRegistry.registerConfigFactory(fabricaConfig);
        configRegistry.addListener(oyenteConfig);
        deviceService.addListener(oyenteDispositivos);
        instalar();
        log.info("Gateway ARP iniciado (appId {})", appId.id());
    }

    @Deactivate
    protected void deactivate() {
        deviceService.removeListener(oyenteDispositivos);
        configRegistry.removeListener(oyenteConfig);
        configRegistry.unregisterConfigFactory(fabricaConfig);
        flowRuleService.removeFlowRulesById(appId);
        log.info("Gateway ARP detenido: flujos retirados");
    }

    /** Quita los flujos de la app y los vuelve a poner con la configuración actual. */
    private void reinstalar() {
        flowRuleService.removeFlowRulesById(appId);
        instalar();
    }

    /** Aplica los flujos de cada gateway cuyo switch ya está disponible; ONOS los conserva y los reinstala solo si el switch se reconecta. applyFlowRules es idempotente. */
    private void instalar() {
        GatewayConfig cfg = configRegistry.getConfig(appId, GatewayConfig.class);
        if (cfg == null || !cfg.isValid()) {
            log.warn("Gateway ARP: sin configuración válida (clave '{}' en apps/{}); no se instala nada", CLAVE_CONFIG, APP);
            return;
        }
        MacAddress gwMac = cfg.gwMac();
        int instalados = 0;
        for (GatewayConfig.Gateway g : cfg.gateways()) {
            Device dispositivo = deviceService.getDevice(g.device);
            if (dispositivo == null || !deviceService.isAvailable(g.device)) {
                log.info("Gateway ARP: {} aún no está disponible; se instalará cuando aparezca", g.device);
                continue;
            }
            Driver driver = driverService.getDriver(g.device);
            if (driver == null || !driver.hasBehaviour(ExtensionTreatmentResolver.class)) {
                log.error("Gateway ARP: el driver de {} ({}) no tiene ExtensionTreatmentResolver; se omite", g.device, driver == null ? "sin driver" : driver.name());
                continue;
            }
            DefaultDriverHandler manejador = new DefaultDriverHandler(new DefaultDriverData(driver, g.device));
            ExtensionTreatmentResolver resolutor = driver.createBehaviour(manejador, ExtensionTreatmentResolver.class);
            ExtensionTreatment ethSrcADst = resolutor.getExtensionInstruction(NICIRA_MOV_ETH_SRC_TO_DST.type());
            ExtensionTreatment shaATha = resolutor.getExtensionInstruction(NICIRA_MOV_ARP_SHA_TO_THA.type());
            ExtensionTreatment spaATpa = resolutor.getExtensionInstruction(NICIRA_MOV_ARP_SPA_TO_TPA.type());
            for (PortNumber puerto : g.ports) {
                TrafficSelector seleccion = DefaultTrafficSelector.builder()
                        .matchInPort(puerto)
                        .matchEthType(Ethernet.TYPE_ARP)
                        .matchArpOp(ARP.OP_REQUEST)
                        .matchArpTpa(g.ip)
                        .build();
                TrafficTreatment.Builder tratamiento = DefaultTrafficTreatment.builder();
                if (cfg.copiarArp()) {
                    tratamiento.setOutput(PortNumber.CONTROLLER);          // antes de modificar: ONOS recibe la petición original
                }
                tratamiento.extension(ethSrcADst, g.device)
                        .setEthSrc(gwMac)
                        .setArpOp(ARP.OP_REPLY)
                        .extension(shaATha, g.device)
                        .extension(spaATpa, g.device)
                        .setArpSha(gwMac)
                        .setArpSpa(g.ip)
                        .setOutput(PortNumber.IN_PORT);
                FlowRule regla = DefaultFlowRule.builder()
                        .forDevice(g.device)
                        .fromApp(appId)
                        .withSelector(seleccion)
                        .withTreatment(tratamiento.build())
                        .withPriority(cfg.prioridad())
                        .forTable(cfg.tabla())
                        .makePermanent()
                        .build();
                flowRuleService.applyFlowRules(regla);
                instalados++;
            }
        }
        log.info("Gateway ARP: {} flujos aplicados", instalados);
    }
}
