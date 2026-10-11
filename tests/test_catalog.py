"""Display formatting checks (no hardware or model data needed)."""
from firehawk.catalog import Param, fmt, note_name


def main():
    delay = Param("SyncSelect", "Note", 0, 1, 14, 6, note="delay")
    mod = Param("SyncSelect", "Note", 0, 1, 14, 6, note="mod")
    assert fmt(delay, 1) == "1/32" and fmt(delay, 6) == "1/8" and fmt(delay, 14) == "Whole"
    assert fmt(mod, 1) == "Whole" and fmt(mod, 6) == "1/4" and fmt(mod, 14) == "1/32"
    assert note_name("delay", 99) == "99"
    assert fmt(Param("Drive", "Drive", 1, 0, 1, 0.5), 0.5) == "5.0"
    print("catalog OK")


if __name__ == "__main__":
    main()
