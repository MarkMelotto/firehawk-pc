# Changelog

## 1.2.0 — 2026-10-11

- **GET ALL EFFECTS…**: one click downloads the official Line 6 app and installs the effect list, so every block
  can change model. You can still pick an `.apk` (or `.xapk`) you downloaded yourself.
- HD models (HD amps, drives, delays and reverbs) get the app's **HD** badge in the signal chain, the editor
  header and the model picker.

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
