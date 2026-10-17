"""Display formatting checks (no hardware or model data needed)."""
from firehawk.catalog import Model, Param, fmt, note_name


def main():
    delay = Param("SyncSelect", "Note", 0, 1, 14, 6, note="delay")
    mod = Param("SyncSelect", "Note", 0, 1, 14, 6, note="mod")
    assert fmt(delay, 1) == "Whole" and fmt(delay, 6) == "1/4" and fmt(delay, 14) == "1/32"
    assert fmt(mod, 1) == "Whole" and fmt(mod, 6) == "1/4" and fmt(mod, 14) == "1/32"
    assert note_name("delay", 99) == "99"
    assert Model("DynamicDelay", "Dynamic Delay HD", "delay_hd").label == "Dynamic Delay"
    assert Model("HD_AmpHiway100", "Hiway 100", "amp_hd").hd and not Model("Tape", "Tape Echo", "delay").hd
    from firehawk.catalog import frac, value_at
    from firehawk.sliders import Layout
    eqhz = Layout(14, (0, 1), (0, 1), (50, 690), 8, 1, 0, False)     # lo-shelf frequency curve
    assert fmt(Param("Low", "Lo", 1, 0, 1, 0, layout=eqhz), 0) == "50 Hz"
    assert fmt(Param("Low", "Lo", 1, 0, 1, 0, layout=eqhz), 1) == "690 Hz"
    p = Param("Low", "Lo", 1, 0, 1, 0, layout=eqhz)
    assert all(abs(frac(p, value_at(p, f)) - f) < 1e-3 for f in (0, .2, .5, .7, 1))
    gate = Param("Thresh", "Threshold", 1, -96, 0, -58, layout=Layout(12, (-96, 0), (-96, 0), (-96, 0), 3, 0, 0, False))
    assert fmt(gate, -58) == "-58 dB" and value_at(gate, 0.5) == -48.0
    assert fmt(Param("Drive", "Drive", 1, 0, 1, 0.5), 0.5) == "50 %"
    from firehawk.device import parse_preset, preset_label
    assert [preset_label(i) for i in (0, 3, 4, 127)] == ["1A", "1D", "2A", "32D"]
    assert parse_preset("12c") == 46 and parse_preset("47") == 46 and parse_preset(preset_label(77)) == 77
    print("catalog OK")


if __name__ == "__main__":
    main()
