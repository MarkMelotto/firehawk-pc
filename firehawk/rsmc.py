"""RobustSerialMsgChannel (RSMC): reliable packet link over the SPP byte stream.

Reconstructed from libAmplifiRemoteNdk.so (armv7, original Line 6):
  RobustSerialMsgChannel_HandleRxData / _ExecTasks / CRC16_Process,
  AndroidSppMsgChannel::execTasks (IAP sync).

Frame (12-byte header, all little-endian):
  0  55 55          magic
  2  00
  3  seq            sequence of this data packet (ack-only packets repeat last seq)
  4  ack            last in-order packet delivered from peer
  5  ack + window   highest seq the peer may send (window = 8)
  6  len  u16       payload length (padded to 4)
  8  pcrc u16       CRC16 of payload (0 when len == 0)
 10  hcrc u16       CRC16 of the 12 header bytes with hcrc zeroed
CRC16 = CCITT-FALSE (poly 0x1021, init 0xFFFF).
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Callable, Dict, Optional

log = logging.getLogger("firehawk.rsmc")

MAGIC = b"\x55\x55"
WINDOW = 8
RETX_S = 1.0          # RobustSerialMsgChannel_Init(..., 1000)
TICK_S = 0.2          # Timer_StartPeriodic(.., 200)
MAX_PAYLOAD = 0xF0 - 12


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        x = (crc >> 8) ^ b
        x ^= x >> 4
        crc = ((crc << 8) ^ x ^ (x << 12) ^ (x << 5)) & 0xFFFF
    return crc


def build_frame(seq: int, ack: int, payload: bytes = b"") -> bytes:
    if len(payload) % 4:
        payload += b"\0" * (4 - len(payload) % 4)
    pcrc = crc16(payload) if payload else 0
    hdr = bytearray(MAGIC + bytes([0, seq & 0xFF, ack & 0xFF, (ack + WINDOW) & 0xFF])
                    + struct.pack("<HHH", len(payload), pcrc, 0))
    hdr[10:12] = struct.pack("<H", crc16(hdr))
    return bytes(hdr) + payload


class Rsmc:
    """Owns the transport write side; deliver() is called with each in-order payload."""

    def __init__(self, write: Callable[[bytes], None], deliver: Callable[[bytes], None]):
        self._write = write
        self._deliver = deliver
        self._lock = threading.RLock()
        self._rx = bytearray()
        self.tx_seq = 0            # last seq we used
        self.tx_acked = 0          # last seq acked by peer
        self.peer_limit = WINDOW   # highest seq peer accepts
        self.rx_last = 0           # last seq delivered from peer
        self._unacked: Dict[int, tuple] = {}   # seq -> (frame, t_sent)
        self._queue: list[bytes] = []
        self._ooo: Dict[int, bytes] = {}       # out-of-order buffer
        self._ack_due = True
        self._running = False
        self._thr: Optional[threading.Thread] = None
        self.stats = {"rx": 0, "tx": 0, "crc_err": 0, "retx": 0}

    # ---- lifecycle ---------------------------------------------------------
    def sync(self) -> None:
        """IAP sync exactly as AndroidSppMsgChannel::execTasks: OK, 5 ticks, START, 5 ticks."""
        self._write(b"IAP x OK")
        time.sleep(5 * TICK_S)
        self._write(b"IAP x START")
        time.sleep(5 * TICK_S)

    def start(self) -> None:
        self._running = True
        self._thr = threading.Thread(target=self._ticker, daemon=True, name="rsmc-tick")
        self._thr.start()

    def stop(self) -> None:
        self._running = False
        try:
            self._write(b"IAP x STOP")
        except Exception:  # noqa: BLE001 - link may already be gone
            pass

    # ---- tx ----------------------------------------------------------------
    def send(self, payload: bytes) -> None:
        with self._lock:
            self._queue.append(payload)
            self._pump()

    def _pump(self) -> None:
        now = time.monotonic()
        # ExecTasks: send while (int8)(peer_limit - tx_seq) > 0 and our own window has room
        while self._queue and ((self.tx_seq + 1 - self.tx_acked) & 0xFF) <= WINDOW \
                and 0 < ((self.peer_limit - self.tx_seq) & 0xFF) < 0x80:
            self.tx_seq = (self.tx_seq + 1) & 0xFF
            frame = build_frame(self.tx_seq, self.rx_last, self._queue.pop(0))
            self._unacked[self.tx_seq] = (frame, now)
            self._raw(frame)
            self._ack_due = False

    def _raw(self, frame: bytes) -> None:
        self.stats["tx"] += 1
        log.debug("TX %s", frame.hex(" "))
        self._write(frame)

    def _ticker(self) -> None:
        while self._running:
            time.sleep(TICK_S)
            with self._lock:
                now = time.monotonic()
                for seq, (frame, t) in sorted(self._unacked.items()):
                    if now - t >= RETX_S:
                        # header ack field is stale in a retransmit; rebuild with current ack
                        n = struct.unpack_from("<H", frame, 6)[0]
                        fresh = build_frame(seq, self.rx_last, frame[12:12 + n])
                        self._unacked[seq] = (fresh, now)
                        self.stats["retx"] += 1
                        self._raw(fresh)
                self._pump()
                # ExecTasks sends an ack-only header when it has nothing else to send
                self._raw(build_frame(self.tx_seq, self.rx_last))
                self._ack_due = False

    # ---- rx ----------------------------------------------------------------
    def feed(self, data: bytes) -> None:
        with self._lock:
            self._rx += data
            while True:
                i = self._rx.find(MAGIC)
                if i < 0:
                    del self._rx[:-1]
                    return
                if i:
                    del self._rx[:i]            # drops "IAP x ..." text and noise
                if len(self._rx) < 12:
                    return
                hdr = bytearray(self._rx[:12])
                hcrc = struct.unpack_from("<H", hdr, 10)[0]
                hdr[10:12] = b"\0\0"
                if crc16(hdr) != hcrc:
                    self.stats["crc_err"] += 1
                    del self._rx[:1]
                    continue
                n, pcrc = struct.unpack_from("<HH", hdr, 6)
                if len(self._rx) < 12 + n:
                    return
                payload = bytes(self._rx[12:12 + n])
                del self._rx[:12 + n]
                if n and crc16(payload) != pcrc:
                    self.stats["crc_err"] += 1
                    continue
                self.stats["rx"] += 1
                log.debug("RX seq=%d ack=%d lim=%d len=%d %s", hdr[3], hdr[4], hdr[5], n, payload[:40].hex(" "))
                self._on_frame(hdr[3], hdr[4], hdr[5], payload)

    def _on_frame(self, seq: int, ack: int, limit: int, payload: bytes) -> None:
        # peer acks
        while self.tx_acked != ack and ((ack - self.tx_acked) & 0xFF) <= WINDOW:
            self.tx_acked = (self.tx_acked + 1) & 0xFF
            self._unacked.pop(self.tx_acked, None)
        self.peer_limit = limit
        if payload:
            d = (seq - self.rx_last) & 0xFF
            if 1 <= d <= WINDOW:
                self._ooo[seq] = payload
            while ((self.rx_last + 1) & 0xFF) in self._ooo:
                self.rx_last = (self.rx_last + 1) & 0xFF
                pl = self._ooo.pop(self.rx_last)
                try:
                    self._deliver(pl)
                except Exception:  # noqa: BLE001 - never kill the link on a parse bug
                    log.exception("deliver failed")
            self._raw(build_frame(self.tx_seq, self.rx_last))   # prompt ack
        self._pump()
