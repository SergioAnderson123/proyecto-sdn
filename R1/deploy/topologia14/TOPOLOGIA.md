# Correspondencia de nombres de la topología de 14 VM (verificada con MAC)

La interfaz web de VNRT, el PDF de IP y los hostnames de las VM **no coincidían en los dos switches de acceso**. Se comprobó cruzando las
MAC de las interfaces (`ip -br link` en cada VM) con las ventanas de cada nodo de VNRT (cada ventana lista, por `Enlace N`, la MAC de la NIC
de **ese** nodo; el mismo número de enlace en dos ventanas son los dos extremos de un cable).

**Decisión (14-nov-2026):** los hostnames de las dos VM de acceso se intercambiaron para que coincidan con los nombres de la interfaz de VNRT
y con el dibujo oficial. Las IP y los puertos DNAT no se tocaron.

## Switches de acceso

| Hostname (ahora) | IP de gestión | Puerto SSH | Nombre en VNRT | MAC de `ens4` | Cable (`Enlace`) | Host conectado | deviceId en ONOS |
|---|---|---|---|---|---|---|---|
| `sw-acc-1` | 192.168.201.**12** | **5812** | `sw-acc-1` | `fa:16:3e:3d:fe:8a` | 18 | **host-bob** = `h1-profesor` | `of:0000e6925d87354b` |
| `sw-acc-2` | 192.168.201.**11** | **5811** | `sw-acc-2` | `fa:16:3e:4a:5a:2c` | 19 | **host-alice** = `h2-alumno` | `of:0000766a0c84c741` |

**Ojo con el PDF de IP:** allí `sw-acc-1` figura con `.11` y el puerto 5811, y `sw-acc-2` con `.12` y el 5812. Para estos dos, **el PDF está invertido**:
la IP `.11` (puerto 5811) es hoy `sw-acc-2`, y la `.12` (puerto 5812) es `sw-acc-1`. El `inventario.ini` ya lo refleja.

Ventanas de VNRT usadas: nodo `sw-acc-1` (`MP_Link 0` `fa:16:3e:f8:d8:dc`, `Enlace 18` `fa:16:3e:3d:fe:8a`, `Enlace 20` `fa:16:3e:ae:bd:ec`,
`Enlace 23` `fa:16:3e:9c:76:d1`) y nodo `sw-acc-2` (`MP_Link 0` `fa:16:3e:07:ca:22`, `Enlace 19` `fa:16:3e:4a:5a:2c`, `Enlace 21`
`fa:16:3e:bd:84:40`, `Enlace 22` `fa:16:3e:31:ac:cc`).

## Hosts

| Hostname | IP de gestión | Puerto SSH | Nombre en VNRT | MAC de `ens4` | Cable | Conecta con |
|---|---|---|---|---|---|---|
| `h1-profesor` | 192.168.201.21 | 5821 | **host-bob** | `fa:16:3e:b6:96:08` | `Enlace 18` | `sw-acc-1` (`ens4` del acceso) |
| `h2-alumno` | 192.168.201.22 | 5822 | **host-alice** | `fa:16:3e:71:08:66` | `Enlace 19` | `sw-acc-2` (`ens4` del acceso) |
| `vm-atacante` | sin red de gestión; `ens3` **10.20.11.203/24** (pública, DHCP) | 22, **por su IP** (`ssh ubuntu@10.20.11.203`; el 5824 no se usa) | **host-jhon** | `fa:16:3e:eb:ea:4c` (leída en la VM, 23-nov; cable sin cruzar con VNRT) | — | `switch-borde` (`ens4`, puerto 1 de OVS, MAC `fa:16:3e:12:48:4c`) |

`vm-atacante` (23-nov): `ens4` lleva la IP **fija** `203.0.113.103/24` (subred externa, sin DHCP) y `ens3` la MAC `fa:16:3e:55:29:68`. La MAC de su `ens4` es el **siguiente salto** de la ruta por defecto del vigilante de capa 3 (`salida` en `R2/policy/planes/vnrt_topologia14.json`): si cambia la NIC hay que actualizar ese plan. La tabla de IP y puertos para consulta está en `docs/Tabla_IPs_y_puertos_VM_actualizada.pdf` (fuente: el `.html` de la misma carpeta).

En resumen: **host-bob es `h1-profesor` (cuelga de `sw-acc-1`) y host-alice es `h2-alumno` (cuelga de `sw-acc-2`)**. El nombre del host no
cambió: el hostname real sigue siendo `h1-profesor` / `h2-alumno`; bob, alice y jhon son los nombres que usa VNRT en su interfaz y en el dibujo.

## Sin verificar todavía
Los cores (`sw-core-1`, `sw-core-2`), `server`, los sensores y el borde no se han cruzado con la interfaz de VNRT. Si los accesos estaban
cruzados, conviene repetir la comprobación con ellos antes de fiarse de las interfaces del dibujo (qué `ens5`/`ens6` va a qué core).
Para comprobarlo sin enviar tráfico: `ip -br link | grep ens` en cada VM y comparar con la ventana del nodo en VNRT; y en la consola de ONOS
(`ssh -p 8101 onos@192.168.201.1`, clave `rocks`) el comando `links` da los pares `deviceId/puerto` ya descubiertos por LLDP.

## Cómo se renombraron las VM
```bash
sudo hostnamectl set-hostname <nombre nuevo>
echo "preserve_hostname: true" | sudo tee /etc/cloud/cloud.cfg.d/99-hostname.cfg      # cloud-init no lo revierte al reiniciar
sudo sed -i 's/<nombre viejo>/<nombre nuevo>/' /etc/hosts
```
ONOS no se ve afectado: identifica los switches por su `deviceId` (el datapath de `br0`), que no cambia con el hostname.
