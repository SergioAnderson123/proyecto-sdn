#!/bin/bash
# Compila la app de ONOS en Docker (Maven + JDK 11). Resultado: target/gateway-arp-1.0.0.oar
# La caché de Maven vive en el volumen `onos-m2`: solo la primera compilación descarga las dependencias de ONOS.
set -e
docker info >/dev/null 2>&1 || { echo "El motor de Docker no responde." >&2; exit 1; }
docker run --rm -v "$(pwd):/app" -v onos-m2:/root/.m2 -w /app maven:3.8.6-eclipse-temurin-11 mvn -B -DskipTests package
ls -l target/*.oar
