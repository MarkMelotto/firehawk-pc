"""ToneMatch Editor / Librarian RPC services (ToneMatchEdit_Remote, ToneMatchLibrarian_Remote).

Out:  {cmd u32, argLen u32, args...}
In:   {type u32, len u32, body[len]}   type 1 = response (FIFO), editor 2..8 = events
Service open (L6BspMsgPort_Amplifi::open/changeMode): only the librarian port switches mode;
it writes service mode 1 (0 updater, 1 librarian, 2 device-info) and the device echoes it.
The editor port sends no mode word.
Handshake: GetVersion(1), GetSymbolTableSize(2), GetSymbolTable(3), Handshake(4, crc32(table)).
"""
from __future__ import annotations

import logging
import queue
import struct
import threading
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .stream import StreamPort

log = logging.getLogger("firehawk.svc")

# value types on the wire (FUN_00106d44)
T_INT, T_BOOL, T_FLOAT = 2, 3, 4


class SymbolTable:
    """{count, size} + count*{strOff, crc32(name), len} + strings. Commands use the *index*."""

    def __init__(self, raw: bytes):
        self.raw = raw
        n, _ = struct.unpack_from("<II", raw, 0)
        base = 8 + 12 * n
        self.names: List[str] = []
        self.index: Dict[str, int] = {}
        for i in range(n):
            off, _h, ln = struct.unpack_from("<III", raw, 8 + 12 * i)
            s = raw[base + off:base + off + ln].decode("latin1")
            self.names.append(s)
            self.index.setdefault(s, i)

    @property
    def crc(self) -> int:
        return zlib.crc32(self.raw) & 0xFFFFFFFF

    def idx(self, name: str) -> int:
        if name not in self.index:
            raise KeyError(f"symbol {name!r} not in device symbol table")
        return self.index[name]

    def name(self, i: int) -> str:
        return self.names[i] if 0 <= i < len(self.names) else f"#{i}"


def encode_value(v) -> bytes:
    if isinstance(v, bool):
        return struct.pack("<II", T_BOOL, int(v))
    if isinstance(v, int):
        return struct.pack("<Ii", T_INT, v)
    return struct.pack("<If", T_FLOAT, float(v))


def decode_value(b: bytes):
    t, = struct.unpack_from("<I", b, 0)
    if t == T_FLOAT:
        return struct.unpack_from("<f", b, 4)[0]
    if t == T_BOOL:
        return bool(struct.unpack_from("<I", b, 4)[0])
    return struct.unpack_from("<i", b, 4)[0]


@dataclass
class Event:
    kind: int          # 2 dsp, 3 patch, 4 param, 5 tap tempo, 6 fbv, 7 pskey, 8 sys setting
    body: bytes


class Service:
    MODE: "int | None" = None
    NAME = "?"

    def __init__(self, port: StreamPort):
        self.port = port
        self._pending: "queue.Queue[queue.Queue]" = queue.Queue()
        self._call_lock = threading.Lock()
        self.version = 0
        self.symbols: Optional[SymbolTable] = None
        self.on_event: Optional[Callable[[Event], None]] = None
        self._thr: Optional[threading.Thread] = None

    def open(self, timeout: float = 5.0) -> None:
        if not self.port.connect(timeout):
            raise TimeoutError(f"{self.NAME}: stream connect timed out")
        if self.MODE is not None:                               # L6BspMsgPort_Amplifi::changeMode
            self.port.write(struct.pack("<I", self.MODE))
            echo, = struct.unpack("<I", self.port.read(4, timeout))
            if echo != self.MODE:
                raise RuntimeError(f"{self.NAME}: device answered mode {echo}, wanted {self.MODE}")
        self._thr = threading.Thread(target=self._reader, daemon=True, name=f"{self.NAME}-rx")
        self._thr.start()

    def handshake(self, cached_tables: "list[bytes] | None" = None) -> None:
        self.version, = struct.unpack_from("<I", self.call(1), 0)
        for raw in cached_tables or []:                       # "choice 1/2": cached tables first
            if self._try_handshake(SymbolTable(raw)):
                return
        size, = struct.unpack_from("<I", self.call(2), 0)
        table = SymbolTable(self.call(3, timeout=30.0)[:size])
        if not self._try_handshake(table):
            raise RuntimeError(f"{self.NAME}: handshake rejected")

    def _try_handshake(self, table: SymbolTable) -> bool:
        st, = struct.unpack_from("<i", self.call(4, struct.pack("<I", table.crc)), 0)
        if st >= 0:
            self.symbols = table
        return st >= 0

    # ---- rpc ---------------------------------------------------------------
    def send(self, cmd: int, args: bytes = b"") -> "queue.Queue[bytes]":
        """Queue a request and return its reply slot without waiting (requests can be pipelined)."""
        if self.port.closed.is_set():
            raise ConnectionError(f"{self.NAME}: not connected")
        with self._call_lock:                                 # responses are FIFO
            slot: "queue.Queue[bytes]" = queue.Queue(1)
            self._pending.put(slot)
            self.port.write(struct.pack("<II", cmd, len(args)) + args)
        return slot

    def wait(self, slot: "queue.Queue[bytes]", cmd: int = 0, timeout: float = 5.0) -> bytes:
        deadline = time.monotonic() + timeout
        while True:                                           # wake up early if the link drops
            try:
                return slot.get(timeout=0.2)
            except queue.Empty:
                if self.port.closed.is_set():
                    raise ConnectionError(f"{self.NAME}: connection lost") from None
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{self.NAME}: cmd {cmd} timed out") from None

    def call(self, cmd: int, args: bytes = b"", timeout: float = 5.0) -> bytes:
        return self.wait(self.send(cmd, args), cmd, timeout)

    def _reader(self) -> None:
        while not self.port.closed.is_set():
            try:
                typ, ln = struct.unpack("<II", self.port.read(8, timeout=3600))
            except TimeoutError:
                continue
            try:
                body = self.port.read(ln, timeout=30) if ln else b""
            except TimeoutError:
                log.error("%s: stalled reading %d-byte body (type %d); stream is now out of sync", self.NAME, ln, typ)
                self.port.closed.set()
                return
            log.debug("%s IN type=%d len=%d %s", self.NAME, typ, ln, body[:256].hex(" "))
            if typ == 1:
                try:
                    self._pending.get_nowait().put(body)
                except queue.Empty:
                    log.warning("%s: unsolicited response %s", self.NAME, body.hex(" "))
            elif typ and self.on_event:
                self.on_event(Event(typ, body))


class Editor(Service):
    MODE, NAME = None, "editor"

    def set_param(self, slot: str, param: str, value) -> int:
        st = self.call(10, struct.pack("<II", self.symbols.idx(slot), self.symbols.idx(param)) + encode_value(value))
        return struct.unpack_from("<i", st, 0)[0]

    def set_param_async(self, slot: str, param: str, value) -> None:
        """SetDspModelParamAsync: same command, reply not awaited (the app does this while a slider moves)."""
        self.send(10, struct.pack("<II", self.symbols.idx(slot), self.symbols.idx(param)) + encode_value(value))

    def get_params(self, items):
        """Pipelined GetDspModelParam for [(slot, param), ...] -> [value or None]."""
        slots = []
        for slot, param in items:
            try:
                slots.append(self.send(9, struct.pack("<II", self.symbols.idx(slot), self.symbols.idx(param))))
            except KeyError:
                slots.append(None)
        out = []
        for q in slots:
            if q is None:
                out.append(None)
                continue
            r = self.wait(q, 9)
            out.append(decode_value(r[4:12]) if struct.unpack_from("<i", r, 0)[0] >= 0 else None)
        return out

    def current_models(self, slots):
        """Pipelined QueryCurrentDspModel -> [model symbol or ""]."""
        qs = []
        for slot in slots:
            try:
                qs.append(self.send(7, struct.pack("<I", self.symbols.idx(slot))))
            except KeyError:
                qs.append(None)
        out = []
        for q in qs:
            if q is None:
                out.append("")
                continue
            st, mid = struct.unpack_from("<iI", self.wait(q, 7), 0)
            out.append(self.symbols.name(mid) if st >= 0 else "")
        return out

    def get_param(self, slot: str, param: str):
        r = self.call(9, struct.pack("<II", self.symbols.idx(slot), self.symbols.idx(param)))
        st, = struct.unpack_from("<i", r, 0)
        if st < 0:
            raise RuntimeError(f"GetParam {slot}/{param} -> {st}")
        return decode_value(r[4:12])

    def load_model(self, slot: str, model: str) -> int:
        r = self.call(8, struct.pack("<II", self.symbols.idx(slot), self.symbols.idx(model)))
        return struct.unpack_from("<i", r, 0)[0]

    def current_model(self, slot: str) -> str:
        r = self.call(7, struct.pack("<I", self.symbols.idx(slot)))
        st, mid = struct.unpack_from("<iI", r, 0)
        return self.symbols.name(mid) if st >= 0 else ""

    def register_dsp_event(self, slot: str, event: str, on: bool = True) -> int:
        """cmd 0xB RegisterDspEvent / 0xC UnregisterDspEvent; events then arrive as type 2 {slot, event, value}."""
        r = self.call(0xB if on else 0xC, struct.pack("<II", self.symbols.idx(slot), self.symbols.idx(event)))
        return struct.unpack_from("<i", r, 0)[0]

    def get_pskey(self, key: int):
        """cmd 0x14 GetPresetPSKeyParam: per-preset setting such as footswitch assignments."""
        r = self.call(0x14, struct.pack("<I", key))
        st, = struct.unpack_from("<i", r, 0)
        if st < 0:
            raise RuntimeError(f"GetPresetPSKeyParam({key}) -> {st}")
        return decode_value(r[4:12])

    def set_pskey(self, key: int, value) -> int:
        """cmd 0x13 SetPresetPSKeyParam."""
        return struct.unpack_from("<i", self.call(0x13, struct.pack("<I", key) + encode_value(value)), 0)[0]

    def set_tempo(self, bpm: float) -> int:
        return struct.unpack_from("<i", self.call(0xE, struct.pack("<f", bpm)), 0)[0]

    def decode_param_event(self, ev: Event):
        """type 2/4 body: slotIdx, paramIdx, typedValue."""
        s, p = struct.unpack_from("<II", ev.body, 0)
        return self.symbols.name(s), self.symbols.name(p), decode_value(ev.body[8:16])


class Librarian(Service):
    MODE, NAME = 1, "librarian"

    def num_presets(self) -> int:
        return struct.unpack_from("<I", self.call(5), 0)[0]

    def preset_name(self, idx: int) -> str:
        r = self.call(6, struct.pack("<I", idx))
        st, ln = struct.unpack_from("<iI", r, 0)
        if st < 0:
            raise RuntimeError(f"GetPresetName({idx}) -> {st}")
        return r[8:8 + ln].split(b"\0")[0].decode("latin1")

    EDIT_BUFFER = 0xFFFFFFFF

    def get_preset_file(self, idx: int) -> bytes:
        """cmd 0xB GetPresetFile: a stored preset (0-based), or EDIT_BUFFER = the tone playing right now
        (FirmwareServiceInterfaceLibrarian::GetToneMatchPreset(0xFFFFFFFF))."""
        r = self.call(0xB, struct.pack("<I", idx & 0xFFFFFFFF), timeout=20.0)
        st, = struct.unpack_from("<i", r, 0)
        if st < 0:
            raise RuntimeError(f"GetPresetFile({idx}) -> {st}")
        return r[4:]

    def set_preset_file(self, idx: int, data: bytes) -> int:
        """cmd 0xC SetPresetFile {idx, size, data}: overwrite a stored preset (max 32 KB)."""
        if not data or len(data) > 0x8000:
            raise ValueError("preset file size")
        r = self.call(0xC, struct.pack("<II", idx & 0xFFFFFFFF, len(data)) + data, timeout=30.0)
        return struct.unpack_from("<i", r, 0)[0]

    def load_preset(self, idx: int) -> int:
        """Switch the device to hardware preset idx (0-based)."""
        return struct.unpack_from("<i", self.call(0xD, struct.pack("<I", idx)), 0)[0]
