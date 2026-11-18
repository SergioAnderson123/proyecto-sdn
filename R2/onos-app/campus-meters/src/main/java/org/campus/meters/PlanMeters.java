package org.campus.meters;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Lo que la app debe tener puesto, calculado de la configuracion (generada del plan de subredes por R2/policy/generar_config_meters.py): por cada puerto de acceso de cada switch listado
 * y por cada clase activa, una Especificacion. Un switch nuevo es una entrada mas en la configuracion (una linea del plan). Clase pura: se prueba sin ONOS.
 *
 * Configuracion (clave "campusmeters" en apps/org.campus.meters):
 * <pre>
 * {"tabla": 0,
 *  "clases": {"dhcp": {"rate": 10, "burst": 10, "prioridad": 40510}, "eapol": {"rate": 20, "burst": 20, "prioridad": 50010}},
 *  "dispositivos": [{"nombre": "acc-2", "device": "of:0000766a0c84c741", "puertos": [1]}]}
 * </pre>
 * Sin "clases" se activan las dos con los valores del diseño de R1; con "clases" solo las listadas (permite empezar por DHCP). Todo valor ausente toma el del diseño.
 */
public final class PlanMeters {

    public static final int TABLA_POR_DEFECTO = 0;

    private final int tabla;
    private final List<Especificacion> especificaciones;

    private PlanMeters(int tabla, List<Especificacion> especificaciones) {
        this.tabla = tabla;
        this.especificaciones = Collections.unmodifiableList(especificaciones);
    }

    /** Dos planes son iguales si piden exactamente lo mismo (la app no rehace nada cuando se publica la misma configuracion). */
    @Override
    public boolean equals(Object o) {
        return o instanceof PlanMeters && tabla == ((PlanMeters) o).tabla && especificaciones.equals(((PlanMeters) o).especificaciones);
    }

    @Override
    public int hashCode() {
        return java.util.Objects.hash(tabla, especificaciones);
    }

    public int tabla() {
        return tabla;
    }

    public List<Especificacion> especificaciones() {
        return especificaciones;
    }

    /** Los switches con algo que aplicar. */
    public Set<String> dispositivos() {
        Set<String> ids = new HashSet<>();
        for (Especificacion e : especificaciones) {
            ids.add(e.device);
        }
        return ids;
    }

    public List<Especificacion> de(String device) {
        List<Especificacion> lista = new ArrayList<>();
        for (Especificacion e : especificaciones) {
            if (e.device.equals(device)) {
                lista.add(e);
            }
        }
        return lista;
    }

    /** @throws IllegalArgumentException si la configuracion no es valida (la app no aplica nada con una configuracion invalida) */
    public static PlanMeters desde(JsonNode raiz) {
        if (raiz == null || !raiz.has("dispositivos") || !raiz.get("dispositivos").isArray() || raiz.get("dispositivos").size() == 0) {
            throw new IllegalArgumentException("faltan los dispositivos");
        }
        int tabla = raiz.has("tabla") ? raiz.get("tabla").asInt(TABLA_POR_DEFECTO) : TABLA_POR_DEFECTO;
        if (tabla < 0 || tabla > 250) {
            throw new IllegalArgumentException("tabla fuera de rango: " + tabla);
        }
        Map<Clase, long[]> clases = new EnumMap<>(Clase.class);          // clase -> {rate, rafaga, prioridad}
        if (raiz.has("clases")) {
            JsonNode c = raiz.get("clases");
            java.util.Iterator<String> nombres = c.fieldNames();
            while (nombres.hasNext()) {
                String nombre = nombres.next();
                Clase clase = Clase.de(nombre);
                JsonNode p = c.get(nombre);
                clases.put(clase, new long[]{p.has("rate") ? p.get("rate").asLong() : clase.rateDefecto,
                        p.has("burst") ? p.get("burst").asLong() : clase.rafagaDefecto,
                        p.has("prioridad") ? p.get("prioridad").asLong() : clase.prioridadDefecto});
            }
            if (clases.isEmpty()) {
                throw new IllegalArgumentException("'clases' esta vacio");
            }
        } else {
            for (Clase clase : Clase.values()) {
                clases.put(clase, new long[]{clase.rateDefecto, clase.rafagaDefecto, clase.prioridadDefecto});
            }
        }
        for (Map.Entry<Clase, long[]> e : clases.entrySet()) {
            long[] v = e.getValue();
            if (v[0] < 1 || v[1] < 1) {
                throw new IllegalArgumentException(e.getKey().nombre + ": rate y burst deben ser al menos 1");
            }
            if (v[2] < 1 || v[2] > 65535) {
                throw new IllegalArgumentException(e.getKey().nombre + ": prioridad fuera de 1 a 65535");
            }
        }
        Map<String, Especificacion> unicas = new LinkedHashMap<>();
        for (JsonNode d : raiz.get("dispositivos")) {
            if (!d.has("device") || !d.has("puertos") || !d.get("puertos").isArray() || d.get("puertos").size() == 0) {
                throw new IllegalArgumentException("cada dispositivo necesita 'device' y 'puertos'");
            }
            String device = d.get("device").asText();
            for (JsonNode p : d.get("puertos")) {
                long puerto = p.asLong();
                if (puerto < 1) {
                    throw new IllegalArgumentException(device + ": puerto invalido " + p);
                }
                for (Map.Entry<Clase, long[]> e : clases.entrySet()) {
                    Especificacion esp = new Especificacion(device, puerto, e.getKey(), e.getValue()[0], e.getValue()[1], (int) e.getValue()[2]);
                    unicas.put(esp.clave(), esp);
                }
            }
        }
        return new PlanMeters(tabla, new ArrayList<>(unicas.values()));
    }
}
