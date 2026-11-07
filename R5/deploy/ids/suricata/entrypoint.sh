#!/bin/bash
# Prepara y lanza Suricata en la VM IDS. Variables: IDS_IFACE, HOME_NET, SURICATA_ET (1 = descargar ET Open).
# Deja un unico archivo de alertas (eve-alerts.json) que lee el integrador M09. Es idempotente: se parchea en cada arranque.
set -e
CFG=/etc/suricata/suricata.yaml
RULES=/var/lib/suricata/rules
mkdir -p "$RULES" /var/log/suricata

cp /opt/r5/local.rules "$RULES/local.rules"
if [ "${SURICATA_ET:-0}" = "1" ]; then
  [ -s "$RULES/suricata.rules" ] || suricata-update || echo "[r5] suricata-update fallo; se usan solo las reglas locales"
fi

# HOME_NET del campus
sed -i "s|^\( *HOME_NET:\).*|\1 \"[${HOME_NET:-10.0.0.0/24}]\"|" "$CFG"

# reglas: locales siempre; ET Open solo si se descargo
if [ "${SURICATA_ET:-0}" = "1" ] && [ -s "$RULES/suricata.rules" ]; then
  grep -q 'local.rules' "$CFG" || sed -i 's/^\( *\)- suricata.rules/&\n\1- local.rules/' "$CFG"
else
  sed -i 's/^\( *\)- suricata.rules/\1- local.rules/' "$CFG"
fi

# salida de alertas dedicada: se apaga el eve.json por defecto (todos los eventos: llena el disco en horas) y se agrega eve-alerts.json
if ! grep -q 'eve-alerts.json' "$CFG"; then
  python3 - "$CFG" <<'PY'
import re, sys
p = sys.argv[1]
t = open(p).read()
t = re.sub(r'(- eve-log:\n\s+enabled: )yes', r'\1no', t, count=1)
t = t.replace("outputs:\n", "outputs:\n  - eve-log:\n      enabled: yes\n      filetype: regular\n      filename: eve-alerts.json\n      ethernet: yes\n      types:\n        - alert\n", 1)
open(p, "w").write(t)
PY
fi

suricata -T -c "$CFG" -v
# -k none: la red virtual puede entregar tramas con checksum sin calcular (offload)
exec suricata -c "$CFG" --af-packet="${IDS_IFACE:?falta IDS_IFACE}" -k none
