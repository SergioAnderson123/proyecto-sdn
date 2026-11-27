# Configuración de Suricata (host `hids`)

Suricata 8.0.7 instalado en el contenedor de Mininet (`dnf install -y suricata tcpdump`), escuchando en `hids-eth0`, el puerto al que el Core espeja el tráfico Norte-Sur.

```
suricata-update                                  # reglas ET Open (unas 53 mil)
cp R5/suricata/local.rules /var/lib/suricata/rules/local.rules
sed -i 's/^\( *\)- suricata.rules/&\n\1- local.rules/' /etc/suricata/suricata.yaml
sed -i 's|^\( *HOME_NET:\).*|\1 "[10.0.0.0/24]"|' /etc/suricata/suricata.yaml
suricata -T -c /etc/suricata/suricata.yaml -v    # validar
```

Arranque, desde la consola de Mininet:

```
hids suricata -c /etc/suricata/suricata.yaml -i hids-eth0 -k none -l /root/shared-host/logs &
```

`-k none` desactiva la verificación de checksums: con OVS en modo usuario los paquetes virtuales llegan con checksum inválido y Suricata los descartaría.

La alerta `Ethertype unknown` (sid 2200121, severidad 3) es el LLDP de ONOS; el integrador la descarta porque solo actúa con severidad <= 2.
