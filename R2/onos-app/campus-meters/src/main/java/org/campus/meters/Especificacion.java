package org.campus.meters;

/** Lo que debe existir para UN puerto y UNA clase: un medidor (rate y rafaga) y una regla en la tabla 0 con esa prioridad. Clase pura (sin servicios de ONOS). */
public final class Especificacion {

    public final String device;
    public final long puerto;
    public final Clase clase;
    public final long rate;
    public final long rafaga;
    public final int prioridad;

    public Especificacion(String device, long puerto, Clase clase, long rate, long rafaga, int prioridad) {
        this.device = device;
        this.puerto = puerto;
        this.clase = clase;
        this.rate = rate;
        this.rafaga = rafaga;
        this.prioridad = prioridad;
    }

    /** Identifica la especificacion: dos iguales comparten medidor y regla. */
    public String clave() {
        return device + "/" + puerto + "/" + clase.nombre;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof Especificacion)) {
            return false;
        }
        Especificacion e = (Especificacion) o;
        return device.equals(e.device) && puerto == e.puerto && clase == e.clase && rate == e.rate && rafaga == e.rafaga && prioridad == e.prioridad;
    }

    @Override
    public int hashCode() {
        return java.util.Objects.hash(device, puerto, clase, rate, rafaga, prioridad);
    }

    @Override
    public String toString() {
        return clave() + " " + rate + " pps rafaga " + rafaga + " prio " + prioridad;
    }
}
