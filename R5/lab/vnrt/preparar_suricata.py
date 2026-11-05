"""Genera ~/r5/suricata.yaml a partir del de Ubuntu: HOME_NET de VNRT, af-packet en ens4,
reglas locales + ET SCAN, y salida eve-alerts.json solo de alertas. Se ejecuta sin root."""
import os, re
home = os.path.expanduser("~/r5")
t = open("/etc/suricata/suricata.yaml").read()
t = re.sub(r'(?m)^(\s*HOME_NET:).*$', r'\1 "[10.0.0.0/30]"', t, count=1)
t = re.sub(r'(?m)^default-log-dir:.*$', f'default-log-dir: {home}/log', t)
t = re.sub(r'(?m)^default-rule-path:.*$', f'default-rule-path: {home}/rules', t)
t = re.sub(r'(?ms)^rule-files:\n(?:\s*-.*\n)+', 'rule-files:\n  - local.rules\n  - emerging-scan.rules\n', t)
t = re.sub(r'(?ms)^af-packet:\n\s*- interface: \S+', 'af-packet:\n  - interface: ens4', t, count=1)
# eve-log existente: se desactiva y se agrega uno propio solo con alertas
t = re.sub(r'(?ms)(- eve-log:\n\s+enabled: )yes', r'\1no', t, count=1)
t = t.replace("outputs:\n", "outputs:\n  - eve-log:\n      enabled: yes\n      filetype: regular\n      filename: eve-alerts.json\n      ethernet: yes\n      types:\n        - alert\n", 1)
open(f"{home}/suricata.yaml", "w").write(t)
print("suricata.yaml generado")
