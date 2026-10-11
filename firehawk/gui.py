"""Firehawk Remote for PC, styled after the Line 6 Firehawk Remote app.  python -m firehawk.gui"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from . import __version__, icons
from .catalog import LOOKS, MICS, Catalog, Model, Param, fallback_params, fmt, pretty, split_cab
from .device import _EN, TUNER_NOTES, Firehawk
from .importer import DOWNLOAD_PAGE, data_dir, download_and_import, has_line6_data, import_apk

log = logging.getLogger("firehawk.gui")

# ---- palette (values/colors.xml) -------------------------------------------------
BG = "#000000"
PANEL = "#111111"
ROW = "#222222"             # slider_row.xml background
HEADER = "#142d64"          # firehawk_color
HILITE = "#0b4890"          # firehawk_lighter_blue
TEXT = "#ffffff"
SUB = "#9a9a9a"
FONT = "Segoe UI"

# signal chain shown left->right; blocks the amp doesn't report are hidden
CHAIN = [("gate", "Gate"), ("wah", "Wah"), ("volume", "VolumePedal"), ("compressor", "PostComp"),
         ("fx1", "FX1"), ("fx2", "FX2"), ("amp", "Amp"), ("cab", "StudioCab"), ("fx3", "FX3"),
         ("fx4", "FX4"), ("reverb", "FX5"), ("eq", "PostEQ"), ("looper", "Looper")]
FIXED_FAMILY = {"gate": "gate", "volume": "volume", "compressor": "comp", "eq": "eq", "looper": "looper",
                "wah": "wah", "cab": "cab", "amp": "amp"}

ENABLE_TO_GROUP = {v: k for k, v in _EN.items()}     # "FX5Enable" -> "reverb"

TILE_W, TILE_H = 68, 81      # block.png (136x162 @xxhdpi) at half size
ICON_W, ICON_H = 54, 62


# TunerFragment.CENT_SEGMENT: cents range -> lit segments of the 11-step bar (5 = in tune)
CENT_SEGMENTS = [(-51, -36, (0,)), (-36, -26, (0, 1)), (-26, -20, (1,)), (-20, -14, (1, 2)), (-14, -11, (2,)),
                 (-11, -9, (2, 3)), (-9, -7, (3,)), (-7, -5, (3, 4)), (-5, -3, (4,)), (-3, -2, (4, 5)),
                 (-2, 2, (5,)), (2, 3, (5, 6)), (3, 5, (6,)), (5, 7, (6, 7)), (7, 9, (7,)), (9, 11, (7, 8)),
                 (11, 14, (8,)), (14, 20, (8, 9)), (20, 26, (9,)), (26, 36, (9, 10)), (36, 51, (10,))]


def cent_segments(cents: float) -> tuple:
    return next((seg for lo, hi, seg in CENT_SEGMENTS if lo <= cents < hi), ())


class TunerWindow(tk.Toplevel):
    """The app's tuner screen: '>' note '<' over an 11-segment bar (tuner_fragment.xml / TunerView)."""
    DARK, MID, GREEN, RED = "#333333", "#777777", "#00ff00", "#ff0000"   # tuner_* colours
    DIM, LIT = "#4a4a4a", "#ffffff"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Tuner")
        self.configure(bg=BG)
        self.geometry(f"560x330+{app.winfo_rootx() + 310}+{app.winfo_rooty() + 150}")
        self.minsize(360, 220)
        self.transient(app)
        self.cv = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self._draw())
        self.protocol("WM_DELETE_WINDOW", app.tuner_off)
        self.bind("<Escape>", lambda e: app.tuner_off())
        self.note, self.cents = -1, 0.0
        self._draw()

    def show(self, note: int, cents: float):
        self.note, self.cents = note, cents
        self._draw()

    def _draw(self):
        c = self.cv
        c.delete("all")
        w, h = max(c.winfo_width(), 360), max(c.winfo_height(), 220)
        n, cents = self.note, self.cents
        if n < 0:
            up = down = False
        else:
            up, down = cents < 2.0, cents > -2.0          # flat -> '>' tune up, sharp -> '<' tune down
        big = (FONT, max(28, int(h * 0.2)), "normal")
        top = h * 0.3
        c.create_text(w / 6, top, text=">", fill=self.LIT if up else self.DIM, font=big)
        c.create_text(w / 2, top, text=TUNER_NOTES[n] if n >= 0 else "_", fill=TEXT, font=big)
        c.create_text(5 * w / 6, top, text="<", fill=self.LIT if down else self.DIM, font=big)
        if n >= 0:
            c.create_text(w / 2, h * 0.53, text=f"{cents:+.0f} cents", fill=SUB, font=(FONT, 10))
        step, y0, y1 = (w - 24) / 11, h * 0.62, h - 18
        lit = cent_segments(cents) if n >= 0 else ()
        for i in range(11):
            col = self.DARK if i != 5 else self.MID
            if i in lit:
                col = self.GREEN if i == 5 else self.RED
            c.create_rectangle(12 + i * step + 3, y0, 12 + (i + 1) * step - 3, y1, fill=col, width=0)


def _com_ports():
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except ImportError:
        return []


class Block:
    def __init__(self, group: str, slot: str):
        self.group, self.slot = group, slot
        self.model: Optional[Model] = None
        self.model_sid = ""
        self.enabled = True
        self.present = True
        self.values: Dict[str, object] = {}

    @property
    def family(self) -> str:
        if self.model:
            return self.model.family
        if self.group in FIXED_FAMILY:
            return FIXED_FAMILY[self.group]
        return "fx" if self.model_sid else "empty"

    @property
    def picker(self) -> Optional[str]:
        """Which model list this block can switch between (None = fixed block)."""
        if self.group in ("amp", "cab", "wah", "reverb"):
            return self.group
        return "fx" if self.group.startswith("fx") else None

    @property
    def look(self):
        return LOOKS.get(self.family, LOOKS["empty"])

    @property
    def title(self) -> str:
        return self.model.name if self.model else (pretty(self.model_sid) if self.model_sid else self.look.label)


class Worker(threading.Thread):
    """Serialises all device I/O off the Tk thread; coalesces slider writes."""

    def __init__(self):
        super().__init__(daemon=True)
        self.q: "queue.Queue" = queue.Queue()
        self._latest: Dict[tuple, tuple] = {}
        self._lock = threading.Lock()

    def submit(self, fn, *a):
        self.q.put((fn, a))

    def set_latest(self, key, fn, value):
        with self._lock:
            pending = key in self._latest
            self._latest[key] = (fn, value)
        if not pending:
            self.q.put((self._flush, (key,)))

    def _flush(self, key):
        with self._lock:
            fn, value = self._latest.pop(key)
        fn(value)

    def run(self):
        while True:
            fn, a = self.q.get()
            try:
                fn(*a)
            except Exception:  # noqa: BLE001 - keep the worker alive, log the cause
                log.exception("device call failed")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Firehawk Remote {__version__}")
        self.geometry("1180x720")
        self.minsize(980, 600)
        self.configure(bg=BG)
        try:
            from PIL import ImageTk
            self._icon = ImageTk.PhotoImage(icons.app_icon_png(64))
            self.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 - icon is cosmetic
            pass
        self.cat = Catalog()
        self.fh: Optional[Firehawk] = None
        self.worker = Worker()
        self.worker.start()
        self.blocks: List[Block] = [Block(g, s) for g, s in CHAIN]
        self.sel: Optional[Block] = None
        self.preset_names: List[str] = []
        self.tuner_win: Optional[TunerWindow] = None
        self.current_preset = -1
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.TCombobox", fieldbackground="#1b2f5e", background=HEADER, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=HEADER, lightcolor=HEADER, darkcolor=HEADER)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", "#1b2f5e")], foreground=[("readonly", TEXT)])
        style.configure("Vertical.TScrollbar", background="#3a3a3a", troughcolor=BG, bordercolor=BG,
                        arrowcolor=SUB, lightcolor="#3a3a3a", darkcolor="#3a3a3a", gripcount=0, arrowsize=12)
        style.map("Vertical.TScrollbar", background=[("disabled", BG), ("active", "#555555")],
                  troughcolor=[("disabled", BG)], arrowcolor=[("disabled", BG)])

        # header bar
        top = tk.Frame(self, bg=HEADER, height=56)
        top.pack(fill="x")
        top.pack_propagate(False)
        logo = tk.Canvas(top, width=52, height=52, bg=HEADER, highlightthickness=0)
        logo.pack(side="left", padx=(12, 8))
        icons.glyph(logo, "amp", 26, 26, 52, TEXT)
        titles = tk.Frame(top, bg=HEADER)
        titles.pack(side="left")
        self.lbl_device = tk.Label(titles, text="FIREHAWK REMOTE", fg=TEXT, bg=HEADER, font=(FONT, 13, "bold"))
        self.lbl_device.pack(anchor="w")
        self.lbl_status = tk.Label(titles, text="Not connected", fg="#aab7d6", bg=HEADER, font=(FONT, 9))
        self.lbl_status.pack(anchor="w")
        self.btn = tk.Button(top, text="CONNECT", command=self.connect, bg=HILITE, fg=TEXT, relief="flat",
                             activebackground="#1460b8", activeforeground=TEXT, font=(FONT, 10, "bold"),
                             padx=14, bd=0, cursor="hand2")
        self.btn.pack(side="right", padx=12, pady=12)
        self.btn_tuner = tk.Button(top, text="TUNER", command=self.tuner_on, bg=HEADER, fg=TEXT, relief="flat",
                                   activebackground=HILITE, activeforeground=TEXT, font=(FONT, 10, "bold"),
                                   padx=12, bd=0, cursor="hand2", state="disabled")
        self.btn_tuner.pack(side="right", pady=12)
        self.btn_import = tk.Button(top, text="GET ALL EFFECTS…", command=self.get_effects, bg=HEADER, fg="#ffd27a",
                                    relief="flat", activebackground=HILITE, activeforeground=TEXT,
                                    font=(FONT, 9, "bold"), bd=0, cursor="hand2")
        if not has_line6_data():
            self.btn_import.pack(side="right", padx=(0, 18))
        self.port = ttk.Combobox(top, values=_com_ports(), width=18, style="Dark.TCombobox")
        self.port.pack(side="right", pady=14)
        tk.Label(top, text="PORT", fg="#aab7d6", bg=HEADER, font=(FONT, 9, "bold")).pack(side="right", padx=6)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # preset list (hardware tones)
        left = tk.Frame(body, bg=PANEL, width=270)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="HARDWARE TONES", fg=SUB, bg=PANEL, font=(FONT, 9, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(12, 6))
        wrap = tk.Frame(left, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        # like the app: no scrollbars, the mouse wheel scrolls
        self.lst = tk.Listbox(wrap, bg=PANEL, fg="#e6e6e6", selectbackground=HEADER, selectforeground=TEXT,
                              highlightthickness=0, bd=0, activestyle="none", font=(FONT, 11))
        self.lst.pack(side="left", fill="both", expand=True)
        rea = tk.Label(left, text=f"v{__version__}  ·  built with REA", fg="#6f7f9f", bg=PANEL, font=(FONT, 8),
                       cursor="hand2", anchor="w")
        rea.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        rea.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://github.com/morluto/rea"))
        self.lst.bind("<Double-Button-1>", self._preset_clicked)
        self.lst.bind("<Return>", self._preset_clicked)

        # main column
        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        self.lbl_preset = tk.Label(main, text="Not connected", fg=TEXT, bg=BG, font=(FONT, 20, "bold"), anchor="w")
        self.lbl_preset.pack(fill="x", padx=18, pady=(14, 0))
        self.chain = tk.Canvas(main, bg=BG, height=TILE_H + 44, highlightthickness=0)
        self.chain.pack(fill="x", padx=12, pady=(6, 4))
        self.chain.bind("<Configure>", lambda e: self._draw_chain())

        # editor panel header (block name + power)
        ed = tk.Frame(main, bg=BG)
        ed.pack(fill="x", padx=18, pady=(4, 6))
        self.lbl_kind = tk.Label(ed, text="", fg=SUB, bg=BG, font=(FONT, 10, "bold"))
        self.lbl_kind.pack(side="left")
        self.lbl_model = tk.Label(ed, text="", fg=TEXT, bg=BG, font=(FONT, 15, "bold"))
        self.lbl_model.pack(side="left", padx=10)
        self.lbl_model.bind("<Button-1>", lambda e: self.open_picker())
        self.lbl_change = tk.Label(ed, text="", fg="#7fa7ff", bg=BG, font=(FONT, 9, "bold"), cursor="hand2")
        self.lbl_change.pack(side="left")
        self.lbl_change.bind("<Button-1>", lambda e: self.open_picker())
        self.btn_power = tk.Canvas(ed, width=40, height=40, bg=BG, highlightthickness=0, cursor="hand2")
        self.btn_power.pack(side="right")
        self.btn_power.bind("<Button-1>", lambda e: self.toggle_selected())

        # slider rows
        rows_wrap = tk.Frame(main, bg=BG)
        rows_wrap.pack(fill="both", expand=True, padx=(18, 6), pady=(0, 14))
        self.rows = tk.Canvas(rows_wrap, bg=BG, highlightthickness=0)
        self.rows.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self.rows.bind("<Configure>", lambda e: self._draw_rows())
        self.rows.bind("<Button-1>", self._row_press)
        self.rows.bind("<B1-Motion>", self._row_drag)
        self.rows.bind("<ButtonRelease-1>", self._row_release)
        self.rows.bind("<MouseWheel>", lambda e: self.rows.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.rows.bind("<Control-MouseWheel>", self._row_wheel)
        self._row_geo: List[tuple] = []        # (y0, y1, Param)
        self._drag: Optional[Param] = None

        self._draw_chain()
        self._update_editor_header()
        self._draw_rows()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

    # ------------------------------------------------------------- signal chain
    def _draw_chain(self):
        c = self.chain
        c.delete("all")
        shown = [b for b in self.blocks if b.present]
        if not shown:
            return
        width = max(c.winfo_width(), 600)
        gap = max(6, min(22, (width - 20 - len(shown) * TILE_W) // max(len(shown) - 1, 1)))
        total = len(shown) * TILE_W + (len(shown) - 1) * gap
        x = max(10, (width - total) // 2)
        mid = 8 + TILE_H // 2
        c.create_line(x, mid, x + total, mid, fill="#3a3a3a", width=3)
        for b in shown:
            sel = b is self.sel
            icons.tile(c, x, 8, TILE_W, TILE_H, sel)
            icons.glyph(c, b.family, x + TILE_W // 2, 8 + TILE_H // 2, ICON_W, b.look.color, dim=not b.enabled)
            label = b.title if len(b.title) <= 12 else b.title[:11] + "…"
            c.create_text(x + TILE_W // 2, 8 + TILE_H + 12, text=label, fill=TEXT if b.enabled else "#666",
                          font=(FONT, 8, "bold" if sel else "normal"))
            c.create_text(x + TILE_W // 2, 8 + TILE_H + 26, text=b.look.label.upper(),
                          fill=b.look.color if b.enabled else "#555", font=(FONT, 7, "bold"))
            hit = c.create_rectangle(x, 8, x + TILE_W, 8 + TILE_H + 32, outline="", fill="")
            c.tag_bind(hit, "<Button-1>", lambda e, blk=b: self.select(blk))
            c.tag_bind(hit, "<Double-Button-1>", lambda e, blk=b: self.toggle(blk))
            x += TILE_W + gap

    def select(self, b: Block):
        self.sel = b
        self.rows.yview_moveto(0)
        self._draw_chain()
        self._update_editor_header()
        self._draw_rows()
        if self.fh:
            self.worker.submit(self._load_block_values, b)

    # ------------------------------------------------------------ editor panel
    def _update_editor_header(self):
        b = self.sel
        if not b:
            self.lbl_kind.config(text="")
            self.lbl_model.config(text="")
            self.lbl_change.config(text="")
            self.btn_power.delete("all")
            return
        self.lbl_kind.config(text=b.look.label.upper(), fg=b.look.color)
        title = b.title
        if b.group == "cab" and b.model:
            title = b.model.name                         # mic is shown as its own row
        self.lbl_model.config(text=title, cursor="hand2" if self._can_pick(b) else "")
        self.lbl_change.config(text="CHANGE MODEL ▾" if self._can_pick(b) else "")
        self.btn_power.delete("all")
        if b.group != "amp":
            icons.power(self.btn_power, 20, 21, 14, b.enabled)

    def _params(self) -> List[Param]:
        b = self.sel
        if not b:
            return []
        ps = b.model.params if b.model else fallback_params(b.group)
        if b.group == "cab" and b.model_sid:
            ps = [Param("@mic", "Mic", 0, 0, len(MICS) - 1, 0)] + ps
        return ps

    def _draw_rows(self):
        c = self.rows
        c.delete("all")
        self._row_geo = []
        b = self.sel
        w = max(c.winfo_width(), 300)
        if not b:
            c.create_text(w // 2, 60, text="Connect, then click a block in the chain to edit it",
                          fill=SUB, font=(FONT, 12))
            return
        params = self._params()
        if not params:
            hint = ("Import the model data (button at the top) to edit this block" if self.cat.empty
                    else "No editable parameters for this block")
            c.create_text(w // 2, 60, text=hint, fill=SUB, font=(FONT, 12))
            return
        y, h = 0, 46
        look = b.look
        for p in params:
            v = b.values.get(p.sid, p.default)
            c.create_rectangle(0, y, w, y + h, fill=ROW, outline="")
            if p.vtype == 2:        # toggle row
                on = bool(v)
                c.create_rectangle(w - 78, y + 13, w - 12, y + h - 13, fill=look.color if on else "#3a3a3a",
                                   outline="")
                c.create_text(w - 45, y + h // 2, text="ON" if on else "OFF", fill=BG if on else SUB,
                              font=(FONT, 10, "bold"))
            else:
                frac = 0.0
                if v is not None and p.vmax != p.vmin:
                    frac = min(1.0, max(0.0, (float(v) - p.vmin) / (p.vmax - p.vmin)))
                fx = int(w * frac)
                c.create_rectangle(0, y, fx, y + h, fill=look.track, outline="")
                c.create_rectangle(max(0, fx - 4), y, fx, y + h, fill=look.color, outline="")
                c.create_text(w - 14, y + h // 2, text=fmt(p, v if v is not None else 0), fill=TEXT,
                              anchor="e", font=(FONT, 13))
            c.create_text(14, y + h // 2, text=p.name, fill=TEXT, anchor="w", font=(FONT, 13))
            self._row_geo.append((y, y + h, p))
            y += h + 1
        c.configure(scrollregion=(0, 0, w, y), yscrollincrement=h + 1)

    def _hit(self, ev) -> Optional[Param]:
        y = self.rows.canvasy(ev.y)
        for y0, y1, p in self._row_geo:
            if y0 <= y < y1:
                return p
        return None

    def _value_at(self, p: Param, x: int):
        w = max(self.rows.winfo_width(), 1)
        frac = min(1.0, max(0.0, x / w))
        v = p.vmin + frac * (p.vmax - p.vmin)
        return int(round(v)) if p.vtype == 0 else round(v, 3)

    def _row_press(self, ev):
        p = self._hit(ev)
        if not p or not self.sel:
            return
        if p.vtype == 2:
            self._send(self.sel, p, not bool(self.sel.values.get(p.sid, p.default)))
            return
        self._drag = p
        self._send(self.sel, p, self._value_at(p, ev.x))

    def _row_drag(self, ev):
        if self._drag and self.sel:
            self._send(self.sel, self._drag, self._value_at(self._drag, ev.x))

    def _row_release(self, _ev):
        self._drag = None

    def _row_wheel(self, ev):
        p = self._hit(ev)
        if not p or not self.sel or p.vtype == 2:
            return
        cur = self.sel.values.get(p.sid, p.default) or 0
        step = 1 if p.vtype == 0 else (p.vmax - p.vmin) / 100
        v = min(p.vmax, max(p.vmin, float(cur) + (step if ev.delta > 0 else -step)))
        self._send(self.sel, p, int(v) if p.vtype == 0 else round(v, 3))

    def _send(self, b: Block, p: Param, v):
        b.values[p.sid] = v
        self._draw_rows()
        if p.sid == "@mic":                                  # mic = part of the cab model symbol
            if self.fh and b.model_sid:
                b.model_sid = split_cab(b.model_sid)[0] + MICS[int(v)]
                self.worker.set_latest((b.group, "@mic"), lambda sid: self._apply_model(b, sid), b.model_sid)
            return
        if self.fh:
            fh = self.fh
            self.worker.set_latest((b.group, p.sid), lambda val, g=b.group, s=p.sid: fh.set(g, s, val), v)

    # ------------------------------------------------------------ model picker
    def _can_pick(self, b: Block) -> bool:
        return bool(self.fh and b.picker and self.cat.groups(b.picker))

    def open_picker(self):
        b = self.sel
        if not b or not self._can_pick(b):
            if b and b.picker and self.cat.empty:
                messagebox.showinfo("Change model", "Get the effect list first (GET ALL EFFECTS… at the top).")
            return
        syms = self.fh.editor.symbols.index
        base, mic = split_cab(b.model_sid) if b.group == "cab" else (b.model_sid, 0)
        rows = []                                           # (label, model id or None for a header)
        for group, ids in self.cat.groups(b.picker):
            avail = [i for i in ids if (i + MICS[mic] if b.group == "cab" else i) in syms]
            if not avail:
                continue
            rows.append((group.upper(), None))
            for i in avail:
                m = self.cat.get(i)
                name = m.name if m else pretty(i)
                rows.append((("● " if i == base else "   ") + name, i))

        win = tk.Toplevel(self)
        win.title(f"Choose {b.look.label}")
        win.configure(bg=PANEL)
        win.geometry(f"380x560+{self.winfo_rootx() + 300}+{self.winfo_rooty() + 120}")
        win.transient(self)
        q = tk.StringVar()
        ent = tk.Entry(win, textvariable=q, bg="#1e1e1e", fg=TEXT, insertbackground=TEXT, relief="flat",
                       font=(FONT, 12))
        ent.pack(fill="x", padx=10, pady=10, ipady=6)
        lst = tk.Listbox(win, bg=PANEL, fg="#e6e6e6", selectbackground=HEADER, selectforeground=TEXT,
                         highlightthickness=0, bd=0, activestyle="none", font=(FONT, 11))
        lst.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        shown: List[Optional[str]] = []

        def fill(*_):
            needle = q.get().strip().lower()
            lst.delete(0, "end")
            shown.clear()
            for label, mid in rows:
                if mid is None or not needle or needle in label.lower():
                    lst.insert("end", label)
                    shown.append(mid)
                    if mid is None:
                        lst.itemconfig("end", fg=b.look.color, selectbackground=PANEL)
        fill()
        q.trace_add("write", fill)

        def choose(_e=None):
            sel = lst.curselection()
            if not sel or shown[sel[0]] is None:
                return
            mid = shown[sel[0]]
            win.destroy()
            full = mid + MICS[mic] if b.group == "cab" else mid
            self.lbl_model.config(text="Loading…")
            self.worker.submit(self._apply_model, b, full)
        lst.bind("<Double-Button-1>", choose)
        lst.bind("<Return>", choose)
        ent.bind("<Return>", lambda e: (lst.selection_set(next((i for i, m in enumerate(shown) if m), 0)), choose()))
        ent.focus_set()

    def _apply_model(self, b: Block, sid: str):
        """Worker: load a model into the block, then re-read what the amp now has there."""
        st = self.fh.set_model(b.group, sid)
        if st < 0:
            self.after(0, messagebox.showerror, "Change model", f"The amp refused {sid} in this block (error {st}).")
        try:
            b.model_sid = self.fh.editor.current_model(b.slot) or sid
        except Exception:  # noqa: BLE001
            b.model_sid = sid
        b.model = self.cat.get(b.model_sid)
        b.values.clear()
        self.after(0, self._draw_chain)
        self.after(0, self._update_editor_header)
        self._load_block_values(b)
        if b is not self.sel:
            return
        self.after(0, self._draw_rows)

    def toggle_selected(self):
        if self.sel:
            self.toggle(self.sel)

    def toggle(self, b: Block):
        if b.group == "amp":
            return                      # the amp block has no bypass on the unit
        b.enabled = not b.enabled
        self._draw_chain()
        self._update_editor_header()
        if self.fh:
            self.worker.submit(self.fh.set, b.group, "@enabled", b.enabled)

    def get_effects(self):
        """One-time model data setup: download the official app automatically, or pick a downloaded .apk."""
        win = tk.Toplevel(self)
        win.title("Get all effects")
        win.configure(bg=PANEL)
        win.geometry(f"500x330+{self.winfo_rootx() + 300}+{self.winfo_rooty() + 120}")
        win.transient(self)
        win.resizable(False, False)
        tk.Label(win, text="Get all effects", fg=TEXT, bg=PANEL, font=(FONT, 15, "bold"), anchor="w").pack(
            fill="x", padx=20, pady=(18, 6))
        tk.Label(win, justify="left", anchor="w", wraplength=460, fg="#d0d0d0", bg=PANEL, font=(FONT, 10),
                 text="To change models and edit every effect, Firehawk Remote reads the effect list from the "
                      "official Line 6 Firehawk Remote Android app. This is a one-time step; the app file is only "
                      f"read on this PC and the effect list is kept in {data_dir()}.").pack(fill="x", padx=20)
        status = tk.Label(win, text="", fg="#ffd27a", bg=PANEL, font=(FONT, 9), anchor="w", wraplength=460,
                          justify="left")
        btns = []

        def busy(on: bool):
            for b in btns:
                b.config(state="disabled" if on else "normal")

        def auto():
            busy(True)
            status.config(text="Downloading the official app (about 60 MB)…")

            def progress(done, total):
                txt = f"Downloading… {done / 1e6:.0f} MB" + (f" of {total / 1e6:.0f} MB" if total else "")
                self.after(0, status.config, {"text": txt})

            def run():
                try:
                    n = download_and_import(progress)
                except Exception as e:  # noqa: BLE001 - report and offer the manual route
                    log.exception("download")
                    self.after(0, status.config, {"text": f"{e}\n\nUse the download page and pick the file instead."})
                    self.after(0, busy, False)
                    return
                self.after(0, lambda: (win.destroy(), self._effects_ready(n)))
            threading.Thread(target=run, daemon=True).start()

        def manual():
            path = filedialog.askopenfilename(parent=win, title="Select the Firehawk Remote app file",
                                              filetypes=[("Android app", "*.apk *.xapk *.apkm"), ("All files", "*.*")])
            if not path:
                return
            try:
                n = import_apk(path)
            except Exception as e:  # noqa: BLE001 - show any import problem
                status.config(text=str(e))
                return
            win.destroy()
            self._effects_ready(n)

        mk = dict(relief="flat", bd=0, cursor="hand2", font=(FONT, 10, "bold"), padx=14, pady=8)
        b1 = tk.Button(win, text="DOWNLOAD AND INSTALL AUTOMATICALLY", command=auto, bg=HILITE, fg=TEXT,
                       activebackground="#1460b8", activeforeground=TEXT, **mk)
        b1.pack(fill="x", padx=20, pady=(16, 6))
        b2 = tk.Button(win, text="I ALREADY HAVE THE .APK FILE…", command=manual, bg="#2a2a2a", fg=TEXT,
                       activebackground="#3a3a3a", activeforeground=TEXT, **mk)
        b2.pack(fill="x", padx=20)
        btns += [b1, b2]
        link = tk.Label(win, text="Open the download page in your browser", fg="#7fa7ff", bg=PANEL,
                        font=(FONT, 9, "underline"), cursor="hand2")
        link.pack(anchor="w", padx=20, pady=(8, 0))
        link.bind("<Button-1>", lambda e: __import__("webbrowser").open(DOWNLOAD_PAGE))
        status.pack(fill="x", padx=20, pady=(8, 0))

    def _effects_ready(self, n: int):
        self.cat = Catalog()
        self.btn_import.pack_forget()
        messagebox.showinfo("Get all effects", f"Imported {n} effect lists. Every block can now change model.")
        if self.fh:
            self.worker.submit(self._load_chain)

    # -------------------------------------------------------------- device I/O
    def connect(self):
        target = self.port.get().strip()
        if not target:
            messagebox.showinfo("Firehawk Remote", "Choose the Firehawk's Outgoing Bluetooth COM port first.")
            return
        self.btn.config(state="disabled", text="CONNECTING…")
        self.lbl_status.config(text=f"Connecting on {target}…")
        threading.Thread(target=self._connect_worker, args=(target,), daemon=True).start()

    def _connect_worker(self, target):
        try:
            fh = self._open(target)
            fh.on_patch = lambda i: self.worker.submit(self._on_patch, i)
            fh.on_param = lambda s, p, v: self.after(0, self._on_device_param, s, p, v)
            fh.on_tuner = lambda n, c: self.after(0, self._tuner_reading, n, c)
            fh.on_tuner_state = lambda on: self.after(0, self._tuner_state, on)
            fh.connect()
            self.fh = fh
            self.after(0, self.lbl_status.config,
                       {"text": f"Connected on {target}  ·  firmware {fh.editor.version:#x}"})
            self.after(0, self.btn.config, {"text": "CONNECTED", "bg": "#1d7a2a"})
            self.after(0, self.lbl_preset.config, {"text": "Current tone"})
            self.after(0, self.btn_tuner.config, {"state": "normal"})
            if fh.tuner_engaged:
                self.after(0, self._tuner_state, True)
            self.worker.submit(self._load_chain)
            n = fh.librarian.num_presets()
            names: List[str] = []
            for i in range(n):
                names.append(fh.librarian.preset_name(i))
                if i % 16 == 15 or i == n - 1:
                    self.after(0, self._fill_presets, list(names))
        except Exception as e:  # noqa: BLE001 - show any connect failure to the user
            log.exception("connect failed")
            self.after(0, messagebox.showerror, "Connection failed", str(e))
            self.after(0, self.lbl_status.config, {"text": "Not connected"})
            self.after(0, self.btn.config, {"state": "normal", "text": "CONNECT"})

    def _open(self, target):
        return Firehawk.open_port(target)

    def _fill_presets(self, names):
        self.preset_names = names
        self.lst.delete(0, "end")
        for i, n in enumerate(names, 1):
            self.lst.insert("end", f"  {i:>3}   {n}")
        if 0 <= self.current_preset < len(names):
            self._mark_preset(self.current_preset)

    def _mark_preset(self, idx):
        self.current_preset = idx
        self.lst.selection_clear(0, "end")
        if idx < self.lst.size():
            self.lst.selection_set(idx)
            self.lst.see(idx)
        name = self.preset_names[idx] if idx < len(self.preset_names) else ""
        self.lbl_preset.config(text=f"{idx + 1:03d}   {name}")

    def _preset_clicked(self, _e=None):
        if not self.fh or not self.lst.curselection():
            return
        idx = self.lst.curselection()[0]
        self._mark_preset(idx)
        self.worker.submit(self._select_preset, idx)

    def _select_preset(self, idx):
        self.fh.select_preset(idx + 1)
        self._load_chain()

    def _on_patch(self, idx):
        self.after(0, self._mark_preset, idx)
        self._load_chain()

    def _load_chain(self):
        """Worker thread: which model sits in each block and whether it's on."""
        fh = self.fh
        for b in self.blocks:
            try:
                sid = fh.editor.current_model(b.slot)
            except Exception:  # noqa: BLE001 - slot not on this unit
                sid = ""
            b.present = bool(sid) or b.group in ("amp", "cab")
            b.model_sid = sid
            b.model = self.cat.get(sid)
            try:
                b.enabled = bool(fh.get(b.group, "@enabled")) if b.group != "amp" else True
            except Exception:  # noqa: BLE001
                b.enabled = True
            b.values.clear()
        self.after(0, self._chain_loaded)

    def _chain_loaded(self):
        if self.sel is None or not self.sel.present:
            self.sel = next((b for b in self.blocks if b.group == "amp"), None)
        self._draw_chain()
        self._update_editor_header()
        self._draw_rows()
        if self.sel:
            self.worker.submit(self._load_block_values, self.sel)

    def _load_block_values(self, b: Block):
        if b.group == "cab" and b.model_sid:
            b.values["@mic"] = split_cab(b.model_sid)[1]
        for p in (b.model.params if b.model else fallback_params(b.group)):
            try:
                b.values[p.sid] = self.fh.get(b.group, p.sid)
            except Exception:  # noqa: BLE001 - param not addressable on this firmware
                pass
        if b is self.sel:
            self.after(0, self._draw_rows)

    def _on_device_param(self, slot, param, value):
        """Editor event from the amp (floorboard / amp controls)."""
        log.info("amp event %s/%s = %r", slot, param, value)
        if slot == "GuitarProc" and param in ENABLE_TO_GROUP:            # block switched on/off
            for b in self.blocks:
                if b.group == ENABLE_TO_GROUP[param]:
                    b.enabled = bool(value)
                    self._draw_chain()
                    if b is self.sel:
                        self._update_editor_header()
            return
        if slot == "GuitarProc" and param == "ChannelVolume":
            slot, param = "Amp", "@volume"
        for b in self.blocks:
            if b.slot == slot and b.model and any(p.sid == param for p in b.model.params):
                b.values[param] = value
                if b is self.sel:
                    self._draw_rows()

    # ------------------------------------------------------------------ tuner
    def tuner_on(self):
        if not self.fh:
            return
        self._tuner_state(True)
        self.worker.submit(self._tuner_set, True)

    def tuner_off(self):
        self._tuner_state(False)
        if self.fh and self.fh.tuner_engaged:
            self.worker.submit(self._tuner_set, False)

    def _tuner_set(self, on: bool):
        try:
            st = self.fh.tuner(on)
        except Exception as e:  # noqa: BLE001
            log.exception("tuner")
            st = str(e)
        if on and not (isinstance(st, int) and st >= 0):
            self.after(0, self._tuner_state, False)
            self.after(0, messagebox.showerror, "Tuner", f"The amp did not start its tuner ({st}).")

    def _tuner_state(self, on: bool):
        """Tuner engaged from here or with the amp's tuner footswitch: open / close the tuner window."""
        if on and self.tuner_win is None:
            self.tuner_win = TunerWindow(self)
        elif not on and self.tuner_win is not None:
            self.tuner_win.destroy()
            self.tuner_win = None

    def _tuner_reading(self, note: int, cents: float):
        if self.tuner_win is not None:
            self.tuner_win.show(note, cents)

    def quit_app(self):
        if self.fh and self.fh.tuner_engaged:
            try:
                self.fh.tuner(False)
            except Exception:  # noqa: BLE001
                pass
        if self.fh:
            try:
                self.fh.close()
            except Exception:  # noqa: BLE001 - closing a dead link
                pass
        self.destroy()


def _setup_log():
    d = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    logging.basicConfig(filename=os.path.join(d, "firehawk-debug.log"), filemode="w", level=logging.DEBUG,
                        format="%(asctime)s.%(msecs)03d %(threadName)s %(name)s %(message)s", datefmt="%H:%M:%S")


def main():
    _setup_log()
    App().mainloop()


if __name__ == "__main__":
    main()
