import struct, sys, time, threading, logging
sys.path.insert(0, '.')
from firehawk import rsmc
rsmc.TICK_S = 0.02                 # speed up IAP sync for the test
from firehawk.device import Firehawk
from firehawk.rsmc import crc16, build_frame
from tests.sim_device import SimDevice
from tests.fake_table import build as build_table

# 1) framing matches bytes captured from a real Firehawk session (S25 port constants)
CAPTURED_SYN = bytes.fromhex("55 55 00 01 00 08 14 00 b1 bd ce a7 0c 00 00 28 03 20 ef 03 00 00 e4 00 00 01 00 21 00 10 00 00")
CAPTURED_MODE = bytes.fromhex("55 55 00 02 01 09 14 00 0c 22 67 e9 0c 00 00 18 03 20 ef 03 00 02 00 04 00 10 00 00 02 00 00 00")
CAPTURED_ACK = bytes.fromhex("55 55 00 00 00 08 00 00 00 00 03 6b")
assert build_frame(0, 0) == CAPTURED_ACK

class Pipe:
    def __init__(self): self.peer = None
    def write(self, b): self.peer(b)
    def read(self, n=256): time.sleep(3600)
    def close(self): pass

# synthetic ~27 KB table, nothing cached -> full download through stream credit
import random
table = build_table()
import firehawk.device as fd
fd.CACHED_TABLES = []   # no cached table -> full download through stream credit
dev = SimDevice(table)
host_t = Pipe()
fh = Firehawk(host_t)
host_t.peer = lambda b: dev.feed(b)
dev.to_host = lambda b: threading.Thread(target=fh.link.feed, args=(b,)).start()
dev.link.start()

params = []
patches = []
fh.on_param = lambda *a: params.append(a)
fh.on_patch = patches.append
fh.connect()
frames = [f for f in dev.raw_frames if f[:2] == b'UU' and len(f) > 12]
assert frames[0] == CAPTURED_SYN, frames[0].hex(' ')
print('SYN frame identical to captured device session')
# real Firehawk 1500 log: librarian selects service mode 1 (the S25 shim used 2 = DEVICE_INFO)
assert frames[1][12:-4] == CAPTURED_MODE[12:-4] and frames[1][-4:] == bytes([1, 0, 0, 0]), frames[1].hex(' ')
print('mode-select frame layout matches captured session (mode 1)')

print('symbol table downloaded:', len(fh.librarian.symbols.raw), 'bytes')
print('presets:', fh.preset_names())
assert fh.set('amp', 'Drive', 0.75) == 0
print('get amp/Drive ->', round(fh.get('amp', 'Drive'), 3))
assert fh.set('fx1', '@enabled', True) == 0
assert fh.set('reverb', '@enabled', False) == 0  # AEON: reverb -> FX5Enable
assert fh.set_model('fx2', 'RezSynth') == 0 and fh.editor.current_model('FX2') == 'RezSynth'
assert fh.set_model('cab', '2x12BlackfaceStudio57OnAxis') == 0
print('model change OK:', fh.editor.current_model('FX2'), '/', fh.editor.current_model('StudioCab'))
assert fh.select_preset(3) == 0 and dev.preset == 2
time.sleep(0.2)
assert patches == [2], patches
print('patch event -> preset index', patches)
print('param events:', [(s, p, round(v, 3) if isinstance(v, float) else v) for s, p, v in params])
# tuner: engage from the app, readings arrive as Tuner/Note DSP events, footswitch disengages
readings, states = [], []
fh.on_tuner = lambda n, c: readings.append((n, round(c, 1)))
fh.on_tuner_state = states.append
assert fh.tuner(True) == 0 and fh.tuner_engaged
dev.tuner_note(7.12)          # E, 12 cents sharp
dev.tuner_note(11.9)          # A, 10 cents flat
dev.tuner_note(-1.0)          # no signal
time.sleep(0.2)
assert readings == [(7, 12.0), (0, -10.0), (-1, 0.0)], readings
dev.footswitch('Tuner', 'Enable', False)
time.sleep(0.3)
assert not fh.tuner_engaged and states == [True, False], states
dev.tuner_note(3.0)
time.sleep(0.1)
assert len(readings) == 3, 'tuner events after unregister'
dev.footswitch('Tuner', 'Enable', True)       # tuner switched on at the amp -> app follows
time.sleep(0.3)
assert fh.tuner_engaged and states[-1] is True
dev.tuner_note(3.0)
time.sleep(0.1)
assert readings[-1] == (3, 0.0), readings
assert not any(s == 'Tuner' for s, _p, _v in params), 'tuner events leaked into on_param'
print('tuner OK:', readings, states)
# save preset: stored file of the loaded preset, patched with the live edit buffer, written to another slot
import tempfile
from pathlib import Path
from firehawk import preset as pf
import firehawk.importer as imp
S = fh.editor.symbols.index
def tone(name):
    E = pf.entry
    return pf.serialize([
        E(pf.SECTION, pf.T_INT, 2), E(pf.SECTION_VER, pf.T_INT, 7), E(pf.NAME_KEY, pf.T_STR, name),
        pf.Entry(0x19, pf.T_ARRAY, struct.pack('<I', 200) + bytes(800)),          # kept as-is; > 512-byte window
        E(pf.SECTION_END, pf.T_NONE, 0),
        E(pf.SECTION, pf.T_INT, 1), E(pf.SECTION_VER, pf.T_INT, 3),
        E(pf.SLOT, pf.T_SYM, S['GuitarProc']), E(pf.ASSET, pf.T_SYM, S['GuitarProc']), E(pf.COUNT, pf.T_INT, 2),
        E(S['FX1EnableState'], pf.T_BOOL, False), E(S['ChannelVolumeState'], pf.T_FLOAT, 0.5),
        E(pf.SLOT, pf.T_SYM, S['Amp']), E(pf.ASSET, pf.T_SYM, S['HD_AmpBlackfaceDoubleVib']), E(pf.COUNT, pf.T_INT, 2),
        E(S['DriveState'], pf.T_FLOAT, 0.2), E(S['TrebleState'], pf.T_FLOAT, 0.3), E(pf.SLOT_END, pf.T_NONE, 0),
        E(pf.SLOT, pf.T_SYM, S['FX2']), E(pf.ASSET, pf.T_SYM, S['AnalogChorus']), E(pf.COUNT, pf.T_INT, 1),
        E(S['DepthState'], pf.T_FLOAT, 0.1), E(pf.SLOT_END, pf.T_NONE, 0),
        E(pf.SLOT_END, pf.T_NONE, 0), E(pf.SECTION_END, pf.T_NONE, 0)])
dev.preset_files = {0: tone('Clean'), 2: tone('Lead')}
assert pf.serialize(pf.parse(dev.preset_files[0])) == dev.preset_files[0]
class Cat:
    device_params = staticmethod(lambda m: [('Speed', 1, 0.5), ('@enabled', 2, True)] if m == 'RezSynth' else [])
    cab_link = staticmethod(lambda c: '')
tmpd = Path(tempfile.mkdtemp()); imp.data_dir = lambda: tmpd
fh.current_preset = 0
steps = []
changes = fh.save_preset(2, 'My Tone', Cat(), steps.append)
saved = pf.parse(dev.preset_files[2])
assert pf.preset_name(saved) == 'My Tone', pf.preset_name(saved)
vals = {fh.editor.symbols.name(e.key): e.value for e in saved if e.key < 0x80000000}
assert abs(vals['DriveState'] - 0.75) < 1e-6 and vals['FX1EnableState'] is True, vals
assert 'SpeedState' in vals and 'DepthState' not in vals                     # FX2 rebuilt for RezSynth
models = [fh.editor.symbols.name(e.u32) for e in saved if e.key == pf.ASSET]
assert models == ['GuitarProc', 'HD_AmpBlackfaceDoubleVib', 'RezSynth'], models
assert pf.serialize(pf.parse(dev.preset_files[0])) == tone('Clean')         # source untouched
assert list((tmpd / 'backups').iterdir()), 'no backup of the overwritten preset'
assert any(e.key == 0x19 and len(e.raw) == 804 for e in saved)
print('save preset OK:', changes)
time.sleep(1.3)
assert any(k[0] == 'keep' for k in dev.log), 'no keepalive sent'
print('keepalives OK; link stats host', fh.link.stats, 'dev', dev.link.stats)
fh._ka_stop.set()
print('ALL OK')
