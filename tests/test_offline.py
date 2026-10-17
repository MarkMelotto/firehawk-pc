"""Amp off / link lost: connect fails fast, close never hangs, a dropped link is reported."""
import threading, time
from firehawk import rsmc
rsmc.TICK_S = 0.02
import firehawk.device as fd
from firehawk.device import Firehawk
from tests.sim_device import SimDevice
from tests.fake_table import build as build_table

fd.CACHED_TABLES = []
fd.ANSWER_S = 1.5


class Silent:                                  # port opens, amp never answers
    def __init__(self): self.gone = threading.Event()
    def write(self, b): pass
    def read(self, n=256): self.gone.wait(); return b""
    def close(self): self.gone.set()


class Broken(Silent):                          # write times out (BT port with nothing behind it)
    def write(self, b): raise ConnectionError("write timeout")


class Pipe(Silent):
    def __init__(self): super().__init__(); self.peer = None
    def write(self, b):
        if self.gone.is_set(): raise ConnectionError("gone")
        self.peer(b)


def fails_fast(t):
    fh = Firehawk(t)
    t0 = time.monotonic()
    try:
        fh.connect()
        raise AssertionError("connected to nothing")
    except ConnectionError:
        pass
    took = time.monotonic() - t0
    t1 = time.monotonic(); fh.close(); closing = time.monotonic() - t1
    assert took < 4 and closing < 1.5, (took, closing)
    return took


def main():
    print("silent amp: failed after %.1f s" % fails_fast(Silent()))
    print("broken port: failed after %.1f s" % fails_fast(Broken()))

    dev = SimDevice(build_table()); t = Pipe(); fh = Firehawk(t)
    t.peer = dev.feed
    dev.to_host = lambda b: None if t.gone.is_set() else threading.Thread(target=fh.link.feed, args=(b,)).start()
    dev.link.start()
    lost = []
    fh.on_disconnect = lost.append
    fh.connect()
    assert fh.preset_names() == ["Clean", "Crunch", "Lead"]
    t.close()                                  # amp switched off: reader ends
    time.sleep(0.3)
    assert lost, "link loss not reported"
    t0 = time.monotonic()
    try:
        fh.librarian.num_presets()
        raise AssertionError("call on a dead link succeeded")
    except ConnectionError:
        pass
    assert time.monotonic() - t0 < 1
    t1 = time.monotonic(); fh.close(); assert time.monotonic() - t1 < 1.5
    print("link loss reported:", lost[0])
    print("offline OK")


if __name__ == "__main__":
    main()
