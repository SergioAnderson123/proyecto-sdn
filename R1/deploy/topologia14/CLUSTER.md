# Clúster ONOS + Atomix de 3 nodos (fase 3)

Se lanza desde `switch-borde` con el mismo `lanzar.sh`, eligiendo el playbook con `PLAYBOOK`:

```bash
cd ~/proyecto-sdn/R1/deploy/topologia14 && git pull
PLAYBOOK=site_cluster.yml ./lanzar.sh --tags comprobar          # no instala nada
```

## Qué lleva cada nodo
| VM | Atomix | ONOS | Servicios |
|---|---|---|---|
| node-ctrl-1 | atomix-1 | 192.168.201.1 (maestro) | LDAP, RADIUS, Policy Mapper (esta etapa: solo aquí) |
| node-ctrl-2 | atomix-2 | 192.168.201.2 | (etapa posterior: réplicas de R1/R2) |
| node-ctrl-3 | atomix-3 | 192.168.201.3 | (etapa posterior: monitoreo) |

ONOS y Atomix corren con `network_mode: host`: el id de cada nodo es su IP de gestión. Atomix forma un Raft de 3 miembros (tolera la
caída de uno); los ONOS son clientes de ese almacén. Imágenes: `onosproject/onos:2.7-latest` y `atomix/atomix:3.1.12`.

## Pasos
1. **Una vez**, bajar el stack de la fase 1 (conserva imágenes, repo, `.env` y datos de LDAP):
   `PLAYBOOK=site_cluster.yml ./lanzar.sh --tags limpiar -e limpiar_clasico=true`
2. **Preparar y descargar** sin arrancar nada: `PLAYBOOK=site_cluster.yml ./lanzar.sh --tags cluster_config`. Muestra cómo arranca la imagen
   de Atomix (entrypoint, cmd y carpetas): si `/opt/atomix/conf` no existe, hay que ajustar `templates/cluster-compose.yml.j2`.
3. **Clúster:** `PLAYBOOK=site_cluster.yml ./lanzar.sh --tags cluster` (Atomix en los 3, ONOS uno a uno, comprueba 3 nodos READY).
4. **Servicios y apps de ONOS:** `--tags servicios` (LDAP, RADIUS y Policy Mapper en node-ctrl-1; `init-onos.sh` con las IP).
5. **Switches y maestro:** `--tags maestro` (los 5 switches a los 3 ONOS y `device-role` maestro = node-ctrl-1).
6. **Estado:** `--tags estado`.

Todo junto: `PLAYBOOK=site_cluster.yml ./lanzar.sh`.

## Comprobaciones manuales
```bash
# en node-ctrl-1
curl -s -u onos:rocks http://127.0.0.1:8181/onos/v1/cluster | python3 -m json.tool        # 3 nodos, status READY
ssh -p 8101 onos@127.0.0.1                                                                 # clave rocks
  onos> nodes          onos> masters          onos> devices          onos> apps -a -s
docker logs atomix --tail 30 ; docker logs onos --tail 30
# en un switch
sudo ovs-vsctl show                                                                        # 3 Controller, is_connected: true en los que responden
```

## Prueba de tolerancia a fallos
1. `docker stop onos` en node-ctrl-1: en ctrl-2 `masters` debe repartir los 5 switches entre los nodos vivos y los switches siguen conectados.
2. `docker start onos` en node-ctrl-1: vuelve al clúster, pero **no recupera el mando solo**: repetir `--tags maestro`.
3. `docker stop atomix` en un solo nodo: el clúster sigue (mayoría 2 de 3). Con dos Atomix caídos ONOS se degrada: es lo esperado en Raft.

## Pendiente de verificar (no se ha ejecutado contra las VM)
- Ruta de configuración y argumentos por defecto de la imagen de Atomix (la sonda de `cluster_config` lo muestra).
- Que ONOS 2.7 lea `/root/onos/config/cluster.json` en esa imagen (formato tomado de `tools/test/bin/onos-gen-config`).
- Formato de la salida de `masters` (el assert de `45_maestro.yml` no detiene el despliegue si difiere).
- RADIUS: `clients.conf` ahora acepta `192.168.201.0/24` (cualquier ONOS); hay que reconstruir la imagen (lo hace `--tags servicios`).
- El Policy Mapper no comparte sesiones entre réplicas (están en memoria): replicarlo a node-ctrl-2 exige un almacén compartido.
