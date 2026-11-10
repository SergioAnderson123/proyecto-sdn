# Despliegue de R5 en el slice de 14 VM (dos sensores IDS)

Adaptado a la topología del **informe previo del Lab 6** (`docs/`, figura 1). La VM Controller (R1/R2) se despliega aparte: ver [`R1/deploy/`](../../R1/deploy/README.md) y [`docs/DESPLIEGUE_UBUNTU.md`](../../docs/DESPLIEGUE_UBUNTU.md).

## 1. Qué cambió respecto al slice anterior

| Antes (VNRT, 31/10) | Ahora (14 VM) | Cómo lo resuelve R5 |
|---|---|---|
| 1 Core, 1 IDS con 1 NIC de espejo | 2 cores (`sw-core-1`, `sw-core-2`) y 2 sensores (`nodo-sec-1`, `nodo-sec-2`) | `topologia.yaml` describe cores, puertos y NIC; cada sensor captura en una NIC por core |
| 1 ONOS, 1 Policy Mapper | Clúster ONOS de 3 nodos; Policy Mapper primario y réplica | El integrador, `espejo.py` y `r5ctl` prueban los nodos en orden y siguen con el siguiente si uno cae |
| DROP externo en un Core | Con ECMP el tráfico del atacante entra por cualquiera de los dos cores | El DROP se instala en **ambos** cores con una sola petición |
| Espejo por `ipv4_dst`/`ipv4_src` en el Core | Un paquete puede cruzar los dos cores | Espejo al **ingresar** (por puerto de entrada), sin copiar la troncal entre cores: una copia por paquete |
| Atacante = alias IP en h4 | `vm-atacante` real detrás del gateway NAT | `redes.externa` en `topologia.yaml` |
| `PUERTO_IDS`, `PUERTO_UPLINK`, `IDS_MAC`, `CORE_DEVICE` en el `.env` | Varios switches, puertos y MAC | Van en `topologia.yaml`, por **nombre de interfaz** (el número de puerto se resuelve contra ONOS) |

El laboratorio de Docker y el modo de un solo Core siguen funcionando sin cambios: si no se define `TOPOLOGIA`, `espejo.py`, el integrador y `r5ctl` usan las variables de entorno de siempre.

## 2. Qué corre en cada VM

| VM | Qué | Carpeta |
|---|---|---|
| `nodo-ctrl-1`, `-2`, `-3` | ONOS (clúster) y servicios de R1/R2; monitoreo en el nodo 3 | `R1/deploy/` |
| `sw-core-1`, `sw-core-2` | Open vSwitch. **Aquí se instala el espejo** de R5 y el DROP de atacantes externos | (ONOS por REST) |
| `sw-acc1`, `sw-acc2` | Open vSwitch con sesiones de R1/R2. **Aquí se instala el DROP de hosts internos** (nunca el espejo, ver 4) | (ONOS por REST) |
| `nodo-sec-1`, `nodo-sec-2` | Suricata + integrador M09 + `r5ctl`; reciben el mismo espejo | `R5/deploy/ids/` |
| `vm-atacante` | Origen de los ataques externos | `R5/pruebas/` |

Cada sensor tiene `ens3` (gestión) y una NIC de espejo por switch que le copia tráfico. En R5 se usan las de los **cores**; las de los accesos quedan sin usar hasta resolver el punto 4.

```
 vm-atacante -- gateway(NAT) --+-- sw-core-1 ==LAG== sw-core-2 --+-- servidores
                               |      |  \   espejo   /  |      |
                               |      |   nodo-sec-1/2   |      |
                               +------+-- sw-acc1/2 ------+------+-- h1, h2 (+ netns h3, h4)
```

## 3. Decisiones de diseño

1. **Espejo al ingresar.** Por cada core, por cada puerto de `entradas` (gateway, servidores, accesos) y por cada prefijo (`redes.home` y `redes.externa`), una regla `in_port + ipv4_src` a prioridad 41000 que reenvía con `NORMAL` y copia a los dos sensores. Como cada paquete entra a la red por un solo puerto de entrada, se copia una sola vez aunque ECMP lo reparta; un paquete que cruce los dos cores entra al segundo por la troncal (LAG), que no está en `entradas`. Por eso el espejo es **selectivo por subred**: solo IPv4 de las redes declaradas.
2. **Los dos sensores ven todo.** Cada sensor recibe la copia de ambos cores, así que cada uno ve ambos sentidos de un flujo aunque ECMP los reparta, y si uno cae el otro mantiene la cobertura. A cambio, cada alerta se detecta dos veces y los dos integradores piden el mismo DROP: ONOS lo trata como una sola regla (mismo `appId`, switch, selector y prioridad), que solo se refresca.
3. **MAC por NIC.** La red virtual solo entrega a una VM las tramas dirigidas a la MAC de la NIC. El espejo reescribe `eth_dst` de cada copia con la MAC de la NIC del sensor que le toca (`mac` en `topologia.yaml`); el reenvío normal sale primero y con la MAC original.
4. **DROP.** Atacante externo: en **todos** los cores. Host interno: en el acceso donde ONOS lo ubica (`GET /hosts`), y además se pide al Policy Mapper (primario o réplica) revocar su sesión. Prioridad 42000: gana a la sesión de R2 (40001) y queda bajo el DROP temporal de R1 (45000). Mapa completo: [`prioridades.yaml`](prioridades.yaml).
5. **Clúster.** `ONOS_URL` y `MAPPER_URL` admiten varios nodos: se usa el primero que responda y se recuerda cuál fue. Un 4xx no cambia de nodo (es una respuesta válida de ONOS); un fallo de red o un 5xx sí.
6. **NIC del sensor.** `up.sh` las deja en modo promiscuo, sin IPv6 y sin GRO/LRO, para que el sensor no transmita nada hacia los cores y Suricata no vea paquetes unidos que no existieron.

## 4. Decisión pendiente: espejo en los accesos

El informe previo dice que los accesos también espejan (tráfico este-oeste). **No está implementado**, y no es un detalle: el espejo de R5 reenvía con `NORMAL` a prioridad 41000, que es mayor que el flujo de sesión de R2 (40001). En un acceso, esa regla capturaría el paquete antes que las tablas de rol y **se saltaría la política de R2**. Opciones:

| Opción | Qué implica | Riesgo |
|---|---|---|
| **A. Solo cores (lo implementado)** | Todo el tráfico entre VM distintas y con los servidores cruza un core, así que se ve igual. No se ve (a) lo que R2 descarta en el acceso antes de llegar al core, ni (b) el tráfico entre h1 y sus netns (no sale de la VM) | Ninguno para R2. Un escaneo contra un recurso que el rol no puede alcanzar nunca llega al core |
| **B. Mirror nativo de OVS en los accesos** (`ovs-vsctl set bridge <br> mirrors=...`, `select-src-port` en los puertos de los hosts) | Copia antes del pipeline OpenFlow, sin tocar R2 | No reescribe la MAC destino: en VNRT la copia no llegaría al sensor (ver 3.3). Habría que probarlo |
| **C. Que R2 incluya el espejo en sus flujos** (grupo `ALL` con la copia dentro de las reglas de sesión y de confianza) | Espejo correcto y sin saltarse tablas | Cambia `R2/policy/policy_tables.py` y su prueba de prioridades |

Recomendación: A para el piloto y dejar escrito en el informe que el este-oeste previo al core queda fuera; probar B en el slice si hay tiempo.

## 5. Archivos

| Archivo | Para qué | ¿Se versiona? |
|---|---|---|
| [`topologia.example.yaml`](topologia.example.yaml) | Plantilla: nodos ONOS y Policy Mapper, redes, cores (deviceId, `entradas`, puerto y MAC de espejo por sensor) | Sí |
| `topologia.yaml` | La del slice real (se copia de la plantilla) | **No** (los deviceId y las MAC cambian en cada slice) |
| [`.env.example`](.env.example) → `.env` | Ajustes de **este** sensor: `SENSOR`, `IDS_IFACES`, credenciales de ONOS | `.env` no |
| [`ids/up.sh`](ids/up.sh) | Valida la topología, prepara las NIC, levanta Suricata e integrador e instala el espejo | Sí |
| [`ids/macs.sh`](ids/macs.sh) | Lista las NIC del sensor con su MAC (antes de llenar `topologia.yaml`) | Sí |

## 6. Puesta en marcha

Requisitos: los tres nodos ONOS arriba, los cuatro switches y el gateway conectados (`python3 R1/deploy/slice.py estado`), Docker en los sensores y el repo clonado en cada uno.

**6.1 Datos del slice (una vez, desde cualquier sensor).** Con `.env` y una `topologia.yaml` provisional:

```bash
cd ~/proyecto-sdn/R5/deploy
cp .env.example .env && nano .env                    # SENSOR=nodo-sec-1 (o -2), IDS_IFACES, ONOS_USER/PASS
cp topologia.example.yaml topologia.yaml && nano topologia.yaml
```

Lo que hay que rellenar y de dónde sale:

| Dato | Cómo se obtiene |
|---|---|
| IP de los nodos ONOS y Policy Mapper | Red de gestión `192.168.100.0/24` |
| `device` y nombres de interfaz de cada core | `sudo apt-get install -y python3-requests && ONOS_URL=http://<IP-ONOS>:8181/onos/v1 ONOS_PASS=rocks python3 ../lab/espejo.py --descubrir` (lista cada switch con `puerto N  nombre`). Si ONOS no entrega nombres de puerto, se pueden escribir los **números** en `topologia.yaml` |
| `entradas` | Interfaces del core hacia gateway, servidores, `sw-acc1` y `sw-acc2`. **No** la troncal entre cores ni las de espejo |
| MAC de cada NIC del sensor | `bash ids/macs.sh` en `nodo-sec-1` y en `nodo-sec-2` |
| Qué NIC del sensor está cableada a qué core | Ver **Identificar los enlaces** |

**Identificar los enlaces** (si el diseño del slice no lo dice). En el core, saca una trama por la interfaz de espejo y mira en qué NIC del sensor aparece:

```bash
# en sw-core-1, por cada interfaz de espejo (p. ej. ens11)
sudo arping -c 3 -I ens11 -s 192.0.2.1 192.0.2.2
# en nodo-sec-1 y nodo-sec-2
sudo tcpdump -ni ens4 -c 3 arp          # repetir con cada NIC de espejo: la que ve el ARP es la del otro extremo
```

**6.2 Validar y levantar** (en **cada** sensor, con su propio `.env`):

```bash
python3 ../codigo/topologia.py topologia.yaml        # OK: 2 core(s), 2 sensor(es), 3 nodo(s) ONOS ...
cd ids && chmod +x up.sh macs.sh && ./up.sh
```

`up.sh` imprime la MAC de cada NIC (deben coincidir con `topologia.yaml`), levanta Suricata e integrador, instala el espejo en los dos cores y muestra `r5ctl estado`. Instalar el espejo desde los dos sensores es inocuo: ONOS reemplaza reglas idénticas.

**6.3 Comprobar**

```bash
docker compose exec integrador r5ctl estado
#   Espejo al IDS (reglas / paquetes copiados por core):
#     sw-core-1: 8 reglas, N paquetes ...      <- N debe crecer con el tráfico
#     sw-core-2: 8 reglas, N paquetes ...
sudo tcpdump -ni ens4 -c 5 'ip'                      # en cada sensor, en cada NIC de espejo: debe verse tráfico IP
docker logs r5-suricata 2>&1 | tail                  # "af-packet en: ens4, ens5" y sin errores de captura
```

Con el espejo contando y `tcpdump` sin tráfico, la red virtual está descartando las copias: revisa que `mac` sea la de la NIC del sensor (no la de otra). Con `tcpdump` viendo tráfico y Suricata sin alertas, revisa `HOME_NET`, que sale de `redes.home`.

**6.4 Probar desde `vm-atacante`** (escenarios de `R5/lab/docker/attacks.py` adaptados, ver `R5/pruebas/`):

```bash
nmap -sS -p 1-1000 --max-rate 100 10.0.0.40         # debe bloquearse: r5ctl bloqueos muestra el DROP en sw-core-1 Y sw-core-2
docker compose exec integrador r5ctl bloqueos
docker compose exec integrador r5ctl desbloquear 203.0.113.10
```

## 7. Operación

```bash
docker compose exec integrador r5ctl estado|bloqueos|alertas -n 20|bloquear IP|desbloquear IP
docker compose run --rm espejo python3 /opt/r5/lab/espejo.py --quitar     # retira solo las reglas de espejo
docker compose down                                                       # apaga este sensor (el otro sigue)
```

`r5ctl bloquear IP` bloquea en ambos cores por defecto (`--dispositivo` para uno solo).

## 8. Lo que asume y lo que falta validar

Todo esto se probó con **pruebas unitarias y `docker compose config`**; **no se ha ejecutado en las VM del slice**:

- **Nombres de puerto.** Se asume que ONOS entrega `portName` en las anotaciones de cada puerto (`GET /devices/{id}/ports`). Si no, usar números en `topologia.yaml`.
- **Reenvío de los cores con `NORMAL`.** El espejo reenvía con `NORMAL`, igual que R2 en sus puertos de confianza. Con los lazos entre cores y accesos y sin STP (el informe previo lo señala como pendiente), `NORMAL` puede crear tormentas de broadcast; si los cores reenvían de otra forma (grupos ECMP de ONOS), `NORMAL` cambiaría cómo se reenvía el tráfico espejado. Hay que resolver el lazo antes de instalar el espejo.
- **Red externa.** El informe previo no define la subred entre `vm-atacante` y el gateway. Se **propone** `203.0.113.0/24` (rango de documentación) en `redes.externa`; el gateway debe enrutar sin NAT hacia dentro (solo enmascara la salida del campus), para que los cores vean la IP real del atacante.
- **Servidor DHCP.** `10.0.0.250` (app DHCP de ONOS, R1/R2) está en la lista blanca; si el slice usa otra IP, cambiarla en `redes.lista_blanca`.
- **Recursos.** El informe previo prevé 2 vCPU y 4 GB por sensor con las reglas locales, y 4 vCPU y 8 GB con ET Open (`SURICATA_ET=1`). Con dos copias del tráfico de ambos cores, cada sensor recibe todo el tráfico espejado: medir CPU de Suricata y de los cores bajo ataque.
- **Los dos sensores ven lo mismo,** así que un ataque genera alertas y DROP duplicados. Es inocuo en ONOS, pero `r5ctl alertas` mostrará cada alerta dos veces si se mezclan los `eve-alerts.json`.
- **Mediciones.** Las del 31/10 (VNRT, un Core, un IDS) no son comparables con las de este slice: hay que repetirlas.

## 9. Convivencia con R1 y R2

El espejo se instala **solo en los cores**: un switch con sesiones de R2 (los accesos) lo saltaría (ver 4). Prioridades: espejo 41000, DROP de R5 42000, DROP temporal de R1 45000, sesión de R2 40001. `tests/test_prioridades.py` verifica este mapa contra el código. Cuando R5 bloquea a un host interno, el integrador pide al Policy Mapper `POST /sessions/revoke` (`MAPPER_URL`) para revocar también su sesión.
