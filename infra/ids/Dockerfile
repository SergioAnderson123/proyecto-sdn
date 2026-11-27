# Imagen de R5: integrador M09, CLI r5ctl y el instalador del espejo.
# Solo las dependencias: el codigo (integrador.py, topologia.py, r5ctl.py, espejo.py) no se copia a la imagen,
# compose.yml lo monta entero en /opt/r5/codigo desde apps/ids/integrador (igual que ya hace con topologia.py
# en el servicio suricata).
FROM python:3.12-slim
RUN pip install --no-cache-dir requests pyyaml
RUN printf '#!/bin/sh\nexec python3 /opt/r5/codigo/r5ctl.py "$@"\n' > /usr/local/bin/r5ctl && chmod +x /usr/local/bin/r5ctl
WORKDIR /opt/r5/codigo
CMD ["python3", "-u", "/opt/r5/codigo/integrador.py"]
