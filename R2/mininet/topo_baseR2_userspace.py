"""Ejecuta topo_baseR2.py con Open vSwitch en modo usuario (datapath='user').

Docker Desktop (WSL2) no trae el modulo de kernel de OVS: con el datapath por defecto los switches se crean pero nunca
se conectan a ONOS. Este envoltorio no modifica topo_baseR2.py; solo cambia el tipo de switch que usa.
Con R5_IDS=1 (por defecto) agrega el host `hids` para el IDS de R5 en s2.
Ademas apaga el offload de checksum de los hosts (ver MininetSinOffload).
Uso (dentro del contenedor mininet):  python3 /root/shared-r2/topo_baseR2_userspace.py
En un Linux con el modulo openvswitch cargado se puede usar topo_baseR2.py directamente.
"""
import os
from functools import partial

from mininet.net import Mininet
from mininet.node import OVSSwitch

import topo_baseR2

topo_baseR2.OVSSwitch = partial(OVSSwitch, datapath='user')


class MininetSinOffload(Mininet):
    """Con el datapath en modo usuario los segmentos TCP con checksum "pendiente" (offload) se descartan en el host
    receptor: ARP y ping funcionan pero curl no. Se apaga el offload de cada interfaz al arrancar la red."""

    def start(self):
        if os.getenv("R5_IDS", "1") == "1":
            # IDS de R5: host `hids` (10.0.0.99) en s2, puerto 4 (h4=1, h5=2, enlace a s1=3). Suricata corre en su namespace.
            self.addLink(self.addHost('hids', ip='10.0.0.99/24', mac='00:00:00:00:00:99'), self.get('s2'))
        super().start()
        # LDAP asigna support01 a la MAC ...:31 en el puerto 3 de s1; topo_baseR2.py le da ...:33 a h3.
        self.get('h3').setMAC('00:00:00:00:00:31')
        for h in self.hosts:
            for intf in h.intfList():
                if intf.name != 'lo':
                    h.cmd(f'ethtool -K {intf.name} tx off rx off tso off gso off')


topo_baseR2.Mininet = MininetSinOffload

if __name__ == '__main__':
    topo_baseR2.setLogLevel('info')
    topo_baseR2.topologia_sdn()
