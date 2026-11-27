# playground

Código y pruebas de los milestones R1/R2/R5 (ya eliminados del repo) que no forman parte del despliegue real en
`infra/`/`apps/`/`overlays/`, pero que siguen teniendo una prueba que vale la pena conservar. No se corren junto
al resto de la suite (`apps/.../tests`, `overlays/.../tests`, `tests/test_prioridades.py`) ni se mantienen al
mismo nivel: si algo rompe aquí no bloquea el despliegue.

- `agente_802_1x/` — agente 802.1X de los hosts (`agente.py`); usa los fixtures de `supplicants/`.
- `slice/` — asistente de despliegue por Ansible de la VNRT (`slice.py`). Diez de sus pruebas fallan a propósito:
  comprueban playbooks de Ansible y una ruta de `init-onos.sh` que ya no existen (Ansible se descartó del
  rediseño de `infra/`; ver el historial de `redesign/arch-v2`).
- `mab_vm/` — MAC Authentication Bypass de respaldo (`mab_vm.py`).
- `portal_cautivo/` — servidor HTTP(S) del portal cautivo de R2 (`server.py`) y `portal_auth` (ahora en
  `overlays/r2-policy`).
- `supplicants/` — certificados y `.conf` de `wpa_supplicant` de prueba (los regenera
  `apps/radius/raddb/certs/regenerar.sh`); los usan `agente_802_1x/` y `slice/`.

Ejecutar todo:  `python -m pytest tests/playground -q`
