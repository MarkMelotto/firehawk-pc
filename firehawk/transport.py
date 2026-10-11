"""Bluetooth Classic SPP transport for the Line 6 Firehawk FX.

The Firehawk exposes a plain Serial Port Profile (UUID 00001101-...), exactly
what the Android app opens with createInsecureRfcommSocketToServiceRecord().

Two ways to reach it from a PC:
  * COM port  - pair the Firehawk in Windows Bluetooth settings; Windows then
                creates an "outgoing" COM port for it (More Bluetooth options ->
                COM Ports). Most robust. Needs pyserial.
  * RFCOMM    - direct socket to the MAC address (stdlib, Windows/Linux).
"""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional


class Transport:
    def write(self, data: bytes) -> None: ...
    def read(self, n: int = 256) -> bytes: ...   # blocking; b"" on closed
    def close(self) -> None: ...


class SerialTransport(Transport):
    def __init__(self, port: str):
        import serial  # pyserial
        # baudrate is ignored by the BT virtual COM port but required by the API
        self._s = serial.Serial(port, 115200, timeout=0.2)

    def write(self, data: bytes) -> None:
        self._s.write(data)
        self._s.flush()

    def read(self, n: int = 256) -> bytes:
        while self._s.is_open:
            chunk = self._s.read(n)
            if chunk:
                return chunk
        return b""

    def close(self) -> None:
        self._s.close()


class RfcommTransport(Transport):
    def __init__(self, mac: str, channel: int = 1):
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise RuntimeError("This Python build has no AF_BLUETOOTH; use --port COMx instead")
        self._s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self._s.connect((mac, channel))

    def write(self, data: bytes) -> None:
        self._s.sendall(data)

    def read(self, n: int = 256) -> bytes:
        try:
            return self._s.recv(n)
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self._s.close()
        except OSError:
            pass


class Reader(threading.Thread):
    """Pumps bytes from the transport into a callback (mirrors handleBluetoothInputStream)."""

    def __init__(self, t: Transport, on_data: Callable[[bytes], None], on_close: Optional[Callable[[], None]] = None):
        super().__init__(daemon=True)
        self.t, self.on_data, self.on_close = t, on_data, on_close
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            data = self.t.read(64)  # app reads in 64-byte chunks
            if not data:
                break
            self.on_data(data)
        if self.on_close:
            self.on_close()

    def stop(self) -> None:
        self._stop.set()
