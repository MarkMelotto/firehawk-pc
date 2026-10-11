"""Run the GUI against the simulated amp (no hardware). python -m tests.preview_gui"""
import random, struct, sys, threading, time
sys.path.insert(0, ".")
from firehawk import rsmc
rsmc.TICK_S = 0.02
from firehawk import gui
from firehawk.device import Firehawk
from tests.sim_device import SimDevice
from tests.fake_table import build as build_table

class Pipe:
    def __init__(self): self.peer = None
    def write(self, b): self.peer(b)
    def read(self, n=256): time.sleep(3600)
    def close(self): pass

class DemoApp(gui.App):
    def _open(self, target):
        dev = SimDevice(build_table())
        t = Pipe(); fh = Firehawk(t)
        t.peer = dev.feed
        dev.to_host = lambda b: threading.Thread(target=fh.link.feed, args=(b,)).start()
        rnd = random.Random(3)
        orig = dev._rpc
        def rpc(mode, cmd, a, src, dst):
            if mode == 1 and cmd == 5: return struct.pack("<I", 128)
            if mode == 1 and cmd == 6:
                i, = struct.unpack("<I", a); names = ["Plexi Lead 100 N", "Solo 100 OD", "Blkface Dbl Vibr", "Class A-30 TB",
                    "Tube Bass Preamp", "Acoustic Nylon", "Locked Outta Hvn", "7 Nation Army", "Move Like Jagger", "Rammstein"]
                n = names[i % len(names)].encode(); return struct.pack("<iI", 0, len(n) + 1) + n + b"\0"
            if mode == "editor" and cmd == 9:
                s, p = struct.unpack("<II", a)
                if (s, p) not in dev.params: dev.params[(s, p)] = (4, struct.unpack("<I", struct.pack("<f", rnd.random()))[0])
            return orig(mode, cmd, a, src, dst)
        dev._rpc = rpc
        dev.link.start()
        self.dev = dev
        return fh

if __name__ == "__main__":
    app = DemoApp()
    app.port.set("DEMO")
    if "--auto" in sys.argv:
        app.after(500, app.connect)
    if "--picker" in sys.argv:          # screenshot helper: open the model picker on FX2
        app.after(9000, lambda: (app.select(next(b for b in app.blocks if b.group == "fx2")),
                                 app.after(1500, app.open_picker)))
    if "--tuner" in sys.argv:           # screenshot helper: tuner window with a slightly flat E
        app.after(7000, app.tuner_on)
        app.after(8500, lambda: app.dev.tuner_note(6.93))
    app.mainloop()
