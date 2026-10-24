FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Instalar FreeRADIUS y dependencias de módulos
RUN apt-get update && apt-get install -y \
    freeradius \
    freeradius-ldap \
    freeradius-rest \
    freeradius-utils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copiar la configuración del proyecto
COPY config/ /etc/freeradius/3.0/

# Ajustar permisos exigidos por el demonio freerad
RUN chown -R freerad:freerad /etc/freeradius/3.0/ \
    && chmod -R u=rwX,g=rX,o= /etc/freeradius/3.0/ \
    && chmod 750 /etc/freeradius/3.0/certs/ \
    && chmod 640 /etc/freeradius/3.0/certs/*

EXPOSE 1812/udp 1813/udp

CMD ["freeradius", "-X"]
