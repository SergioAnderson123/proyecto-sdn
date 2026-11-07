# Switches OVS con Ansible

Equivale a los `ovs-vsctl` manuales de `R1/deploy/README.md`, para sw1..sw3.

```bash
sudo apt-get install -y ansible          # en la maquina desde la que lanzas el playbook (Controller o tu PC)
cp inventory.ini.example inventory.ini   # ajustar IP/puertos, bridge y controller_ip
ansible -i inventory.ini switches -m ping
ansible-playbook -i inventory.ini switches.yml --ask-become-pass   # --ask-become-pass si sudo pide contraseña
```

Requiere que el Controller ya este arriba (`R1/deploy/controller/up.sh`). Al final cada switch debe aparecer en `GET /onos/v1/devices`; anota el deviceId del Core para `R5/deploy/.env` (`CORE_DEVICE`).
