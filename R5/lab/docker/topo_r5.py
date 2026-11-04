"""Topología del laboratorio R5 (Mininet + OVS en modo usuario).

            hids (10.0.0.99, Suricata)
               |p4
  hsrv --- s2 -- s1 (CORE) -- s3 --- hint        s1: p1=s2 p2=s3 p3=sx p4=hids
  10.0.0.10              |p3                      hint 10.0.0.21 = host interno (para el caso C2)
                        sx (switch "Internet", sin controlador)
        hext1..4 (10.0.1.11-14)  hnat (10.0.1.100)  hc2 (10.0.1.50)

HOME_NET = 10.0.0.0/24. Los hosts usan /16 para alcanzarse sin router (laboratorio).
Escribe /tmp/topo.ready cuando todo está listo y espera.
"""
import time
from functools import partial

from mininet.link import Link
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

setLogLevel("info")
net = Mininet(switch=partial(OVSSwitch, protocols="OpenFlow13", datapath="user"),
              controller=None, link=Link, autoSetMacs=True)
net.addController("onos", controller=RemoteController, ip="onos", port=6653)

s1 = net.addSwitch("s1", dpid="0000000000000001")   # Core
s2 = net.addSwitch("s2", dpid="0000000000000002")   # borde del servidor
s3 = net.addSwitch("s3", dpid="0000000000000003")   # borde del host interno
sx = net.addSwitch("sx", dpid="00000000000000f1", failMode="standalone")   # "Internet"

hids = net.addHost("hids", ip="10.0.0.99/16")
hsrv = net.addHost("hsrv", ip="10.0.0.10/16")
hint = net.addHost("hint", ip="10.0.0.21/16")
externos = [net.addHost(f"hext{i}", ip=f"10.0.1.{10 + i}/16") for i in range(1, 5)]
hnat = net.addHost("hnat", ip="10.0.1.100/16")
hc2 = net.addHost("hc2", ip="10.0.1.50/16")

net.addLink(s1, s2)     # s1 p1
net.addLink(s1, s3)     # s1 p2
net.addLink(s1, sx)     # s1 p3 (uplink)
net.addLink(s1, hids)   # s1 p4 (puerto del IDS)
net.addLink(s2, hsrv)
net.addLink(s3, hint)
for h in externos + [hnat, hc2]:
    net.addLink(sx, h)

net.start()
sx.cmd("ovs-vsctl del-controller sx")      # el "Internet" es un switch L2 normal
time.sleep(4)

# Con OVS en modo usuario los checksums por offload llegan inválidos y el kernel del host receptor
# descarta los segmentos TCP (el ping funciona, curl no): se desactiva el offload en cada host.
for h in net.hosts:
    for i in h.intfNames():
        h.cmd(f"ethtool -K {i} tx off rx off")

# Servicios de prueba
hsrv.cmd("python3 -m http.server 80 >/tmp/hsrv-http.log 2>&1 &")
hc2.cmd("python3 -m http.server 8080 >/tmp/hc2-http.log 2>&1 &")

print("*** pingAll para que ONOS aprenda los hosts")
net.pingAll(timeout="1")
open("/tmp/topo.ready", "w").write("ok")
while True:
    time.sleep(3600)
