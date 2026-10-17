"""Model catalog from the app's assets/*.models + block look from ProcessorType.java."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional



def _argb(v: int) -> str:
    return "#%06x" % (v & 0xFFFFFF)


@dataclass(frozen=True)
class BlockLook:
    label: str
    color: str         # thumb / accent colour (ProcessorType.color)
    track: str         # darker fill (slider_track_*.png)


# ProcessorType enum + slider_thumb/track colours
LOOKS = {
    "gate":     BlockLook("Gate", _argb(-5789785), "#3d3d3d"),
    "volume":   BlockLook("Vol", _argb(-9515853), "#335e52"),
    "wah":      BlockLook("Wah", _argb(-5009727), "#512d5f"),
    "stomp":    BlockLook("Stomp", "#b0b336", "#474801"),
    "stomp_hd": BlockLook("Stomp", "#b0b336", "#474801"),
    "mod":      BlockLook("Mod", _argb(-14243360), "#015274"),
    "delay":    BlockLook("Delay", _argb(-16601566), "#016301"),
    "delay_hd": BlockLook("Delay", _argb(-16601566), "#016301"),
    "synth":    BlockLook("Synth", _argb(-4886562), "#481b4d"),
    "reverb":   BlockLook("Reverb", _argb(-1025496), "#812101"),
    "reverb_hd": BlockLook("Reverb", _argb(-1025496), "#812101"),
    "amp":      BlockLook("Amp", "#ff2a00", "#5e1000"),
    "amp_hd":   BlockLook("HD Amp", _argb(-15897892), "#042144"),
    "cab":      BlockLook("Cab", _argb(-5789785), "#3d3d3d"),
    "comp":     BlockLook("Comp", _argb(-5197002), "#414201"),
    "eq":       BlockLook("EQ", "#b38236", "#51370f"),
    "looper":   BlockLook("Looper", _argb(-5789785), "#3d3d3d"),
    "fxloop":   BlockLook("FX Loop", _argb(-15158874), "#016149"),
    "fx":       BlockLook("FX", "#a7a7a7", "#3d3d3d"),
    "empty":    BlockLook("Empty", "#a7a7a7", "#3d3d3d"),
}

# assets/*.models "category" -> look family
CATEGORY = {1: "amp", 2: "cab", 3: "stomp", 4: "stomp", 5: "synth", 6: "synth", 7: "synth",
            8: "mod", 9: "delay", 10: "reverb", 11: "wah", 12: "fxloop", 14: "eq", 15: "looper"}
FIXED = {"Gate": "gate", "VolumePedal": "volume", "SharcPodFixedComp": "comp",
         "ParaGraphic4_band": "eq", "FXLoop": "fxloop", "LooperFx500": "looper"}
HD_FAMILIES = {"amp", "stomp", "delay", "reverb"}

# without imported model data: the six controls on the amp's front panel
FALLBACK = {"amp": [("Drive", "Drive"), ("Bass", "Bass"), ("Mid", "Mid"), ("Treble", "Treble"),
                    ("Presence", "Presence"), ("@volume", "Volume")]}

# params the app hides or edits elsewhere
HIDDEN = {"@enabled", "@stereo", "@temposync", "@mixtype", "@mic", "@post"}


@dataclass
class Param:
    sid: str
    name: str
    vtype: int           # 0 int, 1 float, 2 bool, 3 string
    vmin: float
    vmax: float
    default: object
    note: str = ""       # "delay" / "mod": SyncSelect shown as a note value like the app
    layout: object = None    # sliders.Layout: units / range / curve as in the app


@dataclass
class Model:
    sid: str
    name: str
    family: str
    params: List[Param] = field(default_factory=list)

    @property
    def look(self) -> BlockLook:
        return LOOKS.get(self.family, LOOKS["empty"])

    @property
    def hd(self) -> bool:
        """HD model (ProcessorModel.isHD: HD amps, drives, delays, synths, reverbs)."""
        return self.family.endswith("_hd")

    @property
    def label(self) -> str:
        """Name without a trailing ' HD' (the HD badge says it)."""
        return self.name[:-3] if self.hd and self.name.endswith(" HD") else self.name


# cab + mic are one device symbol (L6A36Preset::CabModelAndMicToStudioCabSymbol)
MICS = ["57OnAxis", "57OffAxis", "421", "U67"]
MIC_NAMES = ["57 On Axis", "57 Off Axis", "421 Dynamic", "U67 Condenser"]

# picker groups per block (from the app's *Catalog.json), in the app's order
PICKERS = {"amp": ("AmpCatalog.json", ["Clean", "American", "British", "High Gain"]),
           "fx": ("FXCatalog.json", None), "reverb": ("ReverbCatalog.json", None),
           "wah": ("WahCatalog.json", None), "cab": ("CabCatalog.json", None)}


# SliderRow.note[] order; SyncSelect 1 = whole note down to 14 = 1/32 for delay and mod alike
NOTES = ["1/32", "1/16 T", "1/16", "1/16 \u00b7", "1/8 T", "1/8", "1/8 \u00b7", "1/4 T", "1/4", "1/4 \u00b7",
         "1/2 T", "1/2", "1/2 \u00b7", "Whole"]


def note_name(kind: str, v) -> str:
    i = int(round(float(v))) - 1                     # SyncSelect is 1..14
    if not 0 <= i < len(NOTES):
        return str(v)
    return NOTES[len(NOTES) - 1 - i]                 # 1 = whole note ... 14 = 1/32 (delay and mod, checked on the amp)


def split_cab(sid: str):
    """'2x12BlackfaceStudio57OnAxis' -> ('2x12BlackfaceStudio', 0)."""
    for i, m in enumerate(MICS):
        if sid.endswith(m):
            return sid[: -len(m)], i
    return sid, 0


class Catalog:
    def __init__(self, folder: Optional[Path] = None):
        from .importer import models_dir
        folder = folder or models_dir()
        self.folder = folder
        self.models: Dict[str, Model] = {}
        self.raw: Dict[str, dict] = {}          # model json as shipped (all params, cablink, ...)
        from . import sliders
        sl = folder / "sliders.json"
        self.layouts, self.panels = sliders.load(json.loads(sl.read_text(encoding="utf-8"))) if sl.exists() else ({}, {})
        for f in sorted(folder.glob("*.models")):
            for m in json.loads(f.read_text(encoding="utf-8")):
                sid = m.get("symbolicID", "")
                fam = FIXED.get(sid) or CATEGORY.get(m.get("category"), "stomp")
                if m.get("devices") and fam in HD_FAMILIES:
                    fam += "_hd"
                params = [Param(p["symbolicID"], p.get("name", p["symbolicID"]), p.get("valueType", 1),
                                p.get("min", 0), p.get("max", 1), p.get("default"))
                          for p in m.get("params", [])
                          if p.get("name") and p["symbolicID"] not in HIDDEN and "." not in p["symbolicID"]
                          and p.get("valueType", 1) != 3]
                params = self._panel_params(sid, m, params)
                for p in params:
                    if p.sid == "SyncSelect":
                        p.note = "mod" if fam == "mod" else "delay"
                        p.layout = None
                # newest definition wins (hd files sort after the classic ones)
                self.raw[sid] = m
                self.models[sid] = Model(sid, m.get("shortname") or m.get("name", sid), fam, params)

    def groups(self, kind: str) -> List[tuple]:
        """[(group name, [model ids...]), ...] for a picker kind (amp, fx, reverb, wah, cab)."""
        if kind not in PICKERS:
            return []
        fname, only = PICKERS[kind]
        f = self.folder / fname
        if not f.exists():
            return []
        data = json.loads(f.read_text(encoding="utf-8"))
        return [(g, ids) for g, ids in data.items() if only is None or g in only]

    def _panel_params(self, sid: str, m: dict, default: List[Param]) -> List[Param]:
        """The app's slider panel for this model: its order, units and ranges (sliders.json)."""
        from .sliders import L_SKIP, PERCENT
        raw = {p["symbolicID"]: p for p in m.get("params", [])}
        views = self.panels.get(sid)
        if not views:
            for p in default:
                if p.vtype == 1 and p.vmin == 0 and p.vmax == 1:
                    p.layout = PERCENT
            return default
        out = []
        for psid, lid in views:
            if lid in L_SKIP or psid in HIDDEN or psid not in raw:
                continue
            r = raw[psid]
            out.append(Param(psid, r.get("name", psid), r.get("valueType", 1), r.get("min", 0), r.get("max", 1),
                             r.get("default"), layout=self.layouts.get(lid)))
        return out or default

    def device_params(self, sid: str) -> List[tuple]:
        """[(param symbol, valueType, default)] stored in a preset for this model (no '@' / sub-params)."""
        m = self.raw.get(sid) or self.raw.get(getattr(self.get(sid), "sid", ""), {})
        return [(p["symbolicID"], p.get("valueType", 1), p.get("default")) for p in m.get("params", [])
                if not p["symbolicID"].startswith("@") and "." not in p["symbolicID"]
                and p.get("valueType", 1) != 3]

    def cab_link(self, cab: str) -> str:
        return (self.raw.get(cab) or {}).get("cablink", "")

    @property
    def empty(self) -> bool:
        return not self.models

    def get(self, sid: str) -> Optional[Model]:
        """Exact match, else longest catalog id that prefixes it (cabs carry a mic suffix on the unit)."""
        if sid in self.models:
            return self.models[sid]
        best = max((k for k in self.models if sid.startswith(k)), key=len, default=None)
        return self.models.get(best) if best else None


def pretty(sid: str) -> str:
    """'HD_AmpBlackfaceDoubleVib' -> 'Blackface Double Vib' (used when no model data is imported)."""
    import re
    s = re.sub(r"^(HD_)?(Amp)?", "", sid)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s.replace("_", " ").strip() or sid


def fallback_params(group: str) -> List[Param]:
    from .sliders import PERCENT
    return [Param(sid, name, 1, 0.0, 1.0, 0.5, layout=PERCENT) for sid, name in FALLBACK.get(group, [])]


def fmt(p: Param, v) -> str:
    """Display like the app: %, dB, Hz, ms ... from the app's slider layouts; notes; On/Off."""
    if p.sid == "@mic":
        return MIC_NAMES[int(v)] if 0 <= int(v) < len(MIC_NAMES) else str(v)
    if p.note:
        return note_name(p.note, v)
    if p.vtype == 2:
        return "On" if v else "Off"
    if p.layout is not None:
        return p.layout.text(v)
    if p.vtype == 0:
        return str(int(v))
    if p.vmin == 0 and p.vmax == 1:
        from .sliders import PERCENT
        return PERCENT.text(v)
    return f"{float(v):.1f}"


def frac(p: Param, v) -> float:
    """Slider position 0..1 for a device value (the app's control scale, e.g. EQ frequency curves)."""
    if v is None:
        return 0.0
    if p.layout is not None:
        return p.layout.frac(v)
    return 0.0 if p.vmax == p.vmin else min(1.0, max(0.0, (float(v) - p.vmin) / (p.vmax - p.vmin)))


def value_at(p: Param, f: float):
    """Device value for a slider position 0..1."""
    if p.layout is not None:
        return p.layout.value_at(f)
    v = p.vmin + min(1.0, max(0.0, f)) * (p.vmax - p.vmin)
    return int(round(v)) if p.vtype == 0 else round(v, 3)
