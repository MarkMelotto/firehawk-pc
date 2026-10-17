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


def preset_label(idx: int) -> str:
    """0-based index -> bank + letter as on the amp: 0 -> '1A', 3 -> '1D', 127 -> '32D'."""
    return f"{idx // 4 + 1}{'ABCD'[idx % 4]}"


def parse_preset(text: str) -> int:
    """'12C' (bank + letter) or '46' (1-based number) -> 0-based index."""
    t = text.strip().upper()
    if t in ("EDIT", "CURRENT", "NOW"):                     # the tone playing right now (edit buffer)
        return 0xFFFFFFFF
    if t and t[-1] in "ABCD" and t[:-1].isdigit():
        return (int(t[:-1]) - 1) * 4 + "ABCD".index(t[-1])
    return int(t) - 1


ANSWER_S = 6.0          # the amp answers the IAP sync within a second when it's there

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


# FBV footswitches FS1..FS5 (FootSwitchView.FootSwitchSlots); PS keys per switch i (0-based):
# 10+3i effect type, 11+3i the block's GuitarProc enable symbol, 12+3i its on/off state
# (FirmwareServiceInterfaceEditor::SetFootswitchAssign)
FOOTSWITCHES = 5
ENABLE_GROUP = {v: k for k, v in _EN.items()}


def effect_type(category: int, model_sid: str = "") -> int:
    """ModelIDToEffectType: model category -> footswitch effect type."""
    if category == 4 and model_sid == "SharcPodFixedComp":
        return 10
    return {2: 1, 3: 9, 4: 9, 5: 7, 6: 7, 7: 7, 8: 4, 9: 5, 10: 3, 12: 8, 14: 9}.get(category, 0)


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
        self.link = Rsmc(transport.write, self.router.deliver, on_error=lambda e: self._lost(str(e)))
        self.editor = Editor(StreamPort(self.link.send, 0x2002, 0x3ED, "editor"))
        self.librarian = Librarian(StreamPort(self.link.send, 0x2003, 0x3EF, "librarian"))
        self.router.add(self.editor.port)
        self.router.add(self.librarian.port)
        self.router.on_unrouted = lambda t, s, d, b: log.info("unrouted %x->%x t%d %s", s, d, t, b.hex(" "))
        self._reader = Reader(transport, self.link.feed, on_close=lambda: self._lost("Bluetooth link closed"))
        self.on_disconnect: Optional[Callable[[str], None]] = None      # link lost after connecting
        self._closing = False
        self._lost_once = threading.Event()
        self._ka_stop = threading.Event()
        self.on_param: Optional[Callable[[str, str, object], None]] = None
        self.on_event: Optional[Callable[[Event], None]] = None
        self.on_patch: Optional[Callable[[int], None]] = None       # 0-based preset index
        self.on_tuner: Optional[Callable[[int, float], None]] = None        # note (-1 none), cents
        self.on_tuner_state: Optional[Callable[[bool], None]] = None       # tuner engaged (app or amp)
        self.tuner_engaged = False
        self.current_preset: Optional[int] = None          # 0-based, from preset events / select_preset
        self._patch_events = 0
        self.edit_buffer: Optional[bytes] = None
        self._tuner_events = False
        self._tuner_lock = threading.Lock()

    @classmethod
    def open_port(cls, port: str) -> "Firehawk":
        return cls(RfcommTransport(port) if ":" in port else SerialTransport(port))

    def connect(self, editor: bool = True) -> None:
        self._reader.start()
        self.link.sync()
        self.link.start()
        deadline = time.monotonic() + ANSWER_S          # fail fast instead of waiting on every timeout
        while self.link.stats["rx"] == 0:
            if self.link.dead or time.monotonic() > deadline:
                raise ConnectionError("The amp doesn't answer. Is it switched on and paired, is this its Outgoing "
                                      "COM port, and is the Firehawk app on your phone closed?")
            time.sleep(0.05)
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
        if ev.kind == 3 and len(ev.body) >= 8:                      # {int preset, int ?} typed values
            self.current_preset = struct.unpack_from("<i", ev.body, 4)[0]
            self._patch_events += 1
            # the original app reads the new edit buffer after every preset change
            # (A36EditBufferContext::syncWithDeviceAsMaster -> GetPresetFile(0xFFFFFFFF)); do the same
            threading.Thread(target=self._fetch_edit_buffer, daemon=True, name="sync-edit-buffer").start()
            if self.on_patch:
                self.on_patch(self.current_preset)
        if self.on_event:
            self.on_event(ev)

    # ---- convenience -------------------------------------------------------
    def set(self, group: str, param: str, value) -> int:
        slot, p = resolve(group, param)
        return self.editor.set_param(slot, p, value)

    def set_async(self, group: str, param: str, value) -> None:
        slot, p = resolve(group, param)
        self.editor.set_param_async(slot, p, value)

    def get_many(self, items):
        """[(group, param), ...] -> [value or None], one round trip."""
        res = []
        for g, p in items:
            try:
                res.append(resolve(g, p))
            except KeyError:
                res.append(("", ""))
        vals = self.editor.get_params([r for r in res if r[0]])
        it = iter(vals)
        return [next(it) if r[0] else None for r in res]

    def get(self, group: str, param: str):
        slot, p = resolve(group, param)
        return self.editor.get_param(slot, p)

    def set_model(self, group: str, model_sid: str) -> int:
        """Swap the model in a block (editor cmd 8 LoadDspModel); cab ids include the mic suffix."""
        return self.editor.load_model(GROUP_SLOT[group], model_sid)

    def _fetch_edit_buffer(self) -> None:
        try:
            self.edit_buffer = self.librarian.get_preset_file(self.librarian.EDIT_BUFFER)
            log.info("edit buffer synced (%d bytes) after preset change", len(self.edit_buffer))
        except Exception as e:  # noqa: BLE001 - informational
            log.warning("edit buffer sync failed: %s", e)

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

    def footswitches(self) -> list:
        """Block group on FS1..FS5 for the current tone (None = unassigned)."""
        qs = [self.editor.send(0x14, struct.pack("<I", 11 + 3 * i)) for i in range(FOOTSWITCHES)]
        out = []
        for q in qs:
            r = self.editor.wait(q, 0x14)
            st, typ, val = struct.unpack_from("<iIi", r, 0)
            out.append(ENABLE_GROUP.get(self.editor.symbols.name(val)) if st >= 0 and val >= 0 else None)
        return out

    def assign_footswitch(self, fs: int, group: Optional[str], category: int = 0, model_sid: str = "",
                          enabled: bool = True) -> int:
        """Put a block on footswitch fs (0-based) for this tone, or clear it (group None)."""
        if group is None:
            sym, etype, enabled = -1, 0, False
        else:
            sym, etype = self.editor.symbols.idx(_EN[group]), effect_type(category, model_sid)
        for key, value in ((11 + 3 * fs, sym), (10 + 3 * fs, etype), (12 + 3 * fs, bool(enabled))):
            st = self.editor.set_pskey(key, value)
            if st < 0:
                return st
        return 0

    def select_preset(self, number: int) -> int:
        """number is 1-based as shown on the unit."""
        st = self.librarian.load_preset(number - 1)
        if st >= 0:
            self.current_preset = number - 1
        return st

    def save_preset(self, target: int, name: Optional[str] = None, catalog=None,
                    progress: Optional[Callable[[str], None]] = None) -> list[str]:
        """Store the amp's current tone in preset `target` (0-based), optionally renamed.

        The amp serialises its own edit buffer (GetPresetFile(0xFFFFFFFF)); we rename it, back up the preset
        that gets overwritten, upload with SetPresetFile and read it back. Aborts without writing if the amp
        switches preset meanwhile. Returns a short list of what was done."""
        from . import preset as pf
        from .importer import data_dir
        say = progress or (lambda m: None)
        switches = self._patch_events
        say("Reading the current tone from the amp…")
        # Only ever upload what the amp serialised itself: a preset rebuilt from editor values (the old
        # _patched_copy) was accepted but made a Firehawk 1500 hang when that preset was loaded.
        try:
            data = self.librarian.get_preset_file(self.librarian.EDIT_BUFFER)
            entries = pf.parse(data)
        except (RuntimeError, ValueError) as e:
            raise RuntimeError(f"The amp didn't hand over the current tone ({e}); nothing was saved.") from e
        how = "edit buffer"
        if pf.serialize(entries) != pf.serialize(pf.parse(pf.serialize(entries))):
            raise RuntimeError("Unknown preset format on this amp; nothing was saved.")
        if self._patch_events != switches:
            raise RuntimeError("The amp switched presets while saving; nothing was saved. Try again.")
        old = pf.preset_name(entries)
        if name is not None and name != old:
            pf.set_name(entries, name)
        out = pf.serialize(entries)

        say("Backing up the preset that gets overwritten…")
        bdir = data_dir() / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            (bdir / f"preset-{preset_label(target)}-{stamp}.bin").write_bytes(self.librarian.get_preset_file(target))
        except RuntimeError as e:                      # empty slot / unreadable: nothing to back up
            log.warning("no backup of %s: %s", preset_label(target), e)
        if self._patch_events != switches:
            raise RuntimeError("The amp switched presets while saving; nothing was saved. Try again.")

        say(f"Writing preset {preset_label(target)}…")
        log.info("save %s -> preset %s (%d bytes), name %r -> %r", how, preset_label(target), len(out), old, name)
        st = self.librarian.set_preset_file(target, out)
        if st < 0:
            raise RuntimeError(f"The amp refused the preset (error {st}); nothing was changed.")
        say("Checking…")
        if self.librarian.get_preset_file(target) != out:
            log.warning("read-back of preset %s differs from what was written", preset_label(target))
        # like the phone app (saveCurrentToHardware -> loadHardwarePreset -> loadPresetFromDevice): load the
        # saved preset back into the edit buffer. Without this a Firehawk 1500 froze on the next preset switch.
        say("Loading the saved preset…")
        st = self.librarian.set_preset_file(self.librarian.EDIT_BUFFER, out)
        if st < 0:
            log.warning("reloading the edit buffer after saving failed (%d)", st)
        self.current_preset = target
        return [f"{how} -> {preset_label(target)}", f"name: {old!r} -> {name or old!r}"]

    def _patched_copy(self, target: int, catalog, say) -> list:
        """Fallback: stored file of the source preset with every model/value replaced by live editor values."""
        from . import preset as pf
        src = self.current_preset if self.current_preset is not None and self.current_preset >= 0 else target
        say("Reading the stored preset…")
        orig = self.librarian.get_preset_file(src)
        entries = pf.parse(orig)
        if pf.serialize(entries) != orig:
            raise RuntimeError("Unknown preset format on this amp; nothing was saved.")
        say("Reading the current tone from the amp…")
        patcher = pf.Patcher(self.editor.symbols, self.editor.current_model, self.editor.get_param,
                             catalog.device_params if catalog else None, catalog.cab_link if catalog else None)
        out = patcher.apply(entries)
        log.info("patched copy of %s, changes:\n  %s", preset_label(src), "\n  ".join(patcher.changes) or "(none)")
        return out

    def preset_names(self) -> list[str]:
        return [self.librarian.preset_name(i) for i in range(self.librarian.num_presets())]

    def _lost(self, why: str) -> None:
        if self._closing or self._lost_once.is_set():
            return
        self._lost_once.set()
        log.warning("connection lost: %s", why)
        self.link.dead = True
        for p in (self.editor.port, self.librarian.port):
            p.closed.set()                              # wake anything waiting on a reply
            with p._cond:
                p._cond.notify_all()
        if self.on_disconnect:
            self.on_disconnect(why)

    def close(self) -> None:
        """Never blocks for long, also when the amp is gone."""
        self._closing = True
        self._ka_stop.set()
        if not self.link.dead:
            for p in (self.editor.port, self.librarian.port):
                try:
                    p.close()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.3)
        self.link.stop()
        self._reader.stop()
        self.t.close()
