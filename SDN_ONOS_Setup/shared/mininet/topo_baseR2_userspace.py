"""Ejecuta topo_baseR2.py con Open vSwitch en modo usuario (datapath='user').

Docker Desktop (WSL2) no trae el modulo de kernel de OVS: con el datapath por defecto los switches se crean pero nunca
se conectan a ONOS. Este envoltorio no modifica topo_baseR2.py; solo cambia el tipo de switch que usa.
Ademas apaga el offload de checksum de los hosts (ver MininetSinOffload).
Uso (dentro del contenedor mininet):  python3 /root/shared-host/topo_baseR2_userspace.py
En un Linux con el modulo openvswitch cargado se puede usar topo_baseR2.py directamente.
"""
from functools import partial

from mininet.net import Mininet
from mininet.node import OVSSwitch

import topo_baseR2

topo_baseR2.OVSSwitch = partial(OVSSwitch, datapath='user')


class MininetSinOffload(Mininet):
    """Con el datapath en modo usuario los segmentos TCP con checksum "pendiente" (offload) se descartan en el host
    receptor: ARP y ping funcionan pero curl no. Se apaga el offload de cada interfaz al arrancar la red."""

    def start(self):
        super().start()
        for h in self.hosts:
            for intf in h.intfList():
                if intf.name != 'lo':
                    h.cmd(f'ethtool -K {intf.name} tx off rx off tso off gso off')


topo_baseR2.Mininet = MininetSinOffload

if __name__ == '__main__':
    topo_baseR2.setLogLevel('info')
    topo_baseR2.topologia_sdn()
