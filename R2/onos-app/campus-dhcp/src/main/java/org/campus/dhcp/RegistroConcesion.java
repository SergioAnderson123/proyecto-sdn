package org.campus.dhcp;

import org.onlab.packet.Ip4Address;

/**
 * Una concesion confirmada tal como se guarda en el mapa del cluster (ConsistentMap "campus-dhcp-concesiones", clave = MAC del cliente):
 * texto simple "ip|subred|puerto|vencimiento", por ejemplo "10.0.1.100|acc-1|of:0000e6925d87354b/1|1763830000000" (el vencimiento es un instante en ms desde 1970).
 * Se usa texto y no una clase propia para que el mapa se pueda leer desde cualquier version de la app, sin problemas de clases entre bundles.
 * Clase pura (sin servicios de ONOS), probada sin ONOS.
 */
public final class RegistroConcesion {

    public final String subred;
    public final int ip;
    public final String puerto;
    public final long expira;

    public RegistroConcesion(String subred, int ip, String puerto, long expira) {
        this.subred = subred;
        this.ip = ip;
        this.puerto = puerto;
        this.expira = expira;
    }

    public static RegistroConcesion de(Servidor.Evento e) {
        return new RegistroConcesion(e.subred, e.ip.toInt(), e.puerto, e.expira);
    }

    public String codificar() {
        return Ip4Address.valueOf(ip) + "|" + subred + "|" + puerto + "|" + expira;
    }

    /** @return el registro, o null si el texto no tiene la forma esperada (un valor corrupto o de otra version se ignora, no rompe la app). */
    public static RegistroConcesion decodificar(String valor) {
        if (valor == null) {
            return null;
        }
        String[] p = valor.split("[|]", -1);
        if (p.length != 4 || p[1].isEmpty() || p[2].isEmpty()) {
            return null;
        }
        try {
            return new RegistroConcesion(p[1], Ip4Address.valueOf(p[0]).toInt(), p[2], Long.parseLong(p[3]));
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
