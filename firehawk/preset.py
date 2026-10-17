"""Firehawk preset files (L6SPePresetSerializer) and "save edit buffer to preset" by patching the stored file.

A preset file is a flat stream of {key u32, type u16, value}:
  type 0 none | 1 bool u32 | 2 int i32 | 3 float f32 | 4 string {len u32, bytes incl. NUL}
       6 array {count u32, count*u32} | 7 symbol u32 (index into the device symbol table)
Directives (keys >= 0x80000000):
  0x80000000 section start (int: 1 DSP, 2 PS/metadata)   0x80000001 section version   0x80000002 section end
  0x80000003 slot start (symbol)   0x80000004 slot end
  0x80000005 asset (model symbol)  0x80000006 count (int), followed by `count` params {"<Param>State" symbol: value}
PS section key 0 = preset name (max 63 bytes).

The original app rebuilds the whole file from its own edit buffer (L6A36Preset::WriteToDevice). We instead
take the preset as stored on the amp (librarian GetPresetFile), replace models and values with what the amp's
edit buffer holds right now (editor QueryCurrentDspModel / GetDspModelParam), and upload it (SetPresetFile).
Everything we don't understand is kept byte for byte.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

log = logging.getLogger("firehawk.preset")

SECTION, SECTION_VER, SECTION_END, SLOT, SLOT_END, ASSET, COUNT = range(0x80000000, 0x80000007)
T_NONE, T_BOOL, T_INT, T_FLOAT, T_STR, T_5, T_ARRAY, T_SYM = range(8)
NAME_KEY = 0
MAX_NAME = 63


@dataclass
class Entry:
    key: int
    type: int
    raw: bytes            # value bytes exactly as stored (after the type)

    @property
    def u32(self) -> int:
        return struct.unpack_from("<I", self.raw)[0]

    @property
    def value(self):
        if self.type == T_FLOAT:
            return struct.unpack("<f", self.raw)[0]
        if self.type == T_INT:
            return struct.unpack("<i", self.raw)[0]
        if self.type == T_BOOL:
            return bool(self.u32)
        if self.type == T_STR:
            return self.raw[4:].split(b"\0")[0].decode("latin1")
        if self.type == T_SYM:
            return self.u32
        return self.raw

    def encode(self) -> bytes:
        return struct.pack("<IH", self.key, self.type) + self.raw


def entry(key: int, typ: int, value) -> Entry:
    if typ == T_FLOAT:
        raw = struct.pack("<f", float(value))
    elif typ == T_INT:
        raw = struct.pack("<i", int(round(float(value))))
    elif typ in (T_BOOL,):
        raw = struct.pack("<I", 1 if value else 0)
    elif typ == T_SYM:
        raw = struct.pack("<I", value)
    elif typ == T_STR:
        b = value.encode("latin1", "replace")[:MAX_NAME] + b"\0"
        raw = struct.pack("<I", len(b)) + b
    elif typ == T_NONE:
        raw = b""
    else:
        raise ValueError(f"can't build type {typ}")
    return Entry(key, typ, raw)


def parse(data: bytes) -> List[Entry]:
    out, o = [], 0
    while o < len(data):
        if o + 6 > len(data):
            raise ValueError(f"truncated entry at {o}")
        key, typ = struct.unpack_from("<IH", data, o)
        o += 6
        if typ in (T_NONE, T_5):
            n = 0
        elif typ in (T_BOOL, T_INT, T_FLOAT, T_SYM):
            n = 4
        elif typ == T_STR:
            n = 4 + struct.unpack_from("<I", data, o)[0]
        elif typ == T_ARRAY:
            n = 4 + 4 * struct.unpack_from("<I", data, o)[0]
        else:
            raise ValueError(f"unknown value type {typ} at {o - 6}")
        if o + n > len(data):
            raise ValueError(f"truncated value at {o}")
        out.append(Entry(key, typ, data[o:o + n]))
        o += n
    return out


def serialize(entries: List[Entry]) -> bytes:
    return b"".join(e.encode() for e in entries)


@dataclass
class Asset:
    slot: str             # slot symbol name, e.g. "FX1", "Amp", "GuitarProc"
    model_at: int         # index of the ASSET entry
    count_at: int         # index of the COUNT entry; params follow


def assets(entries: List[Entry], name: Callable[[int], str]) -> List[Asset]:
    out, stack = [], []
    for i, e in enumerate(entries):
        if e.key == SLOT:
            stack.append(name(e.u32))
        elif e.key == SLOT_END and stack:
            stack.pop()
        elif e.key == ASSET and stack and i + 1 < len(entries) and entries[i + 1].key == COUNT:
            out.append(Asset(stack[-1], i, i + 1))
    return out


def preset_name(entries: List[Entry]) -> Optional[str]:
    section = 0
    for e in entries:
        if e.key == SECTION:
            section = e.u32
        elif e.key == NAME_KEY and section == 2 and e.type == T_STR:
            return e.value
    return None


def set_name(entries: List[Entry], new: str) -> None:
    section = 0
    for i, e in enumerate(entries):
        if e.key == SECTION:
            section = e.u32
        elif e.key == NAME_KEY and section == 2 and e.type == T_STR:
            entries[i] = entry(NAME_KEY, T_STR, new)
            return
    raise ValueError("preset has no name field")


class Patcher:
    """Rewrites a stored preset with the amp's live edit buffer.

    model(slot) -> current model symbol name ("" if unknown)
    param(slot, name) -> current value (raises if not addressable)
    model_params(model) -> [(param name, value type 0 int/1 float/2 bool, default)] for a model change
    cab_link(cab) -> LiveCab model for a StudioCab model (catalog 'cablink')
    """

    def __init__(self, symbols, model, param, model_params=None, cab_link=None):
        self.sym = symbols
        self.model, self.param = model, param
        self.model_params = model_params or (lambda m: [])
        self.cab_link = cab_link or (lambda c: "")
        self.changes: List[str] = []

    def _idx(self, name: str) -> Optional[int]:
        return self.sym.index.get(name)

    def _live(self, slot: str, p: str):
        for s in (slot, slot.replace("Stereo", "")):
            try:
                return self.param(s, p)
            except Exception:  # noqa: BLE001 - not addressable in this slot spelling
                continue
        raise KeyError(p)

    def _current_model(self, slot: str) -> str:
        for s in (slot, slot.replace("Stereo", "")):
            try:
                m = self.model(s)
                if m:
                    return m
            except Exception:  # noqa: BLE001
                continue
        return ""

    def apply(self, entries: List[Entry]) -> List[Entry]:
        out = list(entries)
        studio_cab = None
        # back to front so index shifts from rebuilt param lists don't affect earlier assets
        for a in reversed(assets(out, self.sym.name)):
            old_model = self.sym.name(out[a.model_at].u32)
            if a.slot == "LiveCab":
                continue                                   # handled after StudioCab
            if a.slot == "GuitarProc" or a.slot == "Air":
                new_model = old_model
            else:
                new_model = self._current_model(a.slot) or old_model
            n = out[a.count_at].value
            if new_model != old_model and self._idx(new_model) is not None:
                out[a.model_at] = entry(ASSET, T_SYM, self._idx(new_model))
                self.changes.append(f"{a.slot}: {old_model} -> {new_model}")
                if a.slot == "StudioCab":
                    studio_cab = new_model
                    new_params = self._patch_params(a.slot, out[a.count_at + 1:a.count_at + 1 + n])
                else:
                    new_params = self._build_params(a.slot, new_model)
            else:
                new_params = self._patch_params(a.slot, out[a.count_at + 1:a.count_at + 1 + n])
            out[a.count_at] = entry(COUNT, T_INT, len(new_params))
            out[a.count_at + 1:a.count_at + 1 + n] = new_params
        if studio_cab:
            self._relink_live_cab(out, studio_cab)
        return out

    def _patch_params(self, slot: str, params: List[Entry]) -> List[Entry]:
        res = []
        for e in params:
            key = self.sym.name(e.key)
            p = key[:-5] if key.endswith("State") else key
            try:
                v = self._live(slot, p)
            except KeyError:
                res.append(e)
                continue
            typ = e.type if e.type in (T_BOOL, T_INT, T_FLOAT) else T_FLOAT
            ne = entry(e.key, typ, v)
            if ne.raw != e.raw:
                self.changes.append(f"{slot}/{p}: {e.value!r} -> {ne.value!r}")
            res.append(ne)
        return res

    def _build_params(self, slot: str, model: str) -> List[Entry]:
        res = []
        for p, vtype, default in self.model_params(model):
            k = self._idx(p + "State")
            if k is None or p.startswith("@"):
                continue
            try:
                v = self._live(slot, p)
            except KeyError:
                v = default if default is not None else 0
            typ = {0: T_INT, 1: T_FLOAT, 2: T_BOOL}.get(vtype, T_FLOAT)
            res.append(entry(k, typ, v))
        return res

    def _relink_live_cab(self, out: List[Entry], studio_cab: str) -> None:
        from .catalog import split_cab
        link = self.cab_link(split_cab(studio_cab)[0])
        if not link or self._idx(link) is None:
            return
        for a in assets(out, self.sym.name):
            if a.slot == "LiveCab":
                old = self.sym.name(out[a.model_at].u32)
                if old != link:
                    out[a.model_at] = entry(ASSET, T_SYM, self._idx(link))
                    self.changes.append(f"LiveCab: {old} -> {link}")
