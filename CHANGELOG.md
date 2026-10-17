# Changelog

## 1.3.0 — 2026-10-17

- **SAVE** (or Ctrl+S): store the current tone on the amp, in the same preset or another one, with a new name.
  The preset that gets overwritten is backed up first (`%LOCALAPPDATA%\FirehawkRemote\backups`).
  The dialog starts at the current preset with its name filled in. The amp itself provides the tone that is
  saved (never a preset rebuilt by this app, which an earlier test build did and which made the amp hang
  when loading it); saving stops without writing if the amp switches preset meanwhile, and quitting during a save asks first.
- Hardware tones are grouped like on the amp and the phone app: **Bank 1** with A–D, up to **Bank 32**; presets
  are labelled 1A … 32D in the app and the command line.
- `firehawk-cli.exe save 3C --name "My Tone"`; `firehawk-cli.exe dump 3C` shows a stored preset.
- HD models (HD amps, drives, delays and reverbs) get the app's **HD** badge in the signal chain, the editor
  header and the model picker.
- Delay **Note** values were shown in reverse; they now run from a whole note down to 1/32, like on the amp.
- The previous run's log is kept as `firehawk-debug.previous.log`.
- Sending large messages now respects the amp's 512-byte receive window.
- No more hanging when the amp is off or out of range: connecting gives up after a few seconds with a clear
  message, the COM port is released so you can retry, a lost connection is detected and shown, and closing the
  app never freezes. The CONNECT button becomes DISCONNECT while connected.

## 1.2.0 — 2026-10-11

- **GET ALL EFFECTS…**: one click downloads the official Line 6 app and installs the effect list, so every block
  can change model. You can still pick an `.apk` (or `.xapk`) you downloaded yourself.

## 1.1.0 — 2026-10-11

- Tuner: click **TUNER** to use the amp's built-in tuner, shown like the phone app (note, ">" / "<" arrows and the
  cents bar). Switching the tuner on with the amp's footswitch opens it automatically.
- `firehawk-cli.exe tuner` prints note and cents.

## 1.0.0 — 2026-10-11

First release.

- Connects to a Firehawk 1500 / Firehawk FX over Bluetooth (Windows COM port or MAC address).
- Lists and loads the 128 presets; follows preset changes made on the amp.
- Shows the current tone's signal chain; turn blocks on and off.
- Edits amp and effect parameters with slider rows; tempo-synced delay and modulation show note values (1/8, 1/4 dotted, …) like the phone app.
- Change the model of every block: amps, cabs (incl. microphone), drives, mods, delays, filters/synths, reverbs and wahs.
- Effects switched on/off with the amp's footswitches or floorboard update in the app.
- Optional one-time import of model data from the official Android app (.apk) for full effect editing.
- Command-line tool `firehawk-cli.exe` for scripting.
