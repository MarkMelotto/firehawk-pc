"""Original block glyphs drawn on a Tk canvas (no Line 6 artwork is used or needed)."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk


def tile(c: "tk.Canvas", x: int, y: int, w: int, h: int, selected: bool) -> None:
    """Rounded grey tile; white outline when selected."""
    r = 9
    pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r, x + w, y + h,
           x + w - r, y + h, x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
    c.create_polygon(pts, smooth=True, fill="#474747", outline="#ffffff" if selected else "#474747",
                     width=3 if selected else 1)


def power(c: "tk.Canvas", cx: int, cy: int, r: int, on: bool) -> None:
    col = "#ffffff" if on else "#5a5a5a"
    c.create_arc(cx - r, cy - r, cx + r, cy + r, start=120, extent=300, style="arc", outline=col, width=4)
    c.create_line(cx, cy - r - 3, cx, cy - 2, fill=col, width=4, capstyle="round")


def glyph(c: "tk.Canvas", family: str, cx: int, cy: int, s: int, color: str, dim: bool = False) -> None:
    """Draw the icon for a block family centred on (cx, cy) inside an s*s box."""
    col = _dim(color) if dim else color
    w = max(2, s // 14)
    h = s // 2
    fam = family.replace("_hd", "")
    line = lambda *p, **k: c.create_line(*p, fill=col, width=w, capstyle="round", joinstyle="round", **k)
    if fam == "gate":                                   # signal line through an open gate
        line(cx - h, cy, cx - h // 4, cy)
        line(cx + h // 4, cy, cx + h, cy)
        line(cx - h // 4, cy - h // 2, cx - h // 4, cy + h // 2)
        line(cx + h // 4, cy - h // 2, cx + h // 4, cy + h // 2)
    elif fam == "volume":                               # speaker with sound waves
        line(cx - h, cy - h // 4, cx - h // 2, cy - h // 4, cx - h // 6, cy - h // 2,
             cx - h // 6, cy + h // 2, cx - h // 2, cy + h // 4, cx - h, cy + h // 4, cx - h, cy - h // 4)
        for k in (1, 2):
            rr = h // 3 * k + h // 6
            c.create_arc(cx - rr, cy - rr, cx + rr, cy + rr, start=-40, extent=80, style="arc",
                         outline=col, width=w)
    elif fam == "wah":                                  # rocker pedal, side view
        line(cx - h, cy + h // 2, cx + h, cy + h // 2)
        line(cx - h + 4, cy + h // 3, cx + h - 4, cy - h // 3)
        line(cx, cy + h // 2, cx, cy + h // 6)
    elif fam in ("stomp", "fx"):                                # stomp box: two knobs + switch
        c.create_rectangle(cx - h * 2 // 3, cy - h, cx + h * 2 // 3, cy + h, outline=col, width=w)
        for dx in (-h // 3, h // 3):
            c.create_oval(cx + dx - 5, cy - h // 2 - 5, cx + dx + 5, cy - h // 2 + 5, outline=col, width=w)
        c.create_oval(cx - 7, cy + h // 3 - 7, cx + 7, cy + h // 3 + 7, fill=col, outline=col)
    elif fam == "mod":                                  # sine wave
        _wave(c, cx, cy, h, col, w, lambda t: math.sin(t * 2 * math.pi * 1.5))
    elif fam == "synth":                                # sawtooth
        _wave(c, cx, cy, h, col, w, lambda t: 2 * ((t * 2.5) % 1) - 1)
    elif fam == "delay":                                # fading echoes
        for i, f in enumerate((1.0, 0.65, 0.4)):
            rr = int(h * 0.38 * f) + 3
            x = cx - h + h // 3 + i * (h * 2 // 3)
            c.create_oval(x - rr, cy - rr, x + rr, cy + rr, outline=_mix(col, f), width=w)
    elif fam == "reverb":                               # expanding arcs
        for k in range(3):
            rr = h // 3 + k * h // 3
            c.create_arc(cx - h - rr, cy - rr, cx - h + rr, cy + rr, start=-50, extent=100, style="arc",
                         outline=_mix(col, 1 - k * 0.25), width=w)
    elif fam == "amp":                                  # amp head: knob row + grille
        c.create_rectangle(cx - h, cy - h * 2 // 3, cx + h, cy + h * 2 // 3, outline=col, width=w)
        for i in range(5):
            x = cx - h * 2 // 3 + i * h // 3
            c.create_oval(x - 3, cy - h // 2 - 3, x + 3, cy - h // 2 + 3, fill=col, outline=col)
        for i in range(4):
            yy = cy - h // 6 + i * h // 6
            line(cx - h + 6, yy, cx + h - 6, yy)
    elif fam == "cab":                                  # speaker cabinet
        c.create_rectangle(cx - h, cy - h, cx + h, cy + h, outline=col, width=w)
        c.create_oval(cx - h * 2 // 3, cy - h * 2 // 3, cx + h * 2 // 3, cy + h * 2 // 3, outline=col, width=w)
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=col, outline=col)
    elif fam == "comp":                                 # arrows squeezing a level
        line(cx - h, cy - h // 2, cx - h // 4, cy - h // 6, arrow="last")
        line(cx - h, cy + h // 2, cx - h // 4, cy + h // 6, arrow="last")
        line(cx, cy, cx + h, cy)
    elif fam == "eq":                                   # three faders
        for i, k in enumerate((0.3, -0.35, 0.1)):
            x = cx - h * 2 // 3 + i * h * 2 // 3
            line(x, cy - h, x, cy + h)
            yk = cy + int(k * h)
            c.create_rectangle(x - 6, yk - 4, x + 6, yk + 4, fill=col, outline=col)
    elif fam == "looper":                               # circular arrow
        c.create_arc(cx - h * 2 // 3, cy - h * 2 // 3, cx + h * 2 // 3, cy + h * 2 // 3, start=60, extent=300,
                     style="arc", outline=col, width=w)
        line(cx + h // 3, cy - h * 2 // 3 + 1, cx + h // 2 + 6, cy - h * 2 // 3 - 4, arrow="last")
    elif fam == "fxloop":                               # send / return arrows
        line(cx - h, cy - h // 3, cx + h, cy - h // 3, arrow="last")
        line(cx + h, cy + h // 3, cx - h, cy + h // 3, arrow="last")
    else:                                               # empty slot
        c.create_text(cx, cy, text="+", fill="#6a6a6a", font=("Segoe UI", s // 3))
    if family.endswith("_hd"):
        c.create_text(cx + h - 2, cy + h, text="HD", fill=col, anchor="se", font=("Segoe UI", max(7, s // 7), "bold"))


def _wave(c, cx, cy, h, col, w, fn) -> None:
    pts = []
    for i in range(41):
        t = i / 40
        pts += [cx - h + t * 2 * h, cy - fn(t) * h * 0.6]
    c.create_line(*pts, fill=col, width=w, smooth=True, capstyle="round")


def _mix(col: str, f: float) -> str:
    r, g, b = (int(col[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (int(r * f + 0x47 * (1 - f)), int(g * f + 0x47 * (1 - f)), int(b * f + 0x47 * (1 - f)))


def _dim(col: str) -> str:
    return _mix(col, 0.3)


def app_icon_png(size: int = 256):
    """Original app icon (amp glyph on blue), returned as a Pillow image."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 6, fill=(20, 45, 100, 255))
    m = s // 6
    d.rounded_rectangle((m, s // 4, s - m, s * 3 // 4), radius=s // 20, outline="white", width=max(2, s // 28))
    for i in range(5):
        x = m + s // 9 + i * (s - 2 * m - 2 * s // 9) // 4
        r = s // 32
        d.ellipse((x - r, s // 3 - r, x + r, s // 3 + r), fill="white")
    for i in range(3):
        y = s // 2 + i * s // 14
        d.line((m + s // 14, y, s - m - s // 14, y), fill="white", width=max(1, s // 48))
    return im
