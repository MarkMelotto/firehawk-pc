# RSMC under packet loss: drop ~15% of frames both ways, all RPCs must still succeed
import random, sys, threading, time
sys.path.insert(0, '.')
from firehawk import rsmc
rsmc.TICK_S = 0.02; rsmc.RETX_S = 0.1
from firehawk.device import Firehawk
from tests.sim_device import SimDevice
random.seed(1)
class Pipe:
    def __init__(self): self.peer=None
    def write(self,b): self.peer(b)
    def read(self,n=256): time.sleep(3600)
    def close(self): pass
dev=SimDevice(open('firehawk/defaultSymbolTable_Firebird.bin','rb').read())
t=Pipe(); fh=Firehawk(t)
lossy=lambda f: (lambda b: None if (b[:2]==b'UU' and random.random()<0.15) else f(b))
t.peer=lossy(dev.feed)
dev.to_host=lossy(lambda b: threading.Thread(target=fh.link.feed,args=(b,)).start())
dev.link.start(); fh.connect()
for i in range(20):
    assert fh.set('amp','Bass',i/20)==0
    assert abs(fh.get('amp','Bass')-i/20)<1e-6
print('lossy OK', 'retx host', fh.link.stats['retx'], 'dev', dev.link.stats['retx'])
fh._ka_stop.set()
