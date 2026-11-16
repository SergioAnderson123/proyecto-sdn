package org.campus.dhcp;

import org.onlab.packet.Ip4Address;
import org.onlab.packet.MacAddress;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;

/**
 * Un ambito DHCP: la subred de un switch de acceso. Se identifica por (switch, puerto de acceso): el DHCP que entra por esos puertos recibe direcciones de este ambito.
 * Es una clase pura (sin servicios de ONOS) para poder probarla sin ONOS. Las direcciones se guardan como enteros de 32 bits, como en el paquete DHCP.
 */
public final class Subred {

    public final String nombre;
    public final String device;
    public final Set<Long> puertos;
    public final int red;
    public final int mascara;
    public final int gateway;
    public final int inicio;
    public final int fin;
    public final int servidorIp;
    public final MacAddress servidorMac;
    public final List<Integer> dns;
    public final int leaseSeg;

    /** @param prefijo por ejemplo "10.0.1.0/24"; inicio y fin delimitan el ambito dinamico (inclusive), fuera quedan las IP fijas. */
    public Subred(String nombre, String device, Set<Long> puertos, String prefijo, String gateway, String inicio, String fin,
                  String servidorIp, MacAddress servidorMac, List<String> dns, int leaseSeg) {
        String[] partes = prefijo.split("/");
        if (partes.length != 2) {
            throw new IllegalArgumentException(nombre + ": prefijo invalido " + prefijo);
        }
        int longitud = Integer.parseInt(partes[1]);
        if (longitud < 8 || longitud > 30) {
            throw new IllegalArgumentException(nombre + ": longitud de prefijo fuera de 8 a 30");
        }
        this.mascara = longitud == 0 ? 0 : (int) (0xFFFFFFFFL << (32 - longitud));
        this.red = Ip4Address.valueOf(partes[0]).toInt();
        if ((this.red & this.mascara) != this.red) {
            throw new IllegalArgumentException(nombre + ": el prefijo tiene bits de host " + prefijo);
        }
        this.nombre = nombre;
        this.device = device;
        this.puertos = Collections.unmodifiableSet(puertos);
        if (puertos.isEmpty()) {
            throw new IllegalArgumentException(nombre + ": sin puertos");
        }
        this.gateway = Ip4Address.valueOf(gateway).toInt();
        this.inicio = Ip4Address.valueOf(inicio).toInt();
        this.fin = Ip4Address.valueOf(fin).toInt();
        this.servidorIp = Ip4Address.valueOf(servidorIp).toInt();
        this.servidorMac = servidorMac;
        List<Integer> d = new ArrayList<>();
        for (String s : dns) {
            d.add(Ip4Address.valueOf(s).toInt());
        }
        this.dns = Collections.unmodifiableList(d);
        this.leaseSeg = leaseSeg;
        validar();
    }

    private void validar() {
        if (Integer.compareUnsigned(inicio, fin) > 0) {
            throw new IllegalArgumentException(nombre + ": el inicio del ambito es mayor que el fin");
        }
        for (int ip : new int[]{inicio, fin, gateway, servidorIp}) {
            if (!contiene(ip)) {
                throw new IllegalArgumentException(nombre + ": " + Ip4Address.valueOf(ip) + " no esta en la subred");
            }
        }
        if (inicio == red || fin == broadcast()) {
            throw new IllegalArgumentException(nombre + ": el ambito incluye la direccion de red o la de difusion");
        }
        if (Integer.compareUnsigned(gateway, inicio) >= 0 && Integer.compareUnsigned(gateway, fin) <= 0) {
            throw new IllegalArgumentException(nombre + ": el gateway cae dentro del ambito dinamico");
        }
        if (Integer.compareUnsigned(servidorIp, inicio) >= 0 && Integer.compareUnsigned(servidorIp, fin) <= 0) {
            throw new IllegalArgumentException(nombre + ": la IP del servidor DHCP cae dentro del ambito dinamico");
        }
        if (leaseSeg < 30) {
            throw new IllegalArgumentException(nombre + ": la concesion debe durar al menos 30 s");
        }
    }

    public boolean contiene(int ip) {
        return (ip & mascara) == red;
    }

    public int broadcast() {
        return red | ~mascara;
    }

    public boolean atiende(String dispositivo, long puerto) {
        return device.equals(dispositivo) && puertos.contains(puerto);
    }

    /** Capacidad del ambito dinamico. */
    public int capacidad() {
        return fin - inicio + 1;
    }
}
