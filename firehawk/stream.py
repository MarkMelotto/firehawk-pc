"""BSP message transport + MsgPort_Stream (a tiny TCP-like stream per service).

Reconstructed from MsgTransportHeader_Pack/Unpack, MsgPort_Stream_Connect and the
stream tx/rx tasks (FUN_0013128c / FUN_001314ec) of the original library.

Transport header (8 bytes LE):
  w0 = type<<28 | 2<<26 | len      type 1 = data, 2 = connect(SYN); len excludes these 8 bytes
  w1 = srcPort | dstPort<<16
  (type 2 only) w = mtu<<16
Stream header (8 bytes LE):
  wA = flags<<24 | seq<<8          flags: 0x01 SYN, 0x02 FIN, 0x04 DATA, 0x08 WND, 0x10 KEEPALIVE, 0x20 HB-enabled
  wB = credit                      cumulative bytes the peer may send us
Receiver aborts unless every non-SYN packet has seq == last+1.
"""
from __future__ import annotations

import logging
import queue
import struct
import threading
import time
from typing import Callable, Dict, Optional

log = logging.getLogger("firehawk.stream")

F_SYN, F_FIN, F_DATA, F_WND, F_KEEP, F_HB = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
RX_WINDOW = 0x1000          # McBspMessaging_CreateStreamPort(0x1000, 0x1000)
MTU = 0xE4                  # 0xF0 max transfer - 12 byte RSMC header
KEEPALIVE_S = 1.0           # peer aborts after 4 s of silence


def transport_header(ptype: int, length: int, src: int, dst: int) -> bytes:
    return struct.pack("<II", (ptype << 28) | (2 << 26) | (length & 0xFFFF), (src & 0xFFFF) | (dst << 16))


class StreamPort:
    def __init__(self, link_send: Callable[[bytes], None], local: int, remote: int, name: str):
        self._send = link_send
        self.local, self.remote, self.name = local, remote, name
        self._lock = threading.Lock()
        self.seq = 1                    # MsgPort_Stream_Connect: 0x1c0 = 1
        self.peer_seq: Optional[int] = None
        self.rx_total = 0
        self.connected = threading.Event()
        self.closed = threading.Event()
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._last_tx = 0.0
        self._advertised = 0            # rx_total at last credit update we sent

    # ---- tx ----------------------------------------------------------------
    def _emit(self, flags: int, data: bytes = b"") -> None:
        with self._lock:                     # assign seq and hand to RSMC atomically
            pkt = self._packet(flags, data)
            log.debug("%s TX flags=%02x seq=%d len=%d %s", self.name, flags, (self.seq - 1) & 0xFF, len(data), data[:32].hex(" "))
            self._send(pkt)

    def _packet(self, flags: int, data: bytes = b"") -> bytes:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFF
        credit = (RX_WINDOW + self.rx_total) & 0xFFFFFFFF
        self._advertised = self.rx_total
        stream = struct.pack("<II", (flags << 24) | (seq << 8), credit)
        if flags & F_SYN:
            body = struct.pack("<I", MTU << 16) + stream + data
            pkt = transport_header(2, len(body), self.local, self.remote) + body
        else:
            body = stream + data
            pkt = transport_header(1, len(body), self.local, self.remote) + body
        self._last_tx = time.monotonic()
        return pkt

    def connect(self, timeout: float = 5.0) -> bool:
        self._emit(F_SYN | F_HB)
        return self.connected.wait(timeout)

    def write(self, data: bytes) -> None:
        for i in range(0, len(data), MTU - 16):
            self._emit(F_DATA, data[i:i + MTU - 16])

    def keepalive_if_idle(self) -> None:
        if self.connected.is_set() and not self.closed.is_set() and time.monotonic() - self._last_tx >= KEEPALIVE_S:
            self._emit(F_KEEP)

    def close(self) -> None:
        if self.connected.is_set():
            self._emit(F_FIN)
        self.closed.set()

    # ---- rx ----------------------------------------------------------------
    def read(self, n: int, timeout: float = 5.0) -> bytes:
        deadline = time.monotonic() + timeout
        with self._cond:
            while len(self._buf) < n:
                left = deadline - time.monotonic()
                if left <= 0 or self.closed.is_set():
                    raise TimeoutError(f"{self.name}: wanted {n} bytes, have {len(self._buf)}")
                self._cond.wait(left)
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def on_packet(self, ptype: int, body: bytes) -> None:
        if ptype == 2:
            body = body[4:]                         # peer MTU word
        if len(body) < 8:
            return
        wa, _credit = struct.unpack_from("<II", body, 0)
        flags, seq = wa >> 24, (wa >> 8) & 0xFF
        data = body[8:]
        log.debug("%s RX t%d flags=%02x seq=%d credit=%d len=%d %s", self.name, ptype, flags, seq, _credit, len(data), data[:256].hex(" "))
        if flags & F_SYN:
            self.peer_seq = seq
            self.connected.set()
        elif self.peer_seq is not None and ((seq - self.peer_seq) & 0xFF) != 1:
            log.warning("%s: stream seq jump %d -> %d", self.name, self.peer_seq, seq)
        self.peer_seq = seq
        if data and flags & (F_DATA | F_WND):
            with self._cond:
                self._buf += data
                self.rx_total += len(data)
                self._cond.notify_all()
            # peer only takes new credit from DATA/WND packets (FUN_001314ec), never keepalives
            if self.rx_total - self._advertised >= RX_WINDOW // 4:
                self._emit(F_WND)
        if flags & F_FIN:
            self.closed.set()
            with self._cond:
                self._cond.notify_all()


class Router:
    """Splits RSMC payloads into transport messages and dispatches by dst port."""

    def __init__(self):
        self.ports: Dict[int, StreamPort] = {}
        self.on_unrouted: Optional[Callable[[int, int, int, bytes], None]] = None

    def add(self, port: StreamPort) -> None:
        self.ports[port.local] = port

    def deliver(self, payload: bytes) -> None:
        off = 0
        while off + 8 <= len(payload):
            w0, w1 = struct.unpack_from("<II", payload, off)
            ptype, length = w0 >> 28, w0 & 0xFFFF
            src, dst = w1 & 0xFFFF, w1 >> 16
            body = payload[off + 8:off + 8 + length]
            p = self.ports.get(dst)
            if p:
                p.on_packet(ptype, body)
            elif self.on_unrouted:
                self.on_unrouted(ptype, src, dst, body)
            off += 8 + ((length + 3) & ~3)              # messages are 4-byte aligned in a frame
            if ptype == 0 and length == 0:
                break
