# Firehawk Remote for PC

Control your **Line 6 Firehawk 1500 / Firehawk FX** from a Windows PC over Bluetooth: switch presets, see the
signal chain, turn effects on and off, and tweak amp and effect settings, much like the phone app.

> Unofficial project. Not made, endorsed or supported by Line 6 / Yamaha Guitar Group.
> "Line 6" and "Firehawk" are trademarks of their owners and are used here only to describe what the app works with.

---

## Install (no programming needed)

1. **Download** — go to [**Releases**](https://github.com/MarkMelotto/firehawk-pc/releases/latest) and download
   `FirehawkRemote.exe`. That's the whole app; there is nothing to install.
2. **Pair the amp** — on the PC open *Settings → Bluetooth & devices → Add device → Bluetooth* and pick your Firehawk
   (e.g. `Firehawk_1500_62`).
3. **Find its COM port** — in *Settings → Bluetooth & devices → Devices*, scroll down to *More Bluetooth settings*,
   open the **COM Ports** tab and note the port marked **Outgoing** next to your Firehawk (e.g. `COM4`).
   No outgoing port? Click *Add… → Outgoing → Firehawk → OK*.
4. **Close the Firehawk app on your phone** — the amp talks to one remote at a time.
5. **Start `FirehawkRemote.exe`.** Windows may say *"Windows protected your PC"* because the app isn't signed:
   click **More info → Run anyway**.
6. Type the port (e.g. `COM4`) at the top right and click **CONNECT**.

### Using it

| You want to… | Do this |
|---|---|
| Load a preset | Double-click it in the list on the left |
| Edit a block | Click it in the signal chain, then drag the sliders |
| Turn a block on/off | Double-click it, or use the power button on the right |
| Fine-tune a value | Hold **Ctrl** and use the mouse wheel over a slider |

Presets you switch on the amp itself show up in the app automatically.

### Optional: edit every effect

Out of the box you can edit the amp knobs and switch blocks on and off. To see **all settings of every effect**,
the app needs the effect list from the official *Firehawk Remote* Android app. This is a one-time step:

1. Get the app's `.apk` file, for example by exporting it from your Android phone with an "APK extractor" app.
2. In Firehawk Remote click **IMPORT FROM APK…** and pick the file.

The data stays on your PC (`%LOCALAPPDATA%\FirehawkRemote`); nothing is uploaded.

### Problems?

- **"Cannot open COM…"** — wrong port, or the amp is off / not paired. Use the *Outgoing* port.
- **Connects but nothing loads** — close the phone app, switch the amp off and on, and try again.
- Still stuck? Open an [issue](https://github.com/MarkMelotto/firehawk-pc/issues) and attach
  `firehawk-debug.log`, which is written next to `FirehawkRemote.exe`.

---

## Command line

`firehawk-cli.exe` (also on the Releases page) is for scripting:

```
firehawk-cli.exe --port COM4 presets              # list preset names
firehawk-cli.exe --port COM4 select 12            # load preset 12
firehawk-cli.exe --port COM4 set amp Drive 0.7    # knobs are 0..1
firehawk-cli.exe --port COM4 set reverb @enabled off
firehawk-cli.exe --port COM4 watch                # print changes made on the amp
```

Groups: `amp cab gate wah volume fx1 fx2 fx3 fx4 reverb compressor eq looper`.
Add `-vv` to log every Bluetooth frame. `--port` also accepts the amp's Bluetooth MAC address.

## For developers

```
pip install -r requirements.txt
python -m firehawk.gui                 # run from source
python -m tests.test_e2e               # protocol tests against a simulated amp
python -m tests.test_lossy             # same, with 15 % frame loss
python -m tests.preview_gui            # GUI against the simulated amp, no hardware needed
python -m firehawk.importer x.apk      # import model data from the command line
build.bat                              # local .exe build
```

How the Bluetooth protocol works is documented in [PROTOCOL.md](PROTOCOL.md).

### Making a release

1. Bump `__version__` in `firehawk/__init__.py` and add a section to `CHANGELOG.md`.
2. Commit, then tag and push:
   ```
   git tag v1.0.1
   git push origin main --tags
   ```
3. GitHub Actions runs the tests, builds both `.exe` files and publishes the release
   (see *Actions* tab). The tag must match `__version__`.

## Licence

[MIT](LICENSE) for the code in this repository. No Line 6 code, graphics or data are included; the optional model
data is read from the user's own copy of the official app.
