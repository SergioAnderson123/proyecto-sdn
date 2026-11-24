# =============================================================================
#  Campus SDN — punto de entrada unico de despliegue
#  Idea: el inventario + infra levantan la red UNA vez; los overlays encienden
#  features (r1-802x, r2-mab, r5-ids) sobre la misma topologia.
#  NOTA: andamio. slice.py consolidado y infra/ llegan en el paso 2 (movidas).
# =============================================================================

INVENTORY ?= inventory.ini
SLICE     ?= slice.py
OVERLAY   ?=

PY ?= python3

.PHONY: help up down status test overlay clean

help:
	@echo "make up [OVERLAY=r5-ids]   levanta la topologia del $(INVENTORY)"
	@echo "make down                  la baja"
	@echo "make status                estado de VMs/contenedores"
	@echo "make overlay OVERLAY=r5-ids  aplica un overlay de milestones"
	@echo "make test                  corre los tests (todos los milestones)"
	@echo "make clean                 limpia estado local"

up:
	$(PY) $(SLICE) up --inventory $(INVENTORY) $(if $(OVERLAY),--overlay $(OVERLAY),)

down:
	$(PY) $(SLICE) down --inventory $(INVENTORY)

status:
	$(PY) $(SLICE) status --inventory $(INVENTORY)

overlay:
	@test -n "$(OVERLAY)" || (echo "falta OVERLAY=<nombre>"; exit 1)
	$(PY) $(SLICE) overlay $(OVERLAY) --inventory $(INVENTORY)

test:
	$(PY) -m pytest

clean:
	$(PY) $(SLICE) clean --inventory $(INVENTORY)