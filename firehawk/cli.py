"""firehawk-pc command line.

  python -m firehawk.cli --port COM5 presets             # list preset names
  python -m firehawk.cli --port COM5 select 12           # switch to preset 12
  python -m firehawk.cli --port COM5 set amp Drive 0.7   # 0..1 for knobs
  python -m firehawk.cli --port COM5 set fx1 @enabled off
  python -m firehawk.cli --port COM5 get amp Drive
  python -m firehawk.cli --port COM5 watch               # print knob/preset events from the unit
  add -v to log every frame (protocol debugging)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .device import Firehawk


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
    s = sub.add_parser("select"); s.add_argument("number", type=int)
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
        fh.connect(editor=args.cmd in ("get", "set", "watch"))
        if args.cmd == "presets":
            for i, n in enumerate(fh.preset_names(), 1):
                print(f"{i:3d}  {n}")
        elif args.cmd == "select":
            print("ok" if fh.select_preset(args.number) >= 0 else "device refused")
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
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
