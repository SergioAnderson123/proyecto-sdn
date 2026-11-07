# Servicio MAB de VM (R2). Solo usa la biblioteca estandar: no depende de ninguna imagen local del proyecto.
FROM python:3.10-slim
WORKDIR /r2
COPY mab_vm.py server.py ./
CMD ["python", "-u", "server.py"]
