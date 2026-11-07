from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.cli import CLI
from mininet.topo import Topo

CORE_SWITCHES = 2
DISTRIBUTION_SWITCHES = 4
ACCESS_SWITCHES = 6

HOSTS_PER_ACCESS_SWITCH = 2

class TestTopo(Topo):
    def build(self):
        core = []
        distribution = []
        access = []

        # Core layer

        switch_index = 1

        for _ in range(1, CORE_SWITCHES + 1):
            sw = self.addSwitch(f"swc{switch_index}", protocols="OpenFlow13")
            core.append(sw)
            switch_index += 1

        # Distribution layer
        for i in range(1, DISTRIBUTION_SWITCHES + 1):
            sw = self.addSwitch(f"swd{switch_index}", protocols="OpenFlow13")
            distribution.append(sw)
            switch_index += 1

        # Access layer
        for i in range(1, ACCESS_SWITCHES + 1):
            sw = self.addSwitch(f"swa{switch_index}", protocols="OpenFlow13")
            access.append(sw)
            switch_index += 1

        for c in core:
            for d in distribution:
                self.addLink(c, d)

        for i, a in enumerate(access):
            d1 = distribution[i % len(distribution)]
            d2 = distribution[(i + 1) % len(distribution)]

            self.addLink(d1, a)

            if d2 != d1:
                self.addLink(d2, a)

        host_id = 1
        for a in access:
            for _ in range(HOSTS_PER_ACCESS_SWITCH):
                host = self.addHost(
                    f"h{host_id}"
                )

                self.addLink(a, host)
                host_id += 1

net = Mininet(
    topo=TestTopo(),
    switch=OVSSwitch,
    controller=None
)

net.addController(
    "onos",
    controller=RemoteController,
    ip="onos",
    port=6653
)

net.start()

CLI(net)

net.stop()

