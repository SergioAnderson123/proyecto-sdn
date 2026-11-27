# Imagen de R5: integrador M09, CLI r5ctl y el instalador del espejo.
# El contexto de build es este mismo directorio (apps/ids/integrador): todo lo que necesita ya vive aqui.
FROM python:3.12-slim
RUN pip install --no-cache-dir requests pyyaml
WORKDIR /opt/r5
COPY integrador.py codigo/integrador.py
COPY topologia.py codigo/topologia.py
COPY r5ctl.py cli/r5ctl.py
COPY espejo.py lab/espejo.py
RUN printf '#!/bin/sh\nexec python3 /opt/r5/cli/r5ctl.py "$@"\n' > /usr/local/bin/r5ctl && chmod +x /usr/local/bin/r5ctl
CMD ["python3", "-u", "/opt/r5/codigo/integrador.py"]
