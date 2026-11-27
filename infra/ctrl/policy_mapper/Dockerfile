# Imagen base del Policy Mapper (R1): solo las dependencias. El codigo (policy_mapper.py, eventos.py,
# session_manager.py) no se copia a la imagen: compose.yml lo monta en /app desde apps/onos/policy_mapper.
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5000
CMD ["uvicorn", "policy_mapper:app", "--host", "0.0.0.0", "--port", "5000"]
