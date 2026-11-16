package org.campus.dhcp;

import com.fasterxml.jackson.databind.JsonNode;
import org.onlab.packet.MacAddress;
import org.onosproject.core.ApplicationId;
import org.onosproject.net.config.Config;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Configuracion de la app (clave "campusdhcp" en apps/org.campus.dhcp), generada del plan de subredes por R2/policy/generar_config_dhcp.py:
 * {"servidorMac": "02:00:5E:00:00:FD",
 *  "politica": {"maxPorPuerto": 3, "ofertaSeg": 15, "solicitudesPorSeg": 2.0, "rafaga": 8, "declinadaSeg": 300},
 *  "subredes": [{"nombre": "acc-1", "device": "of:0000e6925d87354b", "puertos": [1], "prefijo": "10.0.1.0/24", "gateway": "10.0.1.254",
 *                "inicio": "10.0.1.100", "fin": "10.0.1.199", "servidor": "10.0.1.250", "dns": ["8.8.8.8"], "leaseSeg": 3600}]}
 * Se publica con: POST /onos/v1/network/configuration/apps/org.campus.dhcp
 */
public class DhcpConfig extends Config<ApplicationId> {

    @Override
    public boolean isValid() {
        try {
            servidorMac();
            politica();
            return !subredes().isEmpty();
        } catch (RuntimeException e) {
            return false;
        }
    }

    public MacAddress servidorMac() {
        JsonNode n = object.get("servidorMac");
        if (n == null) {
            throw new IllegalArgumentException("falta servidorMac");
        }
        return MacAddress.valueOf(n.asText());
    }

    public Politica politica() {
        Politica d = Politica.porDefecto();
        JsonNode p = object.get("politica");
        if (p == null) {
            return d;
        }
        return new Politica(p.has("maxPorPuerto") ? p.get("maxPorPuerto").asInt() : d.maxPorPuerto,
                p.has("ofertaSeg") ? p.get("ofertaSeg").asInt() : d.ofertaSeg,
                p.has("solicitudesPorSeg") ? p.get("solicitudesPorSeg").asDouble() : d.solicitudesPorSeg,
                p.has("rafaga") ? p.get("rafaga").asInt() : d.rafaga,
                p.has("declinadaSeg") ? p.get("declinadaSeg").asInt() : d.declinadaSeg);
    }

    public List<Subred> subredes() {
        if (!object.has("subredes") || !object.get("subredes").isArray()) {
            throw new IllegalArgumentException("faltan las subredes");
        }
        MacAddress mac = servidorMac();
        List<Subred> salida = new ArrayList<>();
        for (JsonNode s : object.get("subredes")) {
            Set<Long> puertos = new HashSet<>();
            for (JsonNode p : s.get("puertos")) {
                puertos.add(p.asLong());
            }
            List<String> dns = new ArrayList<>();
            if (s.has("dns")) {
                for (JsonNode d : s.get("dns")) {
                    dns.add(d.asText());
                }
            }
            salida.add(new Subred(s.get("nombre").asText(), s.get("device").asText(), puertos, s.get("prefijo").asText(), s.get("gateway").asText(),
                    s.get("inicio").asText(), s.get("fin").asText(), s.get("servidor").asText(), mac, dns,
                    s.has("leaseSeg") ? s.get("leaseSeg").asInt() : 3600));
        }
        return salida;
    }
}
