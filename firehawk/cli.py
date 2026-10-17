"""firehawk-pc command line.

  python -m firehawk.cli --port COM5 presets             # list preset names
  python -m firehawk.cli --port COM5 select 3C           # switch to preset 3C (or its number, 11)
  python -m firehawk.cli --port COM5 set amp Drive 0.7   # 0..1 for knobs
  python -m firehawk.cli --port COM5 set fx1 @enabled off
  python -m firehawk.cli --port COM5 get amp Drive
  python -m firehawk.cli --port COM5 watch               # print knob/preset events from the unit
  python -m firehawk.cli --port COM5 save 3C --name "My Tone"   # store the current tone in preset 3C
  python -m firehawk.cli --port COM5 dump 3C             # print (and save) stored preset 3C; "dump edit" = current tone, for debugging
  python -m firehawk.cli --port COM5 tuner               # engage the amp's tuner and print note/cents
  add -v to log every frame (protocol debugging)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .device import TUNER_NOTES, Firehawk, parse_preset, preset_label


def parse_value(s: str):
    low = s.lower()
    if low in ("on", "true"):
        return True
    if low in ("off", "false"):
        return False
    return float(s) if any(c in s for c in ".eE") else int(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="firehawk-pc")
    ap.add_argument("--port", required=True, help="Bluetooth COM port (COM5) or MAC (00:11:22:33:44:55)")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("presets")
    sub.add_parser("watch")
    sub.add_parser("tuner")
    sv = sub.add_parser("save"); sv.add_argument("number", type=parse_preset, help="e.g. 12C"); sv.add_argument("--name")
    sv.add_argument("--from", dest="source", type=parse_preset, help="preset the current tone was loaded from")
    dp = sub.add_parser("dump"); dp.add_argument("number", type=parse_preset, help="e.g. 12C")
    s = sub.add_parser("select"); s.add_argument("number", type=parse_preset, help="e.g. 12C")
    g = sub.add_parser("get"); g.add_argument("group"); g.add_argument("param")
    st = sub.add_parser("set"); st.add_argument("group"); st.add_argument("param"); st.add_argument("value")
    args = ap.parse_args(argv)

    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
                        format="%(asctime)s %(name)s %(message)s")
    try:
        fh = Firehawk.open_port(args.port)
    except Exception as e:  # noqa: BLE001 - report port problems without a traceback
        print(f"Cannot open {args.port}: {e}\n"
              "Check the Firehawk is paired and use its *Outgoing* COM port "
              "(Bluetooth settings > More Bluetooth options > COM Ports).", file=sys.stderr)
        return 2
    try:
        fh.connect(editor=args.cmd in ("get", "set", "watch", "tuner", "save", "dump"))
        if args.cmd == "presets":
            for i, n in enumerate(fh.preset_names()):
                print(f"{preset_label(i):>4}  {n}")
        elif args.cmd == "select":
            print("ok" if fh.select_preset(args.number + 1) >= 0 else "device refused")
        elif args.cmd == "get":
            print(fh.get(args.group, args.param))
        elif args.cmd == "set":
            print("ok" if fh.set(args.group, args.param, parse_value(args.value)) >= 0 else "device refused")
        elif args.cmd == "watch":
            fh.on_param = lambda slot, p, v: print(f"{slot:12s} {p:20s} {v}")
            fh.on_event = lambda ev: ev.kind not in (2, 4) and print(f"event {ev.kind}: {ev.body.hex(' ')}")
            print("Turn knobs / switch presets on the Firehawk. Ctrl+C to quit.")
            while True:
                time.sleep(1)
        elif args.cmd == "save":
            from .catalog import Catalog
            if args.source is not None:
                fh.current_preset = args.source
            for c in fh.save_preset(args.number, args.name, Catalog(), print) or ["(no changes)"]:
                print("  " + c)
            print("saved")
        elif args.cmd == "dump":
            from . import preset as pf
            raw = fh.librarian.get_preset_file(args.number)
            out = f"preset-{preset_label(args.number) if args.number < 0x10000 else 'edit'}.bin"
            open(out, "wb").write(raw)
            name = fh.editor.symbols.name
            depth = 0
            for e in pf.parse(raw):
                if e.key == pf.SLOT_END:
                    depth -= 1
                k = {pf.SECTION: "SECTION", pf.SECTION_VER: "VERSION", pf.SECTION_END: "END", pf.SLOT: "SLOT",
                     pf.SLOT_END: "SLOT END", pf.ASSET: "MODEL", pf.COUNT: "PARAMS"}.get(e.key) or name(e.key)
                v = name(e.value) if e.type == pf.T_SYM else e.value
                print("  " * depth + f"{k} = {v!r}" if not isinstance(v, bytes) else "  " * depth + f"{k} = <{len(v)} bytes>")
                if e.key == pf.SLOT:
                    depth += 1
            print(f"{len(raw)} bytes written to {out}")
        elif args.cmd == "tuner":
            fh.on_tuner = lambda n, c: print(f"\r{TUNER_NOTES[n] if n >= 0 else '-':3s} {c:+6.1f} cents ", end="",
                                             flush=True)
            if fh.tuner(True) < 0:
                print("device refused"); return 1
            print("Play a string. Ctrl+C to quit.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if fh.tuner_engaged and args.cmd == "tuner":
            try:
                fh.tuner(False)
            except Exception:  # noqa: BLE001
                pass
        fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
