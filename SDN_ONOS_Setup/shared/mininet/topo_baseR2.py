from mininet.net import Mininet
from mininet.node import OVSSwitch, Controller, RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel

def topologia_sdn():
    net = Mininet(switch=OVSSwitch, controller=RemoteController)

    c0 = net.addController(
        "onos",
        controller=RemoteController,
        ip="onos",
        port=6653
    )

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    h1 = net.addHost('h1', ip='10.0.0.10/24', mac='00:00:00:00:00:11') # Alumno
    h2 = net.addHost('h2', ip='10.0.0.20/24', mac='00:00:00:00:00:22') # Alumno
    h3 = net.addHost('h3', ip='10.0.0.30/24', mac='00:00:00:00:00:33') # Profesor
    h4 = net.addHost('h4', ip='10.0.0.40/24', mac='00:00:00:00:00:44') # Servicio de Alumnos
    h5 = net.addHost('h5', ip='10.0.0.50/24', mac='00:00:00:00:00:55') # Servicio de Profesor

    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)

    net.addLink(h4, s2)
    net.addLink(h5, s2)

    net.addLink(s1, s2)


    net.start()
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    topologia_sdn()
