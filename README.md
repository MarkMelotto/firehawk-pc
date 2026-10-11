# firehawk-pc

PC remote for the Line 6 Firehawk 1500 / Firehawk FX over Bluetooth (SPP). It's a clean reimplementation of the protocol in Line 6's original `libAmplifiRemoteNdk.so`. See `PROTOCOL.md`.

## Get the Line 6 files

The app's graphics, model definitions and symbol tables belong to Line 6, so they aren't in this repo. Copy them from your own Firehawk Remote APK:

```
pip install -r requirements.txt
python tools/extract_assets.py path\to\firehawk.apk
```

## Setup (Windows)

1. Pair the Firehawk in Windows Bluetooth settings. **Close the phone app first**, because the Firehawk accepts one remote at a time.
2. Open **More Bluetooth options → COM Ports** and note the **Outgoing** port for the Firehawk (for example `COM5`). If none is listed, click **Add… → Outgoing → Firehawk**.
3. Install the dependencies and extract the assets (see above), or just run `dist\FirehawkRemote.exe` from a build.

## Use

```
python -m firehawk.gui                                   # GUI: presets, amp knobs, block on/off
python -m firehawk.cli --port COM5 presets
python -m firehawk.cli --port COM5 select 12
python -m firehawk.cli --port COM5 set amp Drive 0.7      # knobs are 0..1
python -m firehawk.cli --port COM5 set reverb @enabled off
python -m firehawk.cli --port COM5 get amp Treble
python -m firehawk.cli --port COM5 watch                  # live knob/preset events from the unit
```

Add `-vv` to log every frame. If something fails on real hardware, that log shows where.

`--port` also accepts a MAC address (`00:11:22:33:44:55`) for a direct RFCOMM socket without a COM port.

Groups are `amp cab gate wah volume fx1 fx2 fx3 fx4 reverb compressor eq looper`. Param names come from `firehawk/assets/models/*.models` (for example `Drive`, `Bass`, `Mid`, `Treble`, `Presence`). The special params are `@enabled`, `@volume` (amp channel volume), `@mix`, and `@post`.

## Build the .exe

```
build.bat        # -> dist\FirehawkRemote.exe and dist\firehawk-cli.exe (needs the extracted assets)
```

## Tests

```
python -m tests.test_e2e     # full stack vs simulated device + byte-exact check against captured frames
python -m tests.test_lossy   # 15 % frame loss both ways
python -m tests.preview_gui  # GUI against the simulated amp, no hardware needed
```

## Status

Works on a real Firehawk 1500 over COM4: 128 presets, preset switching both ways, amp knobs, block on/off. Turning the amp's physical knobs isn't mirrored live; the window re-reads the knobs when the preset changes. `dist/FirehawkRemote.exe` writes `firehawk-debug.log` next to itself.
