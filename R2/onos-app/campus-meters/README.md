# App de ONOS "Meters por puerto" (org.campus.meters)

Crea, con el servicio de meters de ONOS, un medidor por puerto de acceso y clase (EAPOL 20 pps / ráfaga 20; DHCP 10 pps / ráfaga 10; banda DROP) y una regla aditiva en la tabla 0 que manda esa clase al controlador pasando por el medidor. Reemplaza al script con archivo de estado: la app lleva el ciclo de vida (medidor antes que regla, retiro de ambos, reposición al reconectar un switch).
Diseño, riesgos y uso: `docs/DISENO_METERS_POR_PUERTO.md`.

## Código
- `Clase`, `Especificacion`, `PlanMeters`: la lógica **sin servicios de ONOS** (de la configuración a la lista de medidores y reglas). 7 pruebas JUnit.
- `MetersConfig` y `CampusMetersApp`: la conexión con ONOS (`MeterService`, `FlowRuleService`, `DeviceService`, configuración). Un solo hilo; lazo de reconciliación cada 3 s.

## Configuración
La genera `R2/policy/generar_config_meters.py` del plan de subredes (los puertos de acceso de las subredes `acc*`); se publica con `POST /onos/v1/network/configuration/apps/org.campus.meters` (el playbook 90 lo hace).

## Compilar y probar (en tu PC, con Docker)
```bash
docker run --rm -v "$(pwd):/app" -v onos-m2:/root/.m2 -w /app maven:3.8.6-eclipse-temurin-11 mvn -B package     # pasa las 7 pruebas y genera target/campus-meters-1.0.0.oar (copiarlo a dist/)
```

## Instalar y probar en la slice
`PLAYBOOK=90_meters_app.yml ./lanzar.sh -e accion=instalar -e confirmar=true` y `PLAYBOOK=91_prueba_meters_app.yml ./lanzar.sh`. Retroceso: `-e accion=quitar`.
