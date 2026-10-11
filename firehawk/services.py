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
    def call(self, cmd: int, args: bytes = b"", timeout: float = 5.0) -> bytes:
        with self._call_lock:                                 # responses are FIFO
            slot: "queue.Queue[bytes]" = queue.Queue(1)
            self._pending.put(slot)
            self.port.write(struct.pack("<II", cmd, len(args)) + args)
        try:
            return slot.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"{self.NAME}: cmd {cmd} timed out") from None

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

    def load_preset(self, idx: int) -> int:
        """Switch the device to hardware preset idx (0-based)."""
        return struct.unpack_from("<i", self.call(0xD, struct.pack("<I", idx)), 0)[0]
