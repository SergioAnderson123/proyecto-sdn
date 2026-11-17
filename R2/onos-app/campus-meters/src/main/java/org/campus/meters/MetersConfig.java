package org.campus.meters;

import org.onosproject.core.ApplicationId;
import org.onosproject.net.config.Config;

/** Configuracion de la app (clave "campusmeters" en apps/org.campus.meters); el formato esta en PlanMeters. Se publica con POST /onos/v1/network/configuration/apps/org.campus.meters */
public class MetersConfig extends Config<ApplicationId> {

    @Override
    public boolean isValid() {
        try {
            plan();
            return true;
        } catch (RuntimeException e) {
            return false;
        }
    }

    public PlanMeters plan() {
        return PlanMeters.desde(object);
    }
}
