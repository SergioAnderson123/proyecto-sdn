package org.campus.dhcp;

import org.onlab.packet.MacAddress;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Las direcciones de UN ambito: ofertas pendientes y concesiones confirmadas, con los limites contra el agotamiento:
 * una sola concesion por MAC, una cuota de concesiones y ofertas por puerto de acceso, ofertas que vencen pronto, y las IP declinadas apartadas un tiempo.
 * Es una clase pura: el tiempo entra como parametro (milisegundos) para probarla sin reloj. Todas las operaciones estan sincronizadas.
 */
public final class Pool {

    public enum Rechazo { CUOTA_PUERTO, AGOTADO }

    /** Resultado de pedir una oferta: una IP, o el motivo de no darla. */
    public static final class Oferta {
        public final Integer ip;
        public final Rechazo rechazo;

        Oferta(Integer ip, Rechazo rechazo) {
            this.ip = ip;
            this.rechazo = rechazo;
        }
    }

    /** Una concesion confirmada. */
    public static final class Concesion {
        public final MacAddress mac;
        public final int ip;
        public final String puerto;
        public final long expira;

        Concesion(MacAddress mac, int ip, String puerto, long expira) {
            this.mac = mac;
            this.ip = ip;
            this.puerto = puerto;
            this.expira = expira;
        }
    }

    private static final class Asig {
        final MacAddress mac;
        final String puerto;
        long expira;
        boolean confirmada;

        Asig(MacAddress mac, String puerto, long expira) {
            this.mac = mac;
            this.puerto = puerto;
            this.expira = expira;
        }
    }

    private final Subred subred;
    private final Politica politica;
    private final Map<Integer, Asig> porIp = new HashMap<>();
    private final Map<MacAddress, Integer> porMac = new HashMap<>();
    private final Map<MacAddress, Integer> ultima = new HashMap<>();
    private final Map<Integer, Long> declinadas = new HashMap<>();

    public Pool(Subred subred, Politica politica) {
        this.subred = subred;
        this.politica = politica;
    }

    /** Reserva (sin confirmar) una IP para esa MAC y devuelve el motivo si no puede. `pedida` es la IP que el cliente solicito o 0. */
    public synchronized Oferta ofrecer(MacAddress mac, String puerto, int pedida, long ahora) {
        purgar(ahora);
        Integer ya = porMac.get(mac);
        if (ya != null) {                                                    // una sola concesion u oferta por MAC: se repite la misma
            Asig a = porIp.get(ya);
            if (!a.confirmada) {
                a.expira = ahora + politica.ofertaSeg * 1000L;
            }
            return new Oferta(ya, null);
        }
        if (usoDelPuerto(puerto) >= politica.maxPorPuerto) {
            return new Oferta(null, Rechazo.CUOTA_PUERTO);
        }
        Integer elegida = null;
        if (pedida != 0 && libre(pedida, ahora)) {
            elegida = pedida;
        } else if (ultima.containsKey(mac) && libre(ultima.get(mac), ahora)) {
            elegida = ultima.get(mac);                                       // la misma IP de la vez anterior, si sigue libre
        } else {
            for (int ip = subred.inicio; Integer.compareUnsigned(ip, subred.fin) <= 0; ip++) {
                if (libre(ip, ahora)) {
                    elegida = ip;
                    break;
                }
            }
        }
        if (elegida == null) {
            return new Oferta(null, Rechazo.AGOTADO);
        }
        asignar(mac, puerto, elegida, ahora + politica.ofertaSeg * 1000L, false);
        return new Oferta(elegida, null);
    }

    /**
     * El cliente pide confirmar `ip` (REQUEST). Se acepta si es la IP que se le ofrecio o ya tenia (renovacion), o, si no tiene nada con nosotros (arranque con una IP
     * anterior), si esa IP esta libre y su puerto tiene cuota. Devuelve la concesion, o null si hay que contestar NAK.
     */
    public synchronized Concesion confirmar(MacAddress mac, String puerto, int ip, long ahora) {
        purgar(ahora);
        Integer ya = porMac.get(mac);
        if (ya != null) {
            if (ya != ip) {
                return null;
            }
            Asig a = porIp.get(ip);
            a.confirmada = true;
            a.expira = ahora + subred.leaseSeg * 1000L;
            return new Concesion(mac, ip, a.puerto, a.expira);
        }
        if (usoDelPuerto(puerto) >= politica.maxPorPuerto || !libre(ip, ahora)) {
            return null;
        }
        Asig a = asignar(mac, puerto, ip, ahora + subred.leaseSeg * 1000L, true);
        return new Concesion(mac, ip, a.puerto, a.expira);
    }

    /** RELEASE: el cliente devuelve su IP. @return la concesion que tenia, o null. */
    public synchronized Concesion liberar(MacAddress mac) {
        Integer ip = porMac.remove(mac);
        if (ip == null) {
            return null;
        }
        Asig a = porIp.remove(ip);
        return a.confirmada ? new Concesion(mac, ip, a.puerto, a.expira) : null;
    }

    /**
     * El puerto dejo de tener cable (o el switch cayo): se liberan al instante todas las ofertas y concesiones de ese puerto, sin esperar a que venzan. `prefijoPuerto` es "switch/puerto" o,
     * para liberar todo un switch, "switch/". Devuelve las concesiones confirmadas que se liberaron (para avisar a ONOS). Un equipo que vuelva a conectarse y pida su IP anterior la recupera si sigue libre.
     */
    public synchronized List<Concesion> liberarPuerto(String prefijoPuerto) {
        List<Concesion> liberadas = new ArrayList<>();
        Iterator<Map.Entry<Integer, Asig>> it = porIp.entrySet().iterator();
        while (it.hasNext()) {
            Map.Entry<Integer, Asig> e = it.next();
            Asig a = e.getValue();
            boolean coincide = prefijoPuerto.endsWith("/") ? a.puerto.startsWith(prefijoPuerto) : a.puerto.equals(prefijoPuerto);
            if (coincide) {
                it.remove();
                porMac.remove(a.mac);
                if (a.confirmada) {
                    liberadas.add(new Concesion(a.mac, e.getKey(), a.puerto, a.expira));
                }
            }
        }
        return liberadas;
    }

    /** DECLINE: el cliente detecto que otro equipo ya usa esa IP; se aparta un tiempo. */
    public synchronized void declinar(MacAddress mac, int ip, long ahora) {
        Integer propia = porMac.get(mac);
        if (propia != null && propia == ip) {
            porMac.remove(mac);
            porIp.remove(ip);
        }
        if (subred.contiene(ip)) {
            declinadas.put(ip, ahora + politica.declinadaSeg * 1000L);
        }
    }

    /** Quita lo vencido y devuelve las concesiones confirmadas que vencieron (para avisar a ONOS). Las ofertas vencidas se descartan sin aviso. */
    public synchronized List<Concesion> vencer(long ahora) {
        List<Concesion> vencidas = new ArrayList<>();
        Iterator<Map.Entry<Integer, Asig>> it = porIp.entrySet().iterator();
        while (it.hasNext()) {
            Map.Entry<Integer, Asig> e = it.next();
            Asig a = e.getValue();
            if (a.expira <= ahora) {
                it.remove();
                porMac.remove(a.mac);
                if (a.confirmada) {
                    vencidas.add(new Concesion(a.mac, e.getKey(), a.puerto, a.expira));
                }
            }
        }
        declinadas.values().removeIf(hasta -> hasta <= ahora);
        return vencidas;
    }

    public synchronized Concesion de(MacAddress mac) {
        Integer ip = porMac.get(mac);
        Asig a = ip == null ? null : porIp.get(ip);
        return a != null && a.confirmada ? new Concesion(mac, ip, a.puerto, a.expira) : null;
    }

    /** Concesiones confirmadas y ofertas pendientes (para ver la ocupacion). */
    public synchronized int ocupadas() {
        return porIp.size();
    }

    public synchronized int confirmadas() {
        int n = 0;
        for (Asig a : porIp.values()) {
            if (a.confirmada) {
                n++;
            }
        }
        return n;
    }

    // ---- internos ----
    private void purgar(long ahora) {
        vencer(ahora);
    }

    private boolean libre(int ip, long ahora) {
        if (Integer.compareUnsigned(ip, subred.inicio) < 0 || Integer.compareUnsigned(ip, subred.fin) > 0) {
            return false;
        }
        Long hasta = declinadas.get(ip);
        return !porIp.containsKey(ip) && (hasta == null || hasta <= ahora);
    }

    private int usoDelPuerto(String puerto) {
        int n = 0;
        for (Asig a : porIp.values()) {
            if (a.puerto.equals(puerto)) {
                n++;
            }
        }
        return n;
    }

    private Asig asignar(MacAddress mac, String puerto, int ip, long expira, boolean confirmada) {
        Asig a = new Asig(mac, puerto, expira);
        a.confirmada = confirmada;
        porIp.put(ip, a);
        porMac.put(mac, ip);
        ultima.put(mac, ip);
        if (ultima.size() > 10_000) {                                        // la memoria de "la IP de la vez anterior" no crece sin limite
            ultima.keySet().removeIf(m -> !porMac.containsKey(m));
        }
        return a;
    }
}
