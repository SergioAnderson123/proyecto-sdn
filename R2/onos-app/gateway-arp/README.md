# App de ONOS "Gateway ARP" (capa 3 por subredes)

Instala, **como flujos propios de ONOS**, los flujos estáticos con los que cada switch de acceso contesta el ARP del gateway virtual de su subred, sin controlador (la petición ARP se convierte en respuesta dentro del switch).
Por qué una app y no el REST: el REST de ONOS 2.7 no acepta los criterios `ARP_OP`/`ARP_TPA`, las instrucciones `ARP_OP/SHA/SPA` ni las extensiones de copia de campos (medido en vivo, `docs/DISENO_CAPA3.md` §6, V1); la API Java sí
(así lo hace `openstacknetworking`). Los flujos de una app llevan su appId, así que **no se borran como "extraños"** (`allowExtraneousRules` puede seguir en `false`) y **ONOS los reinstala cuando el switch se reconecta** (los de `ovs-ofctl` se perderían).

**Estado:** escrita contra la API de ONOS 2.7 (`FlowRuleService`, `ExtensionTreatmentResolver`, `NetworkConfigRegistry`); ver en el pie de este archivo si ya se compiló y probó en vivo.

## Qué hace
Por cada `(switch, puerto de acceso, gateway)` de su configuración instala, en la tabla 60 con prioridad 300:
- **coincidencia:** `in_port` + ARP petición + `ARP_TPA` = IP del gateway;
- **acciones:** copiar `eth_src`→`eth_dst`, `eth_src` = MAC común del gateway, `arp_op` = respuesta, copiar `sha`→`tha` y `spa`→`tpa`, `sha` = MAC común, `spa` = IP del gateway, salir por el puerto de entrada (opcionalmente, antes, una copia al controlador para que ONOS aprenda al host).
Reacciona a cambios de configuración y a que un switch aparezca o se reconecte; al desactivar la app retira sus flujos.

## Compilar (en tu PC, con Docker; no en las VM)
```powershell
cd R2\onos-app\gateway-arp
.\construir.ps1            # Docker Desktop abierto; genera target\gateway-arp-1.0.0.oar
```
```bash
./construir.sh             # en Linux/macOS
```
Usa la imagen `maven:3.8.6-eclipse-temurin-11` (ONOS 2.7 usa Java 11) y un volumen de Docker para la caché de Maven (`onos-m2`): la primera vez descarga las dependencias de ONOS (cientos de MB).

## Instalar y configurar (desde `switch-borde`)
`PLAYBOOK=76_gateway_arp.yml ./lanzar.sh` (en `R1/deploy/topologia14`) sube el `.oar`, lo activa, publica la configuración generada con
`python3 R2/policy/generar_config_gateway_arp.py --plan R2/policy/planes/vnrt_topologia14.json` y comprueba que los flujos quedan `ADDED` en cada switch. A mano:
```bash
curl -u onos:rocks -X POST -H 'Content-Type: application/octet-stream' --data-binary @gateway-arp-1.0.0.oar 'http://192.168.201.1:8181/onos/v1/applications?activate=true'
curl -u onos:rocks -X POST -H 'Content-Type: application/json' -d @config.json http://192.168.201.1:8181/onos/v1/network/configuration/apps/org.campus.gatewayarp
```
Configuración (`config.json`), clave `gatewayarp`:
```json
{"gatewayarp": {"gwMac": "02:00:5E:00:00:FE", "tabla": 60, "prioridad": 300, "copiarArp": false,
  "gateways": [{"device": "of:0000e6925d87354b", "ip": "10.0.1.254", "ports": [1]}]}}
```
Quitar la app (`DELETE /onos/v1/applications/org.campus.gatewayarp/active`) retira sus flujos.

> **No reiniciar la app AAA ni desactivar apps por REST en el clúster sin esperar:** el 16-nov una desactivación seguida de una activación dejó una app `ACTIVE` con su característica `Uninstalled`. Para esta app: desactivar, esperar a `INSTALLED`, y activar.
