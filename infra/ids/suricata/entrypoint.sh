#!/bin/bash
# Prepara y lanza Suricata en un sensor (nodo-sec-1 o nodo-sec-2).
# Variables: IDS_IFACES (interfaces de espejo, separadas por comas; IDS_IFACE se acepta por compatibilidad), HOME_NET (si falta, sale de
# topologia.yaml), SURICATA_ET (1 = descargar ET Open).
# Deja un unico archivo de alertas (eve-alerts.json) que lee el integrador M09. Es idempotente: se parchea en cada arranque.
set -e
CFG=/etc/suricata/suricata.yaml
RULES=/var/lib/suricata/rules
mkdir -p "$RULES" /var/log/suricata

cp /opt/r5/local.rules "$RULES/local.rules"
if [ "${SURICATA_ET:-0}" = "1" ]; then
  [ -s "$RULES/suricata.rules" ] || suricata-update || echo "[r5] suricata-update fallo; se usan solo las reglas locales"
fi

# HOME_NET del campus: la variable manda; si no esta, se toma de topologia.yaml (una sola fuente de verdad con el integrador y el espejo)
if [ -z "${HOME_NET:-}" ] && [ -f /opt/r5/topologia.yaml ]; then
  HOME_NET="$(python3 /opt/r5/codigo/topologia.py /opt/r5/topologia.yaml --home-net)" || true
fi
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

# interfaces de captura: una por switch que espeja a este sensor
IFACES="${IDS_IFACES:-${IDS_IFACE:?falta IDS_IFACES}}"
python3 /opt/r5/af_packet.py "$CFG" "$IFACES"

suricata -T -c "$CFG" -v
# -k none: la red virtual puede entregar tramas con checksum sin calcular (offload)
# --af-packet sin interfaz: usa las del archivo de configuracion (todas las de IDS_IFACES)
exec suricata -c "$CFG" --af-packet -k none
