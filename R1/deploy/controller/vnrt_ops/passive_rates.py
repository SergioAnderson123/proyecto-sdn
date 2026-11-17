"""Lectura pasiva de contadores NIC, sin enviar paquetes."""
import pathlib
import socket
import time

root=pathlib.Path('/sys/class/net')
names=[p.name for p in root.iterdir() if p.name.startswith('ens') and p.name!='ens3']
def snapshot():
    return {i:tuple(int((root/i/'statistics'/k).read_text()) for k in ['rx_packets','tx_packets']) for i in names}
before=snapshot(); start=time.monotonic(); time.sleep(5); after=snapshot(); elapsed=time.monotonic()-start
rates=[max(max(after[i][j]-before[i][j],0)/elapsed for j in [0,1]) for i in names]
print(socket.gethostname(),'max_pps',round(max(rates or [0]),1),'tormenta',sum(x>=2000 for x in rates)>=2)
