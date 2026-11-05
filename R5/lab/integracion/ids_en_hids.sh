#!/bin/bash
# Prueba de integracion R2 + R5 en el laboratorio de Docker: Suricata en el host `hids` (s2, puerto 4) y el integrador M09.
# Se ejecuta DENTRO del contenedor mininet:  bash /root/r5/ids_en_hids.sh {suricata|integrador|espejo|estado|parar}
# Requisitos: topologia topo_baseR2_userspace.py en marcha (con `hids`), archivos copiados a /root/r5 (ver R5/lab/integracion/README.md).
R5=/root/r5; LOG=$R5/log; mkdir -p "$LOG"
export ONOS_URL=http://onos:8181/onos/v1 ONOS_USER=onos ONOS_PASS=rocks APP_ID=org.campus.r5
export HOME_NET=10.0.0.0/24 LISTA_BLANCA=10.0.0.40/32,10.0.0.50/32,10.0.0.99/32
export CORE_DEVICE=of:0000000000000001            # respaldo si ONOS no conoce al host: el switch de acceso (s1)
export EVE_PATH=$LOG/eve-alerts.json STATE_PATH=$R5/state.json MAPPER_URL=http://policy_mapper:5000 BLOCK_SEC=120
export P_R5=42000 P_ESPEJO=41000 MAX_SEVERITY=2 POLL_SEC=0.1

case "$1" in
suricata)
  CFG=$R5/suricata.yaml; RULES=$R5/rules; mkdir -p "$RULES"; cp $R5/local.rules "$RULES/local.rules"
  cp /etc/suricata/suricata.yaml "$CFG"
  python3 - "$CFG" "$RULES" "$LOG" <<'PY'
import re, sys
cfg, rules, log = sys.argv[1:4]
t = open(cfg).read()
t = re.sub(r'(?m)^(\s*HOME_NET:).*$', r'\1 "[10.0.0.0/24]"', t, count=1)
t = re.sub(r'(?m)^default-log-dir:.*$', f'default-log-dir: {log}', t)
t = re.sub(r'(?m)^default-rule-path:.*$', f'default-rule-path: {rules}', t)
t = re.sub(r'(?ms)^rule-files:\n(?:\s*-.*\n)+', 'rule-files:\n  - local.rules\n', t)
t = re.sub(r'(?ms)(- eve-log:\n\s+enabled: )yes', r'\1no', t, count=1)
t = t.replace("outputs:\n", "outputs:\n  - eve-log:\n      enabled: yes\n      filetype: regular\n      filename: eve-alerts.json\n      ethernet: yes\n      types:\n        - alert\n", 1)
open(cfg, "w").write(t)
PY
  suricata -T -c "$CFG" >"$LOG/suricata-test.log" 2>&1 && echo "configuracion valida" || { tail -5 "$LOG/suricata-test.log"; exit 1; }
  nohup /root/shared-host/mnx.sh hids suricata -c "$CFG" --af-packet=hids-eth0 -k none -l "$LOG" >"$LOG/suricata.out" 2>&1 &
  sleep 12; tail -2 "$LOG/suricata.out"
  ;;
integrador)
  : > "$LOG/integrador.log"; rm -f "$STATE_PATH"
  nohup python3 $R5/integrador.py >"$LOG/integrador.log" 2>&1 &
  sleep 2; tail -3 "$LOG/integrador.log"
  ;;
espejo)
  # s2 hace de Core en este laboratorio: copia al IDS (puerto 4) lo que llega por el enlace a s1 (puerto 3) y el trafico interno
  ONOS_URL=http://onos:8181/onos/v1 CORE_DEVICE=of:0000000000000002 PUERTO_UPLINK=3 PUERTO_IDS=4 \
    ESPEJO_HOME_NET=10.0.0.0/24 ESPEJO_RED_EXTERNA=10.0.1.0/24 python3 $R5/espejo.py
  ;;
estado)
  echo "--- procesos"; pgrep -af "[s]uricata -c|[i]ntegrador.py" | cut -c1-90
  echo "--- espejo en s2 (prioridad 41000)"; ovs-ofctl -O OpenFlow13 dump-flows s2 | grep -E "priority=41000" | sed 's/cookie=[^,]*, duration=[^,]*, //; s/send_flow_rem //' | cut -c1-170
  echo "--- alertas"; wc -l < "$LOG/eve-alerts.json" 2>/dev/null; tail -4 "$LOG/integrador.log" | cut -c1-170
  ;;
parar)
  pkill -f "[s]uricata -c"; pkill -f "[i]ntegrador.py"; echo parado
  ;;
*) echo "uso: $0 {suricata|integrador|espejo|estado|parar}"; exit 2 ;;
esac
