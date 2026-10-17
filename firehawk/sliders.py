"""How the phone app shows each parameter: units (%, dB, Hz, ms), ranges and curves.

The app keeps this in its native slider library (libSlidersLogicLib.so, ParamLayoutDefs + per-model
ModelPanelViewDef tables). Nothing of it ships here: `extract()` reads it from the user's own APK during the
import and stores it as sliders.json next to the model data. The conversions below re-implement the app's
ModelParamLayouts (control <-> param <-> display value, EQ frequency curves, display formats).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# ParamLayoutID values used by this app
L_DELAY_NOTE, L_MOD_NOTE, L_HEADER = 4, 7, 57
L_SKIP = {2, 5, 52, L_HEADER}           # tempo-mode switches, tap tempo button, section headers

# ParamDisplayFormatType
F_NONE, F_INT, F_PCT, F_DB, F_MS, F_HZMOD, F_SLOWFAST, F_EQGAIN, F_EQHZ, F_EQQ, F_HEADS, F_BITS, F_WAVE, \
    F_INT1, F_INT2, F_BPM = range(16)
F_CONDITION, F_DBF, F_SPEAKER, F_WIDTH = 26, 30, 31, 32

_DISCRETE = {
    F_HEADS: ["12--", "1-3-", "1--4", "-23-", "123-", "12-4", "1-34", "-234", "1234"],
    F_BITS: ["12", "11", "10", "9", "8", "7", "6", "5", "4"],
    F_WAVE: [f"Wave {i}" for i in range(1, 9)],
    F_INT1: ["-1 oct", "-maj6th", "-min6th", "-4th", "unison", "min3rd", "maj3rd", "5th", "1oct"],
    F_INT2: ["-1 oct", "-5th", "-4th", "-maj2nd", "unison", "4th", "5th", "min7th", "1oct"],
    F_CONDITION: ["Wall", "Floor", "Free Field"],
    F_SPEAKER: ["Mono", "Stereo", "Stereo Wide"],
    F_WIDTH: ["Mono", "Stereo", "Stereo Wide"],
}

# EQ frequency curves (curve type 1..4): break-point scale and 9-point display table in Hz
_EQ = {1: (0.312674, [50, 130, 210, 290, 370, 450, 530, 610, 690]),
       2: (0.398429, [50, 130, 290, 450, 1250, 2050, 2850, 4450, 6050]),
       3: (0.172006, [100, 900, 1700, 3300, 4900, 6500, 8100, 9700, 11300]),
       4: (0.586351, [500, 900, 1300, 2100, 2900, 4500, 6100, 7700, 9300])}
_MID = 0.503937


@dataclass(frozen=True)
class Layout:
    lid: int
    ctl: Tuple[float, float]
    param: Tuple[float, float]
    disp: Tuple[float, float]
    fmt: int
    curve: int            # 0 linear, 1..4 EQ frequency
    detents: int
    is_int: bool

    # ---- conversions (ModelParamLayouts) -----------------------------------
    def to_control(self, v: float) -> float:
        if self.curve in _EQ:
            return _eq_dsp(_EQ[self.curve][0], v, *self.param, *self.ctl, to_dsp=False)
        return _lin(v, *self.param, *self.ctl)

    def to_param(self, c: float) -> float:
        if self.curve in _EQ:
            return _eq_dsp(_EQ[self.curve][0], c, *self.ctl, *self.param, to_dsp=True)
        return _lin(c, *self.ctl, *self.param)

    def to_display(self, c: float) -> float:
        if self.curve in _EQ:
            k, table = _EQ[self.curve]
            return _eq_table(_eq_dsp(k, c, *self.ctl, *self.param, to_dsp=True), table)
        return _lin(c, *self.ctl, *self.disp)

    # slider position 0..1 <-> device value
    def frac(self, v: float) -> float:
        lo, hi = self.ctl
        return 0.0 if hi == lo else min(1.0, max(0.0, (self.to_control(float(v)) - lo) / (hi - lo)))

    def value_at(self, frac: float):
        lo, hi = self.ctl
        c = lo + min(1.0, max(0.0, frac)) * (hi - lo)
        if self.detents:
            c = round(c)
        v = self.to_param(c)
        return int(round(v)) if self.is_int else round(v, 4)

    def text(self, v) -> str:
        d = self.to_display(self.to_control(float(v)))
        f = self.fmt
        r = int(d - 0.5) if d < 0 else int(d + 0.5)
        if f == F_PCT:
            return f"{r} %"
        if f == F_DB:
            return f"{r} dB"
        if f == F_DBF:
            return f"{d:.1f} dB"
        if f == F_MS:
            return f"{r} ms"
        if f == F_HZMOD:
            return f"{d:.2f} Hz"
        if f == F_SLOWFAST:
            return "Slow" if d < 0.5 else "Fast"
        if f == F_EQGAIN:
            return f"{0.0 if abs(d) < 0.05 else d:.1f} dB"
        if f == F_EQHZ:
            return f"{d / 1000:.1f} kHz" if d >= 1000 else f"{int(d + 0.5)} Hz"
        if f == F_EQQ:
            return f"{d:.1f}"
        if f == F_BPM:
            return f"{d:.1f} BPM"
        if f in _DISCRETE:
            lst = _DISCRETE[f]
            return lst[int(d + 0.5)] if 0 <= int(d + 0.5) < len(lst) else str(r)
        return str(r)


def _lin(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    y = (y1 - y0) * (x - x0) / (x1 - x0) + y0
    return min(max(y, min(y0, y1)), max(y0, y1))


def _eq_dsp(k, x, x0, x1, y0, y1, to_dsp):
    if to_dsp:
        mx, my = (x1 - x0) * _MID + x0, (y1 - y0) * k + y0
    else:
        mx, my = (x1 - x0) * k + x0, (y1 - y0) * _MID + y0
    return _lin(x, x0, mx, y0, my) if x <= mx else _lin(x, mx, x1, my, y1)


def _eq_table(x, table):
    if x >= 1.0:
        return table[8]
    v = max(x, 0.0) * 8.0
    i = int(math.floor(v))
    return table[i] + (table[i + 1] - table[i]) * (v - i)


# ---------------------------------------------------------------- extraction from the APK's .so
def _segments(so: bytes):
    """ELF32 PT_LOAD segments -> [(vaddr, offset, filesz)]."""
    if so[:4] != b"\x7fELF" or so[4] != 1:
        raise ValueError("not a 32-bit ELF")
    phoff, = struct.unpack_from("<I", so, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", so, 0x2A)
    segs = []
    for i in range(phnum):
        typ, off, vaddr, _pa, filesz = struct.unpack_from("<IIIII", so, phoff + i * phentsize)
        if typ == 1:
            segs.append((vaddr, off, filesz))
    return segs


def extract(so: bytes, model_ids: Dict[int, str]) -> dict:
    """{'layouts': {lid: [...]}, 'panels': {model symbol: [[param symbol, lid], ...]}} from libSlidersLogicLib.so."""
    segs = _segments(so)

    def at(va):
        for v, o, n in segs:
            if v <= va < v + n:
                return va - v + o
        return None

    def cstr(va):
        o = at(va)
        if o is None:
            return None
        e = so.find(b"\0", o)
        s = so[o:e]
        return s.decode("latin1") if len(s) < 80 and all(32 <= c < 127 for c in s) else None

    layouts: Dict[int, list] = {}
    # ParamLayoutDefs table: 0x38-byte entries {lid, uiType, ctlMin, ctlMax, detents, img, curve, pMin, pMax,
    # isInt, dispCurve, dMin, dMax, fmt}; find the run of >= 40 consecutive plausible entries
    for o in range(0, len(so) - 0x38 * 40, 4):
        ok = 0
        while o + (ok + 1) * 0x38 <= len(so):
            lid, ui, *_ = struct.unpack_from("<ii", so, o + ok * 0x38)
            fmt, = struct.unpack_from("<i", so, o + ok * 0x38 + 0x34)
            curve, = struct.unpack_from("<i", so, o + ok * 0x38 + 0x18)
            if not (0 < lid < 80 and 0 <= ui < 8 and 0 <= fmt < 40 and 0 <= curve < 6):
                break
            ok += 1
        if ok >= 40:
            for i in range(ok):
                f = struct.unpack_from("<iiffiiiff?3xiffi", so, o + i * 0x38)
                if f[0] not in layouts:
                    layouts[f[0]] = [f[2], f[3], f[7], f[8], f[11], f[12], f[13], f[6], f[4], bool(f[9])]
            break
    # ModelPanelViewDef: {modelID, 12 x {layoutID, char* symbol, imageType}} = 0x94 bytes
    panels: Dict[str, list] = {}
    for v, o, n in segs:
        for i in range(o, o + n - 0x94, 4):
            mid, = struct.unpack_from("<I", so, i)
            if mid not in model_ids or model_ids[mid] in panels:
                continue
            views, good = [], True
            for k in range(12):
                lid, p, _img = struct.unpack_from("<iIi", so, i + 4 + k * 12)
                if lid == 0:
                    continue
                s = cstr(p)
                if lid not in layouts or s is None:
                    good = False
                    break
                views.append([s, lid])
            if good and views:
                panels[model_ids[mid]] = views
    if not layouts or not panels:
        raise ValueError("slider tables not found")
    return {"layouts": {str(k): v for k, v in layouts.items()}, "panels": panels}


def load(data: dict) -> Tuple[Dict[int, Layout], Dict[str, List[Tuple[str, int]]]]:
    layouts = {int(k): Layout(int(k), (v[0], v[1]), (v[2], v[3]), (v[4], v[5]), v[6], v[7], v[8], v[9])
               for k, v in data.get("layouts", {}).items()}
    panels = {m: [(s, int(l)) for s, l in views] for m, views in data.get("panels", {}).items()}
    return layouts, panels


# without imported data: knobs 0..1 shown as 0-100 %, like the app's kID_LAYOUT_SLIDER_PERCENT
PERCENT = Layout(1, (0.0, 1.0), (0.0, 1.0), (0.0, 100.0), F_PCT, 0, 0, False)
