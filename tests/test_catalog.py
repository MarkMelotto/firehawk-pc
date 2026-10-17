"""Display formatting checks (no hardware or model data needed)."""
from firehawk.catalog import Model, Param, fmt, note_name


def main():
    delay = Param("SyncSelect", "Note", 0, 1, 14, 6, note="delay")
    mod = Param("SyncSelect", "Note", 0, 1, 14, 6, note="mod")
    assert fmt(delay, 1) == "Whole" and fmt(delay, 6) == "1/4" and fmt(delay, 14) == "1/32"
    assert fmt(mod, 1) == "Whole" and fmt(mod, 6) == "1/4" and fmt(mod, 14) == "1/32"
    assert note_name("delay", 99) == "99"
    assert fmt(Param("Drive", "Drive", 1, 0, 1, 0.5), 0.5) == "5.0"
    assert Model("DynamicDelay", "Dynamic Delay HD", "delay_hd").label == "Dynamic Delay"
    assert Model("HD_AmpHiway100", "Hiway 100", "amp_hd").hd and not Model("Tape", "Tape Echo", "delay").hd
    from firehawk.device import parse_preset, preset_label
    assert [preset_label(i) for i in (0, 3, 4, 127)] == ["1A", "1D", "2A", "32D"]
    assert parse_preset("12c") == 46 and parse_preset("47") == 46 and parse_preset(preset_label(77)) == 77
    print("catalog OK")


if __name__ == "__main__":
    main()
