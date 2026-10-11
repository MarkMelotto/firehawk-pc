"""Firehawk Remote for PC, styled after the Line 6 Firehawk Remote app.  python -m firehawk.gui"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from PIL import Image, ImageEnhance, ImageTk

from .catalog import ASSETS, LOOKS, Catalog, Model, Param, fmt
from .device import Firehawk

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

TILE_W, TILE_H = 68, 81      # block.png (136x162 @xxhdpi) at half size
ICON_W, ICON_H = 54, 62


def _com_ports():
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except ImportError:
        return []


class Images:
    def __init__(self):
        self._cache: Dict[tuple, ImageTk.PhotoImage] = {}

    def get(self, name: str, w: int, h: Optional[int] = None, dim: bool = False):
        key = (name, w, h, dim)
        if key not in self._cache:
            im = Image.open(ASSETS / name).convert("RGBA")
            if h is None:
                h = round(im.height * w / im.width)
            im.thumbnail((w, h), Image.LANCZOS)
            if dim:
                r, g, b, a = im.split()
                rgb = ImageEnhance.Brightness(Image.merge("RGB", (r, g, b))).enhance(0.35)
                im = Image.merge("RGBA", (*rgb.split(), a))
            self._cache[key] = ImageTk.PhotoImage(im)
        return self._cache[key]


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
        return FIXED_FAMILY.get(self.group, "empty")

    @property
    def look(self):
        return LOOKS.get(self.family, LOOKS["empty"])

    @property
    def title(self) -> str:
        return self.model.name if self.model else (self.model_sid or self.look.label)


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
        self.title("Firehawk Remote")
        self.geometry("1180x720")
        self.minsize(980, 600)
        self.configure(bg=BG)
        self.img = Images()
        try:
            self.iconphoto(True, self.img.get("icon_fh1500_white.png", 64))
        except Exception:  # noqa: BLE001 - icon is cosmetic
            pass
        self.cat = Catalog()
        self.fh: Optional[Firehawk] = None
        self.worker = Worker()
        self.worker.start()
        self.blocks: List[Block] = [Block(g, s) for g, s in CHAIN]
        self.sel: Optional[Block] = None
        self.preset_names: List[str] = []
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
        tk.Label(top, image=self.img.get("icon_fh1500_white.png", 52), bg=HEADER).pack(side="left", padx=(12, 8))
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
        self.btn_power = tk.Label(ed, bg=BG, cursor="hand2")
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
            c.create_image(x, 8, anchor="nw",
                           image=self.img.get("selected_block.png" if sel else "block.png", TILE_W, TILE_H))
            if b.look.icon:
                c.create_image(x + TILE_W // 2, 8 + TILE_H // 2,
                               image=self.img.get(b.look.icon, ICON_W, ICON_H, dim=not b.enabled))
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
            self.btn_power.config(image="")
            return
        self.lbl_kind.config(text=b.look.label.upper(), fg=b.look.color)
        self.lbl_model.config(text=b.title)
        self.btn_power.config(image=self.img.get("icon_onoff.png" if b.enabled else "icon_onoff_off.png", 34))

    def _params(self) -> List[Param]:
        b = self.sel
        return b.model.params if (b and b.model) else []

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
            c.create_text(w // 2, 60, text="No editable parameters for this block", fill=SUB, font=(FONT, 12))
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
        if self.fh:
            fh = self.fh
            self.worker.set_latest((b.group, p.sid), lambda val, g=b.group, s=p.sid: fh.set(g, s, val), v)

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
            fh.connect()
            self.fh = fh
            self.after(0, self.lbl_status.config,
                       {"text": f"Connected on {target}  ·  firmware {fh.editor.version:#x}"})
            self.after(0, self.btn.config, {"text": "CONNECTED", "bg": "#1d7a2a"})
            self.after(0, self.lbl_preset.config, {"text": "Current tone"})
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
        if not b.model:
            return
        for p in b.model.params:
            try:
                b.values[p.sid] = self.fh.get(b.group, p.sid)
            except Exception:  # noqa: BLE001 - param not addressable on this firmware
                pass
        if b is self.sel:
            self.after(0, self._draw_rows)

    def _on_device_param(self, slot, param, value):
        for b in self.blocks:
            if b.slot == slot and b.model and any(p.sid == param for p in b.model.params):
                b.values[param] = value
                if b is self.sel:
                    self._draw_rows()

    def quit_app(self):
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
