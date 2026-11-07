from mininet.net import Mininet
from mininet.node import OVSSwitch, Controller, RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel

def topologia_sdn():
    # Instanciar la red, definiendo que usaremos un controlador remoto (ONOS)
    net = Mininet(switch=OVSSwitch, controller=RemoteController)

    print("*** Agregando el Controlador ***")
    # Para la primera prueba, Mininet levantará un controlador local por defecto
    # Más adelante, aquí pondremos la IP de ONOS
    c0 = net.addController(
        "onos",
        controller=RemoteController,
        ip="onos",
        port=6653
    )


    print("*** Agregando el Switch de Borde (M01) ***")
    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    print("*** Agregando los Hosts ***")
    # h1 representará al endpoint del alumno
    h1 = net.addHost('h1', ip='10.0.0.10/24', mac='00:00:00:00:00:11')
    
    # h2 representará al servidor del Portal Cautivo
    h2 = net.addHost('h2', ip='10.0.0.20/24', mac='00:00:00:00:00:22')

    print("*** Creando los Enlaces ***")
    net.addLink(h1, s1)
    net.addLink(h2, s1)

    print("*** Iniciando la Red ***")
    net.start()
    
    print("*** Abriendo la consola de Mininet ***")
    CLI(net)

    print("*** Deteniendo la Red ***")
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    topologia_sdn()
