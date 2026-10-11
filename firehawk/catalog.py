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


@dataclass
class Model:
    sid: str
    name: str
    family: str
    params: List[Param] = field(default_factory=list)

    @property
    def look(self) -> BlockLook:
        return LOOKS.get(self.family, LOOKS["empty"])


class Catalog:
    def __init__(self, folder: Optional[Path] = None):
        from .importer import models_dir
        folder = folder or models_dir()
        self.models: Dict[str, Model] = {}
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
                # newest definition wins (hd files sort after the classic ones)
                self.models[sid] = Model(sid, m.get("shortname") or m.get("name", sid), fam, params)

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
    return [Param(sid, name, 1, 0.0, 1.0, 0.5) for sid, name in FALLBACK.get(group, [])]


def fmt(p: Param, v) -> str:
    """Display like the app: 0..1 knobs as 0-10, ints as-is, bools On/Off."""
    if p.vtype == 2:
        return "On" if v else "Off"
    if p.vtype == 0:
        return str(int(v))
    if p.vmin == 0 and p.vmax == 1:
        return f"{float(v) * 10:.1f}"
    return f"{float(v):.1f}"
