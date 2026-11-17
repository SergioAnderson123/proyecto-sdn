# Meters por puerto de acceso (app de ONOS `campus-meters`)

**Estado (17-nov-2026):** la app compila y pasa 7 pruebas Java; el generador de configuración 6 pruebas Python; playbooks `90_meters_app.yml` (instalar, quitar, estado) y `91_prueba_meters_app.yml` (el medidor descarta). **Falta probarlo en vivo.**

## Para qué
Una inundación de EAPOL o de DHCP en un puerto de acceso llega hoy entera al controlador (los flujos de R1 y R2 mandan esos paquetes al controlador sin límite). Los medidores de R1 (`R1/flujos_R1_R2.md`) limitan eso **en el switch**, por puerto: lo que pasa del límite se descarta sin molestar al controlador ni a los demás puertos.

| Clase | Límite (diseño de R1) | Regla aditiva (tabla 0) | Prioridad |
|---|---|---|---|
| EAPOL (0x888E) | 20 pps, ráfaga 20 | `in_port=P, eth_type=0x888e` → medidor, `CONTROLLER` | 50010 (sobre la de sesión de R1, 50000) |
| DHCP del cliente (UDP 68 a 67) | 10 pps, ráfaga 10 | `in_port=P, udp 68→67` → medidor, `CONTROLLER` | 40510 (sobre el DHCP de R2, 40500; bajo el DROP externo 40600 y el espejo de R5 41000) |

Es **aditivo y reversible**: no toca el Policy Mapper ni las reglas existentes (las originales siguen debajo como respaldo). Las prioridades están en `R5/deploy/prioridades.yaml` (`meter_dhcp_puerto`, `meter_eapol_puerto`).

## Por qué una app y no un playbook
En el REST de ONOS crear un medidor y una regla que lo usa funciona (84), pero **borrar un flujo no borra su medidor**, hay que crear el medidor antes que la regla y quitarla antes que el medidor, y ONOS no garantiza que un medidor siga en el switch tras reconectarlo. Eso es ciclo de vida, y lo lleva una app:
- **Lee los puertos de acceso del mismo plan** que el vigilante, el Gateway ARP y la app DHCP (`generar_config_meters.py`): un switch nuevo del plan queda cubierto con una línea.
- **Crea el medidor y espera a que esté ADDED en el switch antes de poner la regla** (lazo de 3 s).
- **Si un switch cae o vuelve, olvida todo lo suyo y lo rehace desde cero**; si un medidor desaparece del switch, lo recrea.
- **Al desactivarla retira reglas Y medidores** (también los huérfanos de una activación anterior).

## Escala
Lineal en puertos de acceso, no en usuarios: 2 medidores y 2 reglas por puerto (≈ 100 por switch de 48 puertos; ≈ 3 000 en 31 switches). `meter-features` de sw-acc-2 (OVS 3.3.9): `max_meter:200000`, así que la capacidad no limita.

## Cómo se usa
```
PLAYBOOK=90_meters_app.yml ./lanzar.sh                                            # solo lee
PLAYBOOK=90_meters_app.yml ./lanzar.sh -e accion=instalar -e confirmar=true       # empieza por DHCP en sw-acc-2
PLAYBOOK=91_prueba_meters_app.yml ./lanzar.sh                                     # inyecta 200 tramas DHCP: el medidor debe descartar la mayoría
PLAYBOOK=83_prueba_dhcp.yml ./lanzar.sh                                           # el DHCP normal sigue funcionando
PLAYBOOK=90_meters_app.yml ./lanzar.sh -e accion=instalar -e confirmar=true -e solo=acc-1,acc-2 -e clases=dhcp,eapol    # todos y las dos clases
PLAYBOOK=72_suplicante.yml ./lanzar.sh -e arrancar=true                           # con EAPOL: EAP-TLS debe seguir autenticando dentro de 20 pps
PLAYBOOK=90_meters_app.yml ./lanzar.sh -e accion=quitar -e confirmar=true         # retroceso
```

## Riesgos que la prueba debe cerrar
- Que EAP-TLS (≈ 10 a 20 tramas seguidas) quepa en 20 pps con ráfaga 20: si no, subir la ráfaga con `--eapol 20/40` en `generar_config_meters.py` (un parámetro).
- Que ONOS cree el medidor en el switch con el driver actual (en el REST ya funcionó) y que la regla con `meter + CONTROLLER` sea aceptada por OVS (en el 84 se probó solo con el medidor).
- Que tras reconectar un switch la app rehaga todo (parar la NIC del switch un momento y mirar el log `Meters`).

## Fuera de esta versión
El DROP de respuestas DHCP desde puertos de acceso (servidor falso, UDP 67→68) va aparte (prio 40600). El medidor de MAB (fallback) se añade como una clase más cuando el fallback se despliegue.
