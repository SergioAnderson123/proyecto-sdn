# R1: control de acceso (802.1X + EAP-TLS, MAC binding y MAB)

Stack de laboratorio: ONOS 2.7, Mininet, OpenLDAP, Policy Mapper (FastAPI) y FreeRADIUS. Flujos: [`flujos_R1_R2.md`](flujos_R1_R2.md).

| Carpeta | Contenido |
|---|---|
| `compose.yml` | Stack completo (desde la raíz del repo: `docker compose -f R1/compose.yml up -d --build`) |
| `shared/policy_mapper/` | Policy Mapper, Session Manager y bitácora de eventos, con sus pruebas |
| `shared/ldap/`, `shared/mininet/` | Estructura LDAP, topología base y supplicants (`wpa_*.conf`) |
| `radius/`, `mininet/`, `policy_mapper/` | Imagen y configuración de FreeRADIUS, Mininet (`init-onos.sh`, `start-ovs.sh`) y Policy Mapper (Dockerfile) |
| `deploy/controller/` | Despliegue en la VM Controller de VNRT ([README](deploy/README.md)) |

El Policy Mapper también usa módulos de R2 (`R2/policy/`), que `compose.yml` monta junto al código de R1.

Pruebas: `python -m pytest R1/shared/policy_mapper/tests -q` (requiere `pip install -r R1/policy_mapper/requirements.txt`).

---

# Using podman/docker
```
podman compose build
podman compose up -d
podman ps
```
