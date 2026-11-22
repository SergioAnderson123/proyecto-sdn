# App de ONOS "DHCP por subred" (org.campus.dhcp)

Servidor DHCP con un ámbito por subred (según el switch y el puerto de acceso por el que llega la petición), con defensas contra el agotamiento de direcciones, que avisa a ONOS de la IP de cada host para que el vigilante de la capa 3 la conozca.
Diseño y razones: `docs/DISENO_DHCP_POR_SUBRED.md`. Reemplaza a la app DHCP estándar (`org.onosproject.dhcp`), que debe quedar desactivada.

## Código
- `Servidor`, `Pool`, `Subred`, `Politica`, `Limitador`: la lógica, **sin servicios de ONOS** (se prueba sin ONOS). 74 pruebas JUnit en `src/test` (17 son del sondeo ARP previo a ofrecer una IP).
- `Presencia`: cuándo sondear por ARP a un equipo con concesión y cuándo darlo por ausente (pura, con el tiempo como parámetro). Ver `docs/DISENO_PRESENCIA_EQUIPOS.md`.
- `DhcpConfig` y `CampusDhcpApp`: la conexión con ONOS (paquetes del controlador, `HostProvider` para registrar la IP, configuración, vencimiento de concesiones, sondeo ARP de presencia y aviso al Policy Mapper).

## Defensas (por puerto de acceso)
Sondeo ARP previo a ofrecer una IP nueva (800 ms; si otro equipo la tiene, se aparta y se ofrece otra; `docs/DISENO_DHCP_POR_SUBRED.md` §2 quinquies), cuota de concesiones y ofertas por puerto (3), una concesión por MAC, ofertas que vencen a los 15 s, límite de tasa (2/s, ráfaga 8), la MAC del cliente debe ser la de origen de la trama, IP declinada apartada 5 min, ámbito por subred.
El medidor del switch (meters de R1) y el DROP de servidores DHCP falsos se aplican aparte (`docs/DISENO_DHCP_POR_SUBRED.md` §6).

## Compilar y probar (en tu PC, con Docker)
```bash
docker run --rm -v "$(pwd):/app" -v onos-m2:/root/.m2 -w /app maven:3.8.6-eclipse-temurin-11 mvn -B package     # pasa las 74 pruebas y genera target/campus-dhcp-1.0.0.oar (copiarlo a dist/)
```

## Instalar y probar en la slice
`PLAYBOOK=82_dhcp_app.yml ./lanzar.sh -e confirmar=true` (instala, desactiva el DHCP estándar, publica la configuración que genera `R2/policy/generar_config_dhcp.py` y las entradas ARP del servidor DHCP de cada subred; retroceso con `-e accion=quitar`).
Prueba con interfaces macvlan en h1, sin tocar su IP real (cliente propio `files/cliente_dhcp_prueba.py`, comprobado contra el servidor Java en `ClientePruebaTest`): `PLAYBOOK=83_prueba_dhcp.yml ./lanzar.sh` y `-e prueba=agotamiento`.

## Límites de esta versión
Las concesiones confirmadas se guardan ademas en un mapa del cluster de ONOS (`campus-dhcp-concesiones`, clave = MAC, valor = `ip|subred|puerto|vencimiento`): sobreviven a reinstalar la app, reiniciar un nodo o cambiar el maestro de un switch (ver docs/DISENO_DHCP_POR_SUBRED.md, 2 quater). Sin reservas por MAC.
