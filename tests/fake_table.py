"""Synthetic symbol table in the device format, so tests need no Line 6 files."""
import struct
import zlib

NAMES = ["Amp", "Drive", "Bass", "Mid", "Treble", "Presence", "Master", "Sag", "Hum", "Bias", "GuitarProc",
         "ChannelVolume", "FX1", "FX2", "FX3", "FX4", "FX5", "Gate", "Wah", "VolumePedal", "StudioCab", "Looper",
         "PostComp", "PostEQ", "Air", "Level", "Thresh", "Decay", "Time", "Feedback", "Tone", "Mix", "Speed",
         "Depth", "Output", "LooperLevel"] + \
        [f"{x}{n}{s}" for x in ("FX",) for n in range(1, 6) for s in ("Enable", "Post", "Mix", "MixType")] + \
        ["GateEnable", "WahEnable", "VolumePedalEnable", "CabEnable", "PostCompEnable", "PostEQEnable",
         "LooperEnable", "HD_AmpBlackfaceDoubleVib", "HD_ColorDrive", "AnalogChorus", "DynamicDelay", "RezSynth",
         "HD_VerbzillaReverb63Spring", "Weeper", "LooperFx500", "2x12BlackfaceStudio57OnAxis",
         "SharcPodFixedComp", "ParaGraphic4_band"] + \
        [f"Filler{i:04d}" for i in range(1100)]          # ~30 KB like the real Firehawk 1500 table


def build(names=NAMES) -> bytes:
    blob, ents = b"", b""
    for n in names:
        b = n.encode()
        ents += struct.pack("<III", len(blob), zlib.crc32(b), len(b))
        blob += b + b"\0"
    return struct.pack("<II", len(names), len(blob)) + ents + blob
