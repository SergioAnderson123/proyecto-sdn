from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.cli import CLI
from mininet.topo import Topo


class TreeTopo(Topo):
    def build(self):
        # Root switch
        s1 = self.addSwitch("s1", protocols="OpenFlow13")

        # Level 1
        s2 = self.addSwitch("s2", protocols="OpenFlow13")
        s3 = self.addSwitch("s3", protocols="OpenFlow13")

        # Level 2
        s4 = self.addSwitch("s4", protocols="OpenFlow13")
        s5 = self.addSwitch("s5", protocols="OpenFlow13")
        s6 = self.addSwitch("s6", protocols="OpenFlow13")
        s7 = self.addSwitch("s7", protocols="OpenFlow13")

        # Hosts
        h1 = self.addHost("h1")
        h2 = self.addHost("h2")
        h3 = self.addHost("h3")
        h4 = self.addHost("h4")

        # Tree
        self.addLink(s1, s2)
        self.addLink(s1, s3)

        self.addLink(s2, s4)
        self.addLink(s2, s5)

        self.addLink(s3, s6)
        self.addLink(s3, s7)

        # Hosts
        self.addLink(s4, h1)
        self.addLink(s5, h2)
        self.addLink(s6, h3)
        self.addLink(s7, h4)


if __name__ == "__main__":
    net = Mininet(
        topo=TreeTopo(),
        switch=OVSSwitch,
        controller=None
    )

    net.addController(
        "onos",
        controller=RemoteController,
        ip="onos",
        port=6653
    )

    setLogLevel("info")
    net.start()
    # net.pingall()
    CLI(net)
    net.stop()
