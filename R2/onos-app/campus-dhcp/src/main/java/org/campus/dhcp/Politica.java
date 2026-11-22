package org.campus.dhcp;

/**
 * Limites contra el agotamiento del ambito (DHCP starvation) y el abuso del servidor. Todos los valores son por puerto de acceso (un puerto = un equipo cableado).
 */
public final class Politica {

    /** Concesiones y ofertas simultaneas que puede tener un mismo puerto de acceso. Un atacante con MAC falsas no pasa de este numero por puerto. */
    public final int maxPorPuerto;
    /** Segundos que se retiene una IP ofrecida mientras el cliente no la confirme (REQUEST): corta, para que ofertas sin confirmar no bloqueen el ambito. */
    public final int ofertaSeg;
    /** Solicitudes (DISCOVER y REQUEST) por segundo que se atienden por puerto, en regimen. */
    public final double solicitudesPorSeg;
    /** Rafaga permitida por puerto antes de aplicar el limite de tasa. */
    public final int rafaga;
    /** Segundos que una IP rechazada por el cliente (DECLINE: ya la usa otro) queda apartada. */
    public final int declinadaSeg;
    /** Milisegundos que se espera una respuesta ARP antes de ofrecer una IP nueva (sondeo previo, opcion B del diseno): si alguien contesta, esa IP ya esta en uso (un equipo con IP fija dentro del ambito dinamico). 0 = sin sondeo previo. */
    public final int sondeoPrevioMs;
    /** Cuantas IP candidatas se prueban como maximo para un mismo cliente antes de desistir (cada una ocupada se aparta). */
    public final int sondeoIntentos;

    public Politica(int maxPorPuerto, int ofertaSeg, double solicitudesPorSeg, int rafaga, int declinadaSeg) {
        this(maxPorPuerto, ofertaSeg, solicitudesPorSeg, rafaga, declinadaSeg, 0, 5);
    }

    public Politica(int maxPorPuerto, int ofertaSeg, double solicitudesPorSeg, int rafaga, int declinadaSeg, int sondeoPrevioMs, int sondeoIntentos) {
        if (maxPorPuerto < 1 || ofertaSeg < 1 || solicitudesPorSeg <= 0 || rafaga < 1 || declinadaSeg < 1 || sondeoPrevioMs < 0 || sondeoIntentos < 1) {
            throw new IllegalArgumentException("politica DHCP invalida");
        }
        this.sondeoPrevioMs = sondeoPrevioMs;
        this.sondeoIntentos = sondeoIntentos;
        this.maxPorPuerto = maxPorPuerto;
        this.ofertaSeg = ofertaSeg;
        this.solicitudesPorSeg = solicitudesPorSeg;
        this.rafaga = rafaga;
        this.declinadaSeg = declinadaSeg;
    }

    /** La misma politica sin sondeo previo (cuando no hay MAC del gateway con la que sondear). */
    public Politica sinSondeoPrevio() {
        return new Politica(maxPorPuerto, ofertaSeg, solicitudesPorSeg, rafaga, declinadaSeg, 0, sondeoIntentos);
    }

    /** Valores por defecto: 3 equipos por puerto, oferta retenida 15 s, 2 solicitudes por segundo con rafaga de 8, IP declinada apartada 5 min, sin sondeo previo (lo activa la configuracion). */
    public static Politica porDefecto() {
        return new Politica(3, 15, 2.0, 8, 300);
    }
}
