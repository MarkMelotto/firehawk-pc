# Changelog

## 1.3.0 — 2026-10-17

### New
- **Save tones to the amp** with **SAVE** (or Ctrl+S): keep or change the name and pick the preset. The dialog
  starts at the current preset. The preset that gets overwritten is backed up first
  (`%LOCALAPPDATA%\FirehawkRemote\backups`).
- **Footswitch panel** at the bottom: click FS1–FS5 to choose which block that FBV switch turns on and off.
  Its light shows whether the block is on; click the light to switch the block on or off. Blocks on a
  footswitch show "FS2" (etc.) on their tile. Save the tone to keep the assignment.
- **Type exact values**: click the value on the right of a slider (e.g. "545 ms") or right-click the slider,
  then type e.g. `375`, `1.2k` or `-6` (Enter applies, Esc cancels). Dragging from the value still slides.
  Note values and other step sliders stay sliders.
- **Tempo Sync** switch on delays and modulations, like the phone app: on uses the note value, off the time in
  ms (or speed in Hz).
- **HD** badge on HD models in the signal chain, the editor and the model picker.
- Hardware tones grouped by bank like on the amp: **Bank 1** A–D … **Bank 32**; presets are labelled 1A–32D.
- On PyPI: `pip install firehawk-remote`, then run `firehawk-remote` (app) or `firehawk-cli`.
- Command line: `firehawk-cli.exe save 3C --name "My Tone"` and `firehawk-cli.exe dump 3C`.

### Improved
- Values are shown like in the phone app: knobs in %, EQ frequencies in Hz/kHz on the app's frequency curves,
  gains and thresholds in dB, delay time in ms, modulation speed in Hz, Smart Harmony key/shift/scale by name,
  and every effect's controls in the app's order (the EQ now shows all four gains). This comes from the
  official app: click **GET ALL EFFECTS…** once more after updating.
- Much faster: the signal chain and block settings are read in one go, and slider moves go out immediately
  (the amp no longer trails behind through older values).
- Delay note values run from a whole note down to 1/32.
- Connecting to an amp that is off or out of range gives up after a few seconds with a clear message and can be
  retried straight away; a lost connection is shown, and closing the app never hangs. CONNECT becomes
  DISCONNECT while connected.
- The previous run's log is kept as `firehawk-debug.previous.log`.

### Fixed
- The amp could stop responding to the floorboard after saving or switching presets while connected; the app
  now follows the phone app's sync steps (reload after saving, read the tone after each preset change).
- Moving the delay time did nothing while the delay was synced to a note.
- Large messages (such as a saved preset) respect the amp's 512-byte receive window.

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
