"""High-level Firehawk FX client: SPP -> RSMC -> BSP streams -> Editor/Librarian."""
from __future__ import annotations

import logging
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .rsmc import Rsmc
from .services import Editor, Event, Librarian
from .stream import Router, StreamPort
from .transport import Reader, RfcommTransport, SerialTransport, Transport

log = logging.getLogger("firehawk")

# Firehawk = "AEON" family (IsAeonDevice: product 0x200004 / 0x200006). GroupIDToSlotSymbol AEON table
# and GuitarProc '@' params (FUN_001059f4, AEON branch) from the original library.
GROUP_SLOT = {"gate": "Gate", "volume": "VolumePedal", "wah": "Wah", "fx1": "FX1", "fx2": "FX2",
              "fx3": "FX3", "fx4": "FX4", "reverb": "FX5", "amp": "Amp", "cab": "StudioCab",
              "compressor": "PostComp", "eq": "PostEQ", "looper": "Looper"}
_EN = {"cab": "CabEnable", "gate": "GateEnable", "compressor": "PostCompEnable", "eq": "PostEQEnable",
       "wah": "WahEnable", "volume": "VolumePedalEnable", "fx1": "FX1Enable", "fx2": "FX2Enable",
       "fx3": "FX3Enable", "fx4": "FX4Enable", "reverb": "FX5Enable", "looper": "LooperEnable"}
GUITARPROC = {(g, "@enabled"): v for g, v in _EN.items()}
GUITARPROC.update({("volume", "@post"): "VolumePost", ("looper", "@post"): "LooperPost",
                   ("reverb", "@post"): "FX5Post", ("reverb", "@mix"): "FX5Mix", ("reverb", "@mixtype"): "FX5MixType",
                   ("amp", "@volume"): "ChannelVolume", ("looper", "@level"): "LooperLevel"})
for _n in range(1, 5):
    GUITARPROC.update({(f"fx{_n}", "@post"): f"FX{_n}Post", (f"fx{_n}", "@mix"): f"FX{_n}Mix",
                       (f"fx{_n}", "@mixtype"): f"FX{_n}MixType", (f"fx{_n}", "@stereo"): f"FX{_n}StereoEnable"})

# cached symbol tables shipped in the APK; Firehawk 1500 matches Firebird (29577 bytes)
# cached symbol tables from an imported APK (optional; the amp sends its own table otherwise)
from .importer import TABLES, data_dir
CACHED_TABLES = [data_dir() / n for n in TABLES]


# TunerFragment.TUNER_NOTE (note 0 = A)
TUNER_NOTES = ["A", "B\u266d", "B", "C", "D\u266d", "D", "E\u266d", "E", "F", "G\u266d", "G", "A\u266d"]


def tuner_reading(v: float) -> tuple[int, float]:
    """Tuner/Note DSP value = semitones above A, 0..12 (convertA36TunerValueToNoteCents) -> (note, cents).
    note -1 = no signal."""
    v = float(v)
    if not 0.0 <= v < 12.0:
        return -1, 0.0
    n = round(v)
    return n % 12, (v - n) * 100.0


def resolve(group: str, param: str) -> tuple[str, str]:
    """App-level (group, param) -> device (slot, param), as GetDSPSlotForGroupAndParam."""
    if param.startswith("@"):
        return "GuitarProc", GUITARPROC[(group, param)]
    if group == "cab" and param == "Level":
        return "Air", param
    return GROUP_SLOT[group], param


class Firehawk:
    def __init__(self, transport: Transport):
        self.t = transport
        self.router = Router()
        self.link = Rsmc(transport.write, self.router.deliver)
        self.editor = Editor(StreamPort(self.link.send, 0x2002, 0x3ED, "editor"))
        self.librarian = Librarian(StreamPort(self.link.send, 0x2003, 0x3EF, "librarian"))
        self.router.add(self.editor.port)
        self.router.add(self.librarian.port)
        self.router.on_unrouted = lambda t, s, d, b: log.info("unrouted %x->%x t%d %s", s, d, t, b.hex(" "))
        self._reader = Reader(transport, self.link.feed)
        self._ka_stop = threading.Event()
        self.on_param: Optional[Callable[[str, str, object], None]] = None
        self.on_event: Optional[Callable[[Event], None]] = None
        self.on_patch: Optional[Callable[[int], None]] = None       # 0-based preset index
        self.on_tuner: Optional[Callable[[int, float], None]] = None        # note (-1 none), cents
        self.on_tuner_state: Optional[Callable[[bool], None]] = None       # tuner engaged (app or amp)
        self.tuner_engaged = False
        self._tuner_events = False
        self._tuner_lock = threading.Lock()

    @classmethod
    def open_port(cls, port: str) -> "Firehawk":
        return cls(RfcommTransport(port) if ":" in port else SerialTransport(port))

    def connect(self, editor: bool = True) -> None:
        self._reader.start()
        self.link.sync()
        self.link.start()
        threading.Thread(target=self._keepalive, daemon=True).start()
        cached = [p.read_bytes() for p in CACHED_TABLES if p.exists()]
        self.librarian.open()
        self.librarian.handshake(cached)
        log.info("librarian v%#x, %d symbols", self.librarian.version, len(self.librarian.symbols.names))
        if editor:
            self.editor.on_event = self._editor_event
            self.editor.open()
            self.editor.handshake(cached)
            log.info("editor v%#x", self.editor.version)
            try:                                                # A36EditorLibrarian: tuner state on connect
                if self.editor.get_param("Tuner", "Enable"):
                    self._tuner_follow(True)
            except Exception as e:  # noqa: BLE001 - no tuner slot on this firmware
                log.info("tuner state unknown: %s", e)

    def _keepalive(self) -> None:
        while not self._ka_stop.wait(0.25):
            for p in (self.editor.port, self.librarian.port):
                p.keepalive_if_idle()

    def _editor_event(self, ev: Event) -> None:
        if ev.kind in (2, 4) and len(ev.body) >= 16:
            slot, param, value = self.editor.decode_param_event(ev)
            if slot == "Tuner":
                if param == "Note" and self.on_tuner:
                    self.on_tuner(*tuner_reading(value))
                elif param == "Enable":                         # tuner footswitch on the amp (or our own echo)
                    on = float(value) > 0
                    if on != self.tuner_engaged:
                        threading.Thread(target=self._tuner_follow, args=(on,), daemon=True).start()
            elif self.on_param:
                self.on_param(slot, param, value)
        if ev.kind == 3 and self.on_patch and len(ev.body) >= 8:   # {int preset, int ?} typed values
            self.on_patch(struct.unpack_from("<i", ev.body, 4)[0])
        if self.on_event:
            self.on_event(ev)

    # ---- convenience -------------------------------------------------------
    def set(self, group: str, param: str, value) -> int:
        slot, p = resolve(group, param)
        return self.editor.set_param(slot, p, value)

    def get(self, group: str, param: str):
        slot, p = resolve(group, param)
        return self.editor.get_param(slot, p)

    def set_model(self, group: str, model_sid: str) -> int:
        """Swap the model in a block (editor cmd 8 LoadDspModel); cab ids include the mic suffix."""
        return self.editor.load_model(GROUP_SLOT[group], model_sid)

    def tuner(self, on: bool) -> int:
        """Engage/disengage the amp's tuner (engageTunerInternal: set Tuner/Enable, then (un)register Note events)."""
        st = self.editor.set_param("Tuner", "Enable", bool(on))
        if st >= 0:
            self._tuner_follow(on)
        return st

    def _tuner_follow(self, on: bool) -> None:
        with self._tuner_lock:
            if on != self._tuner_events:
                try:
                    self.editor.register_dsp_event("Tuner", "Note", on)
                    self._tuner_events = on
                except Exception as e:  # noqa: BLE001
                    log.warning("tuner events %s failed: %s", "on" if on else "off", e)
            changed = on != self.tuner_engaged
            self.tuner_engaged = on
        if changed and self.on_tuner_state:
            self.on_tuner_state(on)

    def select_preset(self, number: int) -> int:
        """number is 1-based as shown on the unit."""
        return self.librarian.load_preset(number - 1)

    def preset_names(self) -> list[str]:
        return [self.librarian.preset_name(i) for i in range(self.librarian.num_presets())]

    def close(self) -> None:
        self._ka_stop.set()
        for p in (self.editor.port, self.librarian.port):
            p.close()
        time.sleep(0.3)
        self.link.stop()
        self._reader.stop()
        self.t.close()
