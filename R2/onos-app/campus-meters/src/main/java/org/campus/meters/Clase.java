package org.campus.meters;

/**
 * Las clases de trafico de control que se miden por puerto de acceso (diseño de R1, R1/flujos_R1_R2.md): cada una tiene su medidor y su regla aditiva.
 *  - EAPOL (0x888E): la autenticacion 802.1X; 20 paquetes por segundo, rafaga 20 (el handshake EAP-TLS cabe: ~20 tramas seguidas).
 *  - DHCP del cliente (UDP 68 a 67): 10 paquetes por segundo, rafaga 10 (MAB y DHCP comparten limite en el diseño de R1).
 * Las prioridades se registran en R5/deploy/prioridades.yaml: la de DHCP queda sobre la regla general de R2 (40500) y bajo el DROP del DHCP externo (40600) y el espejo de R5 (41000);
 * la de EAPOL queda sobre la de sesion (50000).
 */
public enum Clase {
    EAPOL("eapol", 20, 20, 50010),
    DHCP("dhcp", 10, 10, 40510);

    public final String nombre;
    public final long rateDefecto;
    public final long rafagaDefecto;
    public final int prioridadDefecto;

    Clase(String nombre, long rate, long rafaga, int prioridad) {
        this.nombre = nombre;
        this.rateDefecto = rate;
        this.rafagaDefecto = rafaga;
        this.prioridadDefecto = prioridad;
    }

    public static Clase de(String nombre) {
        for (Clase c : values()) {
            if (c.nombre.equalsIgnoreCase(nombre)) {
                return c;
            }
        }
        throw new IllegalArgumentException("clase de trafico desconocida: " + nombre);
    }
}
