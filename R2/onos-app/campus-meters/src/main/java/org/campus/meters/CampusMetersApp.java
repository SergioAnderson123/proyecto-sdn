package org.campus.meters;

import org.onlab.packet.Ethernet;
import org.onlab.packet.IPv4;
import org.onlab.packet.TpPort;
import org.onosproject.core.ApplicationId;
import org.onosproject.core.CoreService;
import org.onosproject.net.DeviceId;
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
import org.onosproject.net.flow.DefaultTrafficTreatment;
import org.onosproject.net.flow.FlowRule;
import org.onosproject.net.flow.FlowRuleService;
import org.onosproject.net.flow.TrafficSelector;
import org.onosproject.net.meter.Band;
import org.onosproject.net.meter.DefaultBand;
import org.onosproject.net.meter.DefaultMeterRequest;
import org.onosproject.net.meter.Meter;
import org.onosproject.net.meter.MeterId;
import org.onosproject.net.meter.MeterRequest;
import org.onosproject.net.meter.MeterService;
import org.onosproject.net.meter.MeterState;
import org.osgi.service.component.annotations.Activate;
import org.osgi.service.component.annotations.Component;
import org.osgi.service.component.annotations.Deactivate;
import org.osgi.service.component.annotations.Reference;
import org.osgi.service.component.annotations.ReferenceCardinality;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Meters por puerto (ver docs/DISENO_METERS_POR_PUERTO.md): por cada puerto de acceso del plan y cada clase (EAPOL, DHCP) crea un medidor con el servicio de meters de ONOS (banda DROP) y una regla ADITIVA
 * en la tabla 0 (prioridad propia, sobre la de R2 y la de ONOS) que manda la clase al controlador pasando por ese medidor. Asi una inundacion en un puerto no llega al controlador ni afecta a los demas puertos.
 *
 * Ciclo de vida (lo que un script no resuelve): el medidor se crea ANTES que la regla y se espera a que este ADDED en el switch; si el switch cae o vuelve se olvida todo lo de ese switch y se rehace desde cero
 * (ONOS no garantiza que un medidor siga en el switch tras reconectar); un lazo cada 3 s repone lo que falte; al desactivar la app se retiran reglas Y medidores (borrar un flujo no borra su medidor).
 * Todo corre en un unico hilo, asi que no hay carreras entre los eventos y el lazo.
 */
@Component(immediate = true)
public class CampusMetersApp {

    private static final String APP = "org.campus.meters";
    private static final String CLAVE_CONFIG = "campusmeters";
    private static final int CICLO_SEG = 3;
    private static final long ESPERA_TRAS_RETIRO_MS = 6_000;      // tras retirar medidores, ONOS tarda en darlos de baja: pedir otros con los mismos ids enseguida cruzo reglas y medidores (92 en modo real)
    private static final long MEDIDOR_ATASCADO_MS = 30_000;        // un medidor que no llega a ADDED en este tiempo se rehace
    private static final short ETHERTYPE_EAPOL = (short) 0x888e;      // 802.1X (PAE)

    private final Logger log = LoggerFactory.getLogger(getClass());

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected CoreService coreService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected MeterService meterService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected FlowRuleService flowRuleService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected DeviceService deviceService;

    @Reference(cardinality = ReferenceCardinality.MANDATORY)
    protected NetworkConfigRegistry configRegistry;

    private ApplicationId appId;
    private ScheduledExecutorService hilo;
    private volatile PlanMeters plan;
    private volatile long pausaHasta;                                 // no se piden medidores antes de este instante (milisegundos)
    /** Lo ya puesto: clave de la especificacion -> medidor y (cuando el medidor esta ADDED) su regla. Solo lo toca el hilo unico. */
    private final Map<String, Aplicado> aplicados = new HashMap<>();
    /** Medidores propios atascados en PENDING_ADD (ONOS no los ve nacer aunque el switch los tenga): se APARTAN, sin retirarlos, para que su id siga ocupado en ONOS y el siguiente medidor reciba otro id; ONOS
     *  reutiliza el id de uno retirado y el switch ya tiene un medidor con ese id (visto en sw-acc-1 tras reinstalar). Se retiran al rehacer el switch o desactivar la app. */
    private final Map<DeviceId, Set<MeterId>> cuarentena = new HashMap<>();

    private static final class Aplicado {
        final DeviceId device;
        MeterId meter;               // puede cambiar: si el medidor no corresponde a la clase se adopta otro (ver reconciliar)
        FlowRule regla;
        long desdeNoAdded;           // desde cuando se ve el medidor sin llegar a ADDED (0 = esta ADDED o aun no se vio)

        Aplicado(DeviceId device, MeterId meter) {
            this.device = device;
            this.meter = meter;
        }
    }

    private final ConfigFactory<ApplicationId, MetersConfig> fabricaConfig =
            new ConfigFactory<ApplicationId, MetersConfig>(SubjectFactories.APP_SUBJECT_FACTORY, MetersConfig.class, CLAVE_CONFIG) {
                @Override
                public MetersConfig createConfig() {
                    return new MetersConfig();
                }
            };

    private final NetworkConfigListener oyenteConfig = new NetworkConfigListener() {
        @Override
        public void event(NetworkConfigEvent evento) {
            if (evento.configClass() != null && evento.configClass().equals(MetersConfig.class)) {
                hilo.execute(CampusMetersApp.this::recargar);
            }
        }
    };

    /** Un switch que cae o vuelve: se olvida todo lo suyo (reglas y medidores) y el lazo lo rehace con el switch disponible. */
    private final DeviceListener oyenteDispositivos = new DeviceListener() {
        @Override
        public void event(DeviceEvent evento) {
            DeviceEvent.Type t = evento.type();
            if (t == DeviceEvent.Type.DEVICE_AVAILABILITY_CHANGED || t == DeviceEvent.Type.DEVICE_REMOVED) {
                DeviceId id = evento.subject().id();
                hilo.execute(() -> {
                    if (plan != null && plan.dispositivos().contains(id.toString())) {
                        log.info("Meters: {} cambio de disponibilidad ({}): se rehace todo lo de ese switch", id, deviceService.isAvailable(id) ? "disponible" : "caido");
                        olvidarDispositivo(id);
                        pausaHasta = System.currentTimeMillis() + ESPERA_TRAS_RETIRO_MS;
                    }
                });
            }
        }
    };

    @Activate
    protected void activate() {
        appId = coreService.registerApplication(APP);
        configRegistry.registerConfigFactory(fabricaConfig);
        configRegistry.addListener(oyenteConfig);
        deviceService.addListener(oyenteDispositivos);
        hilo = Executors.newSingleThreadScheduledExecutor();
        hilo.execute(this::recargar);
        hilo.scheduleWithFixedDelay(this::ciclo, CICLO_SEG, CICLO_SEG, TimeUnit.SECONDS);
        log.info("Meters por puerto iniciado (appId {})", appId.id());
    }

    @Deactivate
    protected void deactivate() {
        deviceService.removeListener(oyenteDispositivos);
        configRegistry.removeListener(oyenteConfig);
        hilo.shutdownNow();
        try {
            hilo.awaitTermination(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        retirarTodo();
        configRegistry.unregisterConfigFactory(fabricaConfig);
        log.info("Meters por puerto detenido: reglas y medidores retirados");
    }

    // ------------------------------------------------------------------ configuracion
    private void recargar() {
        try {
            MetersConfig cfg = configRegistry.getConfig(appId, MetersConfig.class);
            if (cfg == null || !cfg.isValid()) {
                retirarTodo();
                plan = null;
                log.warn("Meters: sin configuracion valida (clave '{}' en apps/{}); no se aplica nada", CLAVE_CONFIG, APP);
                return;
            }
            PlanMeters nuevo = cfg.plan();
            if (nuevo.equals(plan)) {                                        // la misma configuracion (por ejemplo, republicada por el playbook al reinstalar): no se toca nada
                log.info("Meters: la configuracion publicada es igual a la aplicada; no se rehace nada");
                return;
            }
            retirarTodo();
            plan = nuevo;
            pausaHasta = System.currentTimeMillis() + ESPERA_TRAS_RETIRO_MS;
            log.info("Meters: configuracion cargada: {} medidores en {} switches (tabla {}); se espera {} s a que terminen los retiros", plan.especificaciones().size(), plan.dispositivos().size(), plan.tabla(), ESPERA_TRAS_RETIRO_MS / 1000);
        } catch (RuntimeException e) {
            log.error("Meters: error al recargar la configuracion", e);
        }
    }

    // ------------------------------------------------------------------ lazo de reconciliacion
    private void ciclo() {
        PlanMeters p = plan;
        if (p == null || System.currentTimeMillis() < pausaHasta) {
            return;
        }
        try {
            for (String id : p.dispositivos()) {
                DeviceId device = DeviceId.deviceId(id);
                if (deviceService.getDevice(device) == null || !deviceService.isAvailable(device)) {
                    continue;
                }
                reconciliar(device, p);
            }
        } catch (RuntimeException e) {
            log.error("Meters: error en el ciclo de reconciliacion", e);
        }
    }

    private void reconciliar(DeviceId device, PlanMeters p) {
        Map<MeterId, Meter> propios = new HashMap<>();
        for (Meter m : meterService.getMeters(device)) {
            if (appId.equals(m.appId())) {
                propios.put(m.id(), m);
            }
        }
        for (Meter m : propios.values()) {                                  // un retiro en curso: no se piden medidores nuevos hasta que termine (los ids se reutilizan)
            if (m.state() == MeterState.PENDING_REMOVE) {
                log.info("Meters: {} tiene el medidor {} dandose de baja; se espera", device, m.id());
                return;
            }
        }
        for (Especificacion esp : p.de(device.toString())) {
            Aplicado a = aplicados.get(esp.clave());
            Meter existente = a == null ? null : propios.get(a.meter);
            if (a != null && existente != null && existente.state() != MeterState.ADDED) {
                long ahora = System.currentTimeMillis();
                if (a.desdeNoAdded == 0) {
                    a.desdeNoAdded = ahora;
                } else if (ahora - a.desdeNoAdded > MEDIDOR_ATASCADO_MS) {
                    log.warn("Meters: el medidor {} de {} lleva mas de {} s sin llegar a ADDED ({}); se aparta (su id queda ocupado) y se pide otro con otro id", a.meter, esp.clave(), MEDIDOR_ATASCADO_MS / 1000, existente.state());
                    aplicados.remove(esp.clave());
                    if (a.regla != null) {
                        flowRuleService.removeFlowRules(a.regla);
                    }
                    cuarentena.computeIfAbsent(device, k -> new HashSet<>()).add(a.meter);
                    pausaHasta = ahora + ESPERA_TRAS_RETIRO_MS;
                    return;
                }
            } else if (a != null) {
                a.desdeNoAdded = 0;
            }
            if (a != null && existente == null) {                          // el medidor desaparecio (p. ej. un switch reiniciado): se rehace
                log.warn("Meters: el medidor {} de {} ya no esta en {}; se rehace", a.meter, esp.clave(), device);
                quitar(esp.clave());
                a = null;
            }
            if (a != null && (!coincide(existente, esp) || usadoPorOtro(a.meter, esp.clave(), device))) {
                // El medidor de este puerto y clase no tiene su rate y rafaga, o lo comparte otra clase: ocurrio tras reiniciar Open vSwitch (92 en modo real), con la regla de EAPOL atada al medidor
                // de DHCP. Se busca entre los medidores propios sin dueño el que SI corresponde; si no hay, se rehace. La regla se retira ahora y se vuelve a poner en el ciclo siguiente.
                Meter alt = alternativo(propios, esp, esp.clave(), device);
                if (a.regla != null) {
                    flowRuleService.removeFlowRules(a.regla);
                    a.regla = null;
                }
                if (alt != null) {
                    log.warn("Meters: el medidor {} no corresponde a {}; se adopta el {}", a.meter, esp.clave(), alt.id());
                    a.meter = alt.id();
                } else {
                    log.warn("Meters: el medidor {} no corresponde a {} y no hay otro que sirva; se rehace", a.meter, esp.clave());
                    quitar(esp.clave());
                }
                continue;
            }
            if (a == null) {
                Meter adoptable = alternativo(propios, esp, esp.clave(), device);       // un medidor propio sin dueño que ya tiene los valores de esta clase: se aprovecha en vez de pedir otro
                if (adoptable != null) {
                    aplicados.put(esp.clave(), new Aplicado(device, adoptable.id()));
                    log.info("Meters: se adopta el medidor {} para {}", adoptable.id(), esp.clave());
                    continue;
                }
                Meter m = meterService.submit(construirMedidor(device, esp).add());
                if (propios.containsKey(m.id())) {                           // ONOS devolvio un id ya en uso: no se registra, el ciclo siguiente lo resuelve por los valores
                    log.warn("Meters: ONOS asigno el medidor {} a {}, que ya existe; se espera al ciclo siguiente", m.id(), esp.clave());
                    continue;
                }
                aplicados.put(esp.clave(), new Aplicado(device, m.id()));
                propios.put(m.id(), m);
                log.info("Meters: medidor {} pedido para {} ({} pps, rafaga {})", m.id(), esp.clave(), esp.rate, esp.rafaga);
                continue;                                                    // la regla va cuando el medidor este ADDED
            }
            if (a.regla == null && existente.state() == MeterState.ADDED) {
                a.regla = construirRegla(device, esp, a.meter, p.tabla());
                flowRuleService.applyFlowRules(a.regla);
                log.info("Meters: regla {} (prio {}) aplicada con el medidor {}", esp.clave(), esp.prioridad, a.meter);
            }
        }
        for (Meter m : propios.values()) {                                  // medidores nuestros que ya no corresponden a nada (los de la cuarentena se dejan: su id debe seguir ocupado)
            if (enCuarentena(device, m.id())) {
                continue;
            }
            boolean usado = false;
            for (Aplicado a : aplicados.values()) {
                if (a.meter.equals(m.id()) && a.device.equals(device)) {
                    usado = true;
                    break;
                }
            }
            if (!usado) {
                log.info("Meters: se retira el medidor huerfano {} de {}", m.id(), device);
                retirarMedidor(m);
            }
        }
    }

    // ------------------------------------------------------------------ correspondencia entre medidor y clase
    /** Un medidor corresponde a una especificacion si es de paquetes por segundo, tiene una sola banda DROP y su rate y rafaga son los de la clase. */
    private static boolean coincide(Meter m, Especificacion esp) {
        if (m.unit() != Meter.Unit.PKTS_PER_SEC || m.bands().size() != 1) {
            return false;
        }
        Band b = m.bands().iterator().next();
        return b.type() == Band.Type.DROP && b.rate() == esp.rate && b.burst() != null && b.burst() == esp.rafaga;
    }

    private boolean enCuarentena(DeviceId device, MeterId id) {
        Set<MeterId> c = cuarentena.get(device);
        return c != null && c.contains(id);
    }

    private boolean usadoPorOtro(MeterId id, String clave, DeviceId device) {
        for (Map.Entry<String, Aplicado> e : aplicados.entrySet()) {
            if (!e.getKey().equals(clave) && e.getValue().device.equals(device) && e.getValue().meter.equals(id)) {
                return true;
            }
        }
        return false;
    }

    /** Un medidor propio de ese switch, sin dueño entre lo aplicado, con los valores de la clase; null si no hay. */
    private Meter alternativo(Map<MeterId, Meter> propios, Especificacion esp, String clave, DeviceId device) {
        for (Meter m : propios.values()) {
            if (m.state() == MeterState.ADDED && !enCuarentena(device, m.id()) && coincide(m, esp) && !usadoPorOtro(m.id(), clave, device)) {
                return m;
            }
        }
        return null;
    }

    // ------------------------------------------------------------------ construccion
    private MeterRequest.Builder construirMedidor(DeviceId device, Especificacion esp) {
        Band banda = DefaultBand.builder().ofType(Band.Type.DROP).withRate(esp.rate).burstSize(esp.rafaga).build();
        return DefaultMeterRequest.builder().forDevice(device).fromApp(appId).withUnit(Meter.Unit.PKTS_PER_SEC).burst().withBands(Collections.singleton(banda));
    }

    private FlowRule construirRegla(DeviceId device, Especificacion esp, MeterId meter, int tabla) {
        TrafficSelector.Builder sel = DefaultTrafficSelector.builder().matchInPort(PortNumber.portNumber(esp.puerto));
        if (esp.clase == Clase.EAPOL) {
            sel.matchEthType(ETHERTYPE_EAPOL);
        } else {
            sel.matchEthType(Ethernet.TYPE_IPV4).matchIPProtocol(IPv4.PROTOCOL_UDP).matchUdpSrc(TpPort.tpPort(68)).matchUdpDst(TpPort.tpPort(67));
        }
        return DefaultFlowRule.builder()
                .forDevice(device)
                .fromApp(appId)
                .withSelector(sel.build())
                .withTreatment(DefaultTrafficTreatment.builder().meter(meter).setOutput(PortNumber.CONTROLLER).build())
                .withPriority(esp.prioridad)
                .forTable(tabla)
                .makePermanent()
                .build();
    }

    // ------------------------------------------------------------------ retiro
    /** Retira la regla y el medidor de UNA especificacion (primero la regla: un medidor con una regla que lo usa no debe desaparecer). */
    private void quitar(String clave) {
        Aplicado a = aplicados.remove(clave);
        if (a == null) {
            return;
        }
        if (a.regla != null) {
            flowRuleService.removeFlowRules(a.regla);
        }
        for (Aplicado otro : aplicados.values()) {                          // un medidor que otra clase sigue usando (el estado cruzado de tras un reinicio) no se retira
            if (otro.device.equals(a.device) && otro.meter.equals(a.meter)) {
                return;
            }
        }
        for (Meter m : meterService.getMeters(a.device)) {
            if (appId.equals(m.appId()) && m.id().equals(a.meter)) {
                retirarMedidor(m);
            }
        }
    }

    private void retirarMedidor(Meter m) {
        MeterRequest baja = DefaultMeterRequest.builder().forDevice(m.deviceId()).fromApp(appId).withUnit(m.unit()).burst().withBands(m.bands()).remove();
        meterService.withdraw(baja, m.id());
    }

    private void olvidarDispositivo(DeviceId device) {
        Set<MeterId> apartados = cuarentena.remove(device);
        if (apartados != null) {
            for (Meter m : meterService.getMeters(device)) {
                if (appId.equals(m.appId()) && apartados.contains(m.id())) {
                    retirarMedidor(m);
                }
            }
        }
        List<String> claves = new ArrayList<>();
        for (Map.Entry<String, Aplicado> e : aplicados.entrySet()) {
            if (e.getValue().device.equals(device)) {
                claves.add(e.getKey());
            }
        }
        for (String c : claves) {
            quitar(c);
        }
    }

    /** Quita todo lo que esta app puso, aunque no este en `aplicados` (por ejemplo tras reinstalarla). */
    private void retirarTodo() {
        cuarentena.clear();
        List<String> claves = new ArrayList<>(aplicados.keySet());
        for (String c : claves) {
            quitar(c);
        }
        flowRuleService.removeFlowRulesById(appId);
        Collection<Meter> todos = meterService.getAllMeters();
        for (Meter m : todos) {
            if (appId.equals(m.appId())) {
                retirarMedidor(m);
            }
        }
    }
}
