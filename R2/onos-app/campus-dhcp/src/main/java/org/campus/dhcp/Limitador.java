package org.campus.dhcp;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

/** Limitador de tasa por clave (cubo de fichas): cada clave tiene una rafaga y se rellena a un ritmo constante. El tiempo se pasa como parametro (se prueba sin reloj real). */
public final class Limitador {

    private static final class Cubo {
        double fichas;
        long ultimo;
    }

    private final double porSegundo;
    private final double rafaga;
    private final Map<String, Cubo> cubos = new HashMap<>();

    public Limitador(double porSegundo, int rafaga) {
        this.porSegundo = porSegundo;
        this.rafaga = rafaga;
    }

    /** @return true si la solicitud de esa clave cabe en su limite ahora. */
    public synchronized boolean permitir(String clave, long ahoraMs) {
        Cubo c = cubos.get(clave);
        if (c == null) {
            limpiar(ahoraMs);
            c = new Cubo();
            c.fichas = rafaga;
            c.ultimo = ahoraMs;
            cubos.put(clave, c);
        }
        double transcurrido = Math.max(0, ahoraMs - c.ultimo) / 1000.0;
        c.fichas = Math.min(rafaga, c.fichas + transcurrido * porSegundo);
        c.ultimo = ahoraMs;
        if (c.fichas >= 1.0) {
            c.fichas -= 1.0;
            return true;
        }
        return false;
    }

    /** Quita los cubos inactivos (llenos y sin uso en 60 s) para que el mapa no crezca sin limite. */
    private void limpiar(long ahoraMs) {
        if (cubos.size() < 1000) {
            return;
        }
        Iterator<Map.Entry<String, Cubo>> it = cubos.entrySet().iterator();
        while (it.hasNext()) {
            if (ahoraMs - it.next().getValue().ultimo > 60_000) {
                it.remove();
            }
        }
    }
}
