package org.campus.gatewayarp;

import com.fasterxml.jackson.databind.JsonNode;
import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;
import org.onosproject.core.ApplicationId;
import org.onosproject.net.DeviceId;
import org.onosproject.net.PortNumber;
import org.onosproject.net.config.Config;

import java.util.ArrayList;
import java.util.List;

/**
 * Configuración de la app (clave "gatewayarp" en la configuración de red de ONOS):
 * <pre>
 * {"gwMac": "02:00:5E:00:00:FE", "tabla": 60, "prioridad": 300, "copiarArp": false,
 *  "gateways": [{"device": "of:0000e6925d87354b", "ip": "10.0.1.254", "ports": [1]}]}
 * </pre>
 * Se publica con: POST /onos/v1/network/configuration/apps/org.campus.gatewayarp
 */
public class GatewayConfig extends Config<ApplicationId> {

    /** Un gateway virtual: en `device`, las peticiones ARP por los `ports` de acceso para `ip` se contestan con la MAC común. */
    public static final class Gateway {
        public final DeviceId device;
        public final Ip4Address ip;
        public final List<PortNumber> ports;

        Gateway(DeviceId device, Ip4Address ip, List<PortNumber> ports) {
            this.device = device;
            this.ip = ip;
            this.ports = ports;
        }
    }

    @Override
    public boolean isValid() {
        try {
            gwMac();
            if (!object.has("gateways") || !object.get("gateways").isArray() || object.get("gateways").size() == 0) {
                return false;
            }
            for (Gateway g : gateways()) {
                if (g.ports.isEmpty()) {
                    return false;
                }
            }
            return true;
        } catch (RuntimeException e) {
            return false;
        }
    }

    public MacAddress gwMac() {
        JsonNode n = object.get("gwMac");
        if (n == null) {
            throw new IllegalArgumentException("falta gwMac");
        }
        return MacAddress.valueOf(n.asText());
    }

    public int tabla() {
        return object.has("tabla") ? object.get("tabla").asInt(60) : 60;
    }

    public int prioridad() {
        return object.has("prioridad") ? object.get("prioridad").asInt(300) : 300;
    }

    /** Si es true, la petición ARP original también se copia al controlador (para que ONOS aprenda al host). */
    public boolean copiarArp() {
        return object.has("copiarArp") && object.get("copiarArp").asBoolean(false);
    }

    public List<Gateway> gateways() {
        List<Gateway> salida = new ArrayList<>();
        for (JsonNode g : object.get("gateways")) {
            List<PortNumber> puertos = new ArrayList<>();
            for (JsonNode p : g.get("ports")) {
                puertos.add(PortNumber.portNumber(p.asLong()));
            }
            salida.add(new Gateway(DeviceId.deviceId(g.get("device").asText()), Ip4Address.valueOf(g.get("ip").asText()), puertos));
        }
        return salida;
    }
}
