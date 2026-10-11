"""Copy the Line 6 graphics, model definitions and symbol tables out of your own Firehawk Remote APK.

These files are Line 6's, so they are not in the repo. Run once after cloning:

    python tools/extract_assets.py path/to/firehawk.apk
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "firehawk"
PNG = ["block", "selected_block", "block_amp", "block_amp_blue", "block_amp_blue_hd", "block_cab", "block_comp",
       "block_delay", "block_delay_hd", "block_eq", "block_fxloop", "block_gate", "block_looper", "block_mod",
       "block_reverb", "block_reverb_hd", "block_stomp", "block_stomp_hd", "block_synth", "block_synth_hd",
       "block_volume", "block_wah", "icon_onoff", "icon_onoff_off", "icon_fh1500_white", "icon_fh_white"]
TABLES = ["defaultSymbolTable_Firebird.bin", "defaultSymbolTable_FX100.bin", "defaultSymbolTable.bin"]


def main(apk: str) -> int:
    out = PKG / "assets"
    (out / "models").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as z:
        names = set(z.namelist())
        for n in PNG:                                            # xxhdpi = sharpest variant
            (out / f"{n}.png").write_bytes(z.read(f"res/drawable-xxhdpi-v4/{n}.png"))
        models = [n for n in names if n.startswith("assets/") and n.endswith(".models")]
        for n in models:
            (out / "models" / Path(n).name).write_bytes(z.read(n))
        for t in TABLES:                                         # cached handshake tables
            (PKG / t).write_bytes(z.read(f"assets/{t}"))
    _make_icon(out / "icon_fh1500_white.png", PKG.parent / "firehawk.ico")
    print(f"extracted {len(PNG)} images, {len(models)} model files, {len(TABLES)} symbol tables")
    return 0


def _make_icon(src: Path, dst: Path) -> None:
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    s = max(im.size)
    bg = Image.new("RGBA", (s, s), (20, 45, 100, 255))         # firehawk_color #142d64
    bg.alpha_composite(im, ((s - im.width) // 2, (s - im.height) // 2))
    bg.save(dst, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
