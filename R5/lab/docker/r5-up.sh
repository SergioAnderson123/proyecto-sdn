#!/bin/bash
# Arranque del laboratorio R5 dentro del contenedor "lab". Se puede repetir sin romper nada.
set -u
R5=/opt/r5
LOG=/var/log/r5
mkdir -p "$LOG" /var/run/openvswitch /var/lib/integrador /evidencia /var/log/suricata
say() { echo "[r5-up] $*"; }

# --- helper: ejecutar un comando dentro de un host de Mininet (hx hext1 nmap ...) ---
cat > /usr/local/bin/hx <<'EOF'
#!/bin/bash
h="$1"; shift
pid=$(pgrep -f "mininet:${h}\$" | head -1)
[ -z "$pid" ] && { echo "host $h no existe" >&2; exit 1; }
exec mnexec -a "$pid" "$@"
EOF
chmod +x /usr/local/bin/hx

# --- CLI de administración de R5 (r5ctl) ---
cat > /usr/local/bin/r5ctl <<'EOF'
#!/bin/bash
exec python3 /opt/r5/cli/r5ctl.py "$@"
EOF
chmod +x /usr/local/bin/r5ctl

# --- Open vSwitch en modo usuario ---
say "Open vSwitch"
[ -f /etc/openvswitch/conf.db ] || ovsdb-tool create /etc/openvswitch/conf.db /usr/share/openvswitch/vswitch.ovsschema
pgrep -x ovsdb-server >/dev/null || ovsdb-server /etc/openvswitch/conf.db \
    --remote=punix:/var/run/openvswitch/db.sock --remote=db:Open_vSwitch,Open_vSwitch,manager_options --pidfile --detach
ovs-vsctl --no-wait init
pgrep -x ovs-vswitchd >/dev/null || ovs-vswitchd --pidfile --detach

# --- ONOS ---
say "Esperando ONOS..."
until curl -s -u "$ONOS_USER:$ONOS_PASS" "$ONOS_URL/applications" >/dev/null; do sleep 3; done
for app in org.onosproject.openflow org.onosproject.fwd; do
  # ONOS responde a la API antes de terminar de cargar sus aplicaciones: se reintenta hasta que quede ACTIVE
  until curl -s -u "$ONOS_USER:$ONOS_PASS" "$ONOS_URL/applications/$app" | grep -q '"state":"ACTIVE"'; do
    curl -s -u "$ONOS_USER:$ONOS_PASS" -X POST "$ONOS_URL/applications/$app/active" >/dev/null
    sleep 3
  done
done
say "ONOS listo (openflow + fwd)"

# --- Suricata: reglas ET Open + locales, salida de alertas dedicada ---
SURI=/etc/suricata/suricata.yaml
if [ ! -s /var/lib/suricata/rules/suricata.rules ]; then
  say "Descargando reglas ET Open (suricata-update)"
  suricata-update >"$LOG/suricata-update.log" 2>&1 || say "suricata-update falló; ver $LOG/suricata-update.log"
fi
cp "$R5/suricata/local.rules" /var/lib/suricata/rules/local.rules
grep -q 'local.rules' "$SURI" || sed -i 's/^\( *\)- suricata.rules/&\n\1- local.rules/' "$SURI"
sed -i 's|^\( *HOME_NET:\).*|\1 "[10.0.0.0/24]"|' "$SURI"
grep -q 'eve-alerts.json' "$SURI" || sed -i '/^outputs:/a\  - eve-log:\n      enabled: yes\n      filetype: regular\n      filename: eve-alerts.json\n      types:\n        - alert' "$SURI"
suricata -T -c "$SURI" >"$LOG/suricata-test.log" 2>&1 && say "configuración de Suricata válida" || say "ERROR en la configuración de Suricata (ver $LOG/suricata-test.log)"

# --- Topología ---
rm -f /tmp/topo.ready
say "Levantando topología Mininet"
python3 "$R5/lab/docker/topo_r5.py" >"$LOG/mininet.log" 2>&1 &
until [ -f /tmp/topo.ready ]; do sleep 2; done
say "Topología lista"

# --- Espejo en el Core ---
python3 "$R5/lab/espejo.py" | tee "$LOG/espejo.log"

# --- Suricata en hids (interfaz hids-eth0), sin verificar checksums por el OVS en modo usuario ---
hx hids suricata -c "$SURI" -i hids-eth0 -k none -l /var/log/suricata >"$LOG/suricata.log" 2>&1 &
say "Esperando a que Suricata cargue reglas..."
until grep -qi "engine started" /var/log/suricata/suricata.log 2>/dev/null; do sleep 2; done
say "Suricata activo"

# --- Integrador M09 y panel ---
rm -f "$STATE_PATH"
# las alertas de sesiones anteriores quedan en el volumen: el integrador arranca desde el final del archivo
python3 - <<'PY'
import json, os
eve, st = os.environ.get("EVE_PATH", "/var/log/suricata/eve-alerts.json"), os.environ["STATE_PATH"]
if os.path.exists(eve):
    os.makedirs(os.path.dirname(st), exist_ok=True)
    s = os.stat(eve)
    json.dump({"bloqueadas": {}, "inode": s.st_ino, "offset": s.st_size}, open(st, "w"))
PY
python3 "$R5/codigo/integrador.py" >"$LOG/integrador.log" 2>&1 &
python3 "$R5/lab/docker/dashboard.py" >"$LOG/dashboard.log" 2>&1 &
python3 "$R5/lab/docker/monitoring/exporter.py" >"$LOG/exporter.log" 2>&1 &
say "LISTO: Grafana http://localhost:3000 · EveBox http://localhost:5636 · escenarios http://localhost:8080"
wait
