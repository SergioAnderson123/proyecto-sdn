package org.campus.dhcp;

import org.onlab.packet.MacAddress;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Sondeo de presencia de los equipos con concesion: decide CUANDO preguntar por ARP si un equipo sigue ahi y cuando darlo por ausente. Clase pura (sin servicios de ONOS): el tiempo
 * entra como parametro y devuelve ordenes (SONDEAR, AUSENTE) que ejecuta CampusDhcpApp.
 *
 * Un equipo que apago de golpe, o cuyo cable se solto por el lado del host, no avisa a nadie (y en la slice VNRT bajar la NIC de un host NO baja el puerto del switch). Solo queda
 * preguntar. Hay dos modos por equipo:
 *  - lento: cada `sondeoLentoSeg` empieza un ciclo (un equipo vivo contesta a la primera y el ciclo termina); 0 lo desactiva;
 *  - rapido: `intentos` sondeos separados `intervaloSeg`; sin respuesta tras el ultimo, AUSENTE. Lo activan `sospechar` (por ejemplo, cuando R1 borra su sesion 802.1X: si el equipo sigue vivo
 *    conserva su IP; si ya no esta, se libera) y el modo lento.
 * Con 3 intentos cada 5 s un equipo ausente se da por perdido a los 15 s.
 */
public final class Presencia {

    public enum Tipo { SONDEAR, AUSENTE }

    /** El equipo a vigilar: una concesion confirmada. */
    public static final class Objetivo {
        public final MacAddress mac;
        public final int ip;
        public final String puerto;      // "switch/puerto"
        public final int gateway;        // IP del gateway de su subred: es el emisor del ARP de sondeo

        public Objetivo(MacAddress mac, int ip, String puerto, int gateway) {
            this.mac = mac;
            this.ip = ip;
            this.puerto = puerto;
            this.gateway = gateway;
        }
    }

    public static final class Orden {
        public final Tipo tipo;
        public final Objetivo objetivo;

        Orden(Tipo tipo, Objetivo objetivo) {
            this.tipo = tipo;
            this.objetivo = objetivo;
        }
    }

    private static final class Estado {
        boolean rapido;
        boolean enVuelo;       // hay un sondeo enviado sin contestar
        int fallos;
        long proximo;
    }

    private final long intervaloMs;
    private final int intentos;
    private final long lentoMs;
    private final Map<MacAddress, Estado> estados = new HashMap<>();

    public Presencia(int intervaloSeg, int intentos, int sondeoLentoSeg) {
        if (intervaloSeg < 1 || intentos < 1 || sondeoLentoSeg < 0) {
            throw new IllegalArgumentException("presencia: intervalo >= 1, intentos >= 1 y sondeo lento >= 0");
        }
        this.intervaloMs = intervaloSeg * 1000L;
        this.intentos = intentos;
        this.lentoMs = sondeoLentoSeg * 1000L;
    }

    /** Hay motivos para pensar que el equipo ya no esta: empieza el ciclo rapido en el siguiente latido (si ya iba en uno, no se reinicia). */
    public synchronized void sospechar(MacAddress mac, long ahora) {
        Estado e = estados.computeIfAbsent(mac, m -> nuevo(ahora));
        if (!e.rapido) {
            empezarRapido(e, ahora);
        }
    }

    /** El equipo contesto el ARP: esta vivo; termina el ciclo y vuelve al modo lento. */
    public synchronized void respuesta(MacAddress mac, long ahora) {
        Estado e = estados.get(mac);
        if (e != null) {
            e.rapido = false;
            e.enVuelo = false;
            e.fallos = 0;
            e.proximo = proximoLento(ahora);
        }
    }

    /** Se llama cada segundo con las concesiones actuales: devuelve los sondeos a enviar y los equipos a dar por ausentes. Olvida a los que ya no tienen concesion. */
    public synchronized List<Orden> tick(long ahora, Collection<Objetivo> objetivos) {
        Set<MacAddress> vigentes = new HashSet<>();
        for (Objetivo o : objetivos) {
            vigentes.add(o.mac);
        }
        estados.keySet().retainAll(vigentes);
        List<Orden> ordenes = new ArrayList<>();
        for (Objetivo o : objetivos) {
            Estado e = estados.computeIfAbsent(o.mac, m -> nuevo(ahora));
            if (!e.rapido) {
                if (ahora < e.proximo) {
                    continue;
                }
                empezarRapido(e, ahora);                                     // le toca el sondeo lento: un ciclo que un equipo vivo cierra con su primera respuesta
            }
            if (ahora < e.proximo) {
                continue;
            }
            if (e.enVuelo) {                                                 // el sondeo anterior no tuvo respuesta
                e.fallos++;
                if (e.fallos >= intentos) {
                    estados.remove(o.mac);
                    ordenes.add(new Orden(Tipo.AUSENTE, o));
                    continue;
                }
            }
            e.enVuelo = true;
            e.proximo = ahora + intervaloMs;
            ordenes.add(new Orden(Tipo.SONDEAR, o));
        }
        return ordenes;
    }

    public synchronized boolean enCicloRapido(MacAddress mac) {
        Estado e = estados.get(mac);
        return e != null && e.rapido;
    }

    private Estado nuevo(long ahora) {
        Estado e = new Estado();
        e.proximo = proximoLento(ahora);
        return e;
    }

    private long proximoLento(long ahora) {
        return lentoMs > 0 ? ahora + lentoMs : Long.MAX_VALUE;
    }

    private static void empezarRapido(Estado e, long ahora) {
        e.rapido = true;
        e.enVuelo = false;
        e.fallos = 0;
        e.proximo = ahora;
    }
}
