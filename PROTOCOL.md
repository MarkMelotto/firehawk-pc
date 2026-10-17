# Line 6 Firehawk FX — Bluetooth protocol

Reverse-engineered with [REA](https://github.com/morluto/rea) and Ghidra from `com.line6.firehawk` (`firehawk-s25.apk`) with Ghidra 11.3.2:
- `lib/armeabi-v7a/libAmplifiRemoteNdk.so` is the original Line 6 library. It is stripped but keeps its C++ dynamic symbols.
- `lib/arm64-v8a/libAmplifiRemoteNdk.so` is a third-party "S25" shim. See the end of this file.

Validated on a real **Firehawk 1500** (firmware 0x01300000, symbol table = `defaultSymbolTable_Firebird.bin`) on 2026-10-11. Preset list, preset switching, knob get/set, block state, and patch events all work.

### Corrections from real-hardware testing
- Service mode words: **0 = updater, 1 = librarian, 2 = device-info**. Only the Librarian port switches mode, and it sends **1**. The Editor port sends no mode word. Mode 2 makes the amp stream ~4 KB of 200-byte device-info records (name + serial).
- The stream peer only takes new credit from packets flagged DATA or WND (0x04/0x08), never from keepalives. Send a WND packet as received data is consumed, or the peer stops after 4 KB.
- Firehawk = the "AEON" family (`IsAeonDevice`: products 0x200004/0x200006). The slot map is fx1–fx4 → FX1–FX4, reverb → **FX5**, looper → Looper, with `FX5Enable` and friends on GuitarProc.
- Turning the amp's physical knobs produced no param events. Preset changes on the amp send editor event 3 `{int presetIdx, int 0}`, followed by event 6 (FBV status).
- Floorboard / footswitch on-off changes arrive unsolicited as editor event 4 `{GuitarProc, FXnEnable, float 0.0|1.0}` (reverb = FX5Enable). No registration is needed.
- `LoadDspModel` (cmd 8) swaps models live: amp (e.g. Tread Dual → HD Blackface Double Vib) and FX slots (Screamer → HD Facial Fuzz). The amp then loads that model's default parameters.
- Cab and mic are one symbol: `<cab>` + `57OnAxis | 57OffAxis | 421 | U67` (mic index 0–3, `CabModelAndMicToStudioCabSymbol`).
- Picker groups come from the app's `assets/*Catalog.json` (AmpCatalog: Clean / American / British / High Gain; FXCatalog: Drives & Dynamics / Mods / Delays / Filters, Synths, and Pitch; Cab / Reverb / Wah catalogs).

## Layer 0 — Bluetooth Classic SPP

RFCOMM to UUID `00001101-0000-1000-8000-00805f9b34fb` (insecure). The app reads in 64-byte chunks.

## Layer 1 — IAP sync (`AndroidSppMsgChannel::execTasks`, 200 ms tick)

```
"IAP x OK" → 5 ticks → "IAP x START" → 5 ticks → RSMC running        close: "IAP x STOP"
```

## Layer 2 — RSMC, the RobustSerialMsgChannel

| off | size | field |
|---|---|---|
| 0 | 2 | `55 55` |
| 2 | 1 | 0 |
| 3 | 1 | seq of this packet (ack-only packets repeat the last seq) |
| 4 | 1 | ack: last in-order seq delivered from the peer |
| 5 | 1 | ack + free rx slots (window 8) = highest seq the peer may send |
| 6 | 2 | payload length (padded to 4) |
| 8 | 2 | CRC16(payload), 0 if empty |
| 10 | 2 | CRC16(header with this field = 0) |

- CRC16 is CCITT-FALSE: poly 0x1021, init 0xFFFF, stored LE (`CRC16_Process`).
- An ack-only header is sent on every 200 ms tick. Unacked data is retransmitted after 1000 ms.
- Max frame size is 0xF0 (firmware ≥ 2.1), so the max payload is 0xE4.

## Layer 3 — BSP transport + MsgPort_Stream

Transport header, 8 bytes LE (`MsgTransportHeader_Pack`):

```
w0 = type<<28 | 2<<26 | len     type 1 data, 2 connect; len excludes this header
w1 = srcPort | dstPort<<16
type 2 only: w = MTU<<16 (0xE4)
```

Stream header, 8 bytes LE:

```
wA = flags<<24 | seq<<8         SYN 01, FIN 02, DATA 04, WND 08, KEEPALIVE 10, HB-enable 20
wB = credit                      cumulative bytes the peer may send (rx window 0x1000 + bytes consumed)
```

- The first packet is `SYN|HB` (0x21) with seq 1. Every later packet must have seq = last+1, or the receiver aborts.
- When idle, send a KEEPALIVE every 1 s. The peer aborts after 4 s of silence.
- Ports (`L6AndroidNdkDevice::connectWithObjHelper`):

| service | local | device | mode word |
|---|---|---|---|
| Editor | 0x2002 | 0x03ED | – (none) |
| Librarian | 0x2003 | 0x03EF | 1 |

Librarian only: after connecting, write the mode as u32. The device echoes it (`L6BspMsgPort_Amplifi::changeMode`).

## Layer 4 — ToneMatch RPC

```
request : {cmd u32, argLen u32, args}
incoming: {type u32, len u32, body}   type 1 = response (FIFO); editor events 2 DSP, 3 patch,
                                      4 param, 5 tap tempo, 6 FBV, 7 PSKey, 8 sys setting
```

Handshake, both services: `1 GetVersion`, `2 GetSymbolTableSize`, `3 GetSymbolTable`, then `4 Handshake(zlib.crc32(table))` with status ≥ 0. The app tries a cached table first (`assets/defaultSymbolTable.bin`).

Symbol table: `{count, strBytes}`, then `count × {strOff, crc32(name), len}`, then the strings. **Commands use the symbol's index.**

Typed value: `{type u32, value}` with 2 = int, 3 = bool, 4 = float32.

| Editor cmd | args | response |
|---|---|---|
| 7 QueryCurrentDspModel | slot | status, modelIdx |
| 8 LoadDspModel | slot, model | status |
| 9 GetDspModelParam | slot, param | status, typedValue |
| 10 SetDspModelParam | slot, param, typedValue | status |
| 0x0B / 0x0C (Un)RegisterDspEvent | slot, event | status |
| 0x0E SetGlobalTempo | float | status |
| 0x11/0x12 Get/SetPersistentSysParam, 0x13/0x14 PSKey, 0x15/0x16 SystemSetting | | |

| Librarian cmd | args | response |
|---|---|---|
| 5 GetNumPresets | – | count |
| 6 GetPresetName | idx | status, len, name (variable length on Firehawk 1500) |
| 7 GetPresetUUID / 8 Checksum / 10 FileSize / 11 GetPresetFile | idx | status, … |
| 0x0B GetPresetFile | idx (0xFFFFFFFF = current edit buffer) | status, file bytes |
| 0x0C SetPresetFile (0x0F = variant) | idx, size, file bytes | status |
| 0x0D LoadHardwarePreset | idx (0-based) | status |
| 0x0E GetPresetInfo | idx | status, 0x58 bytes |

App groups map to DSP slots (`GroupIDToSlotSymbol`, non-AEON):

| group | slot |
|---|---|
| gate | Gate |
| volume | VolumePedal |
| wah | Wah |
| fx1 / fx2 / fx3 | FX1 / FX2 / FX3 |
| reverb | **FX4** |
| amp | Amp |
| cab | StudioCab |
| compressor | PostComp |
| eq | PostEQ |
| cab/Level | Air |

`@` params are on slot `GuitarProc`: `FXnEnable`, `FXnPost`, `FXnMix`, `FXnMixType`, `ChannelVolume`, and so on (see `device.py`).

Parameter names and ranges per model are in `assets/*.models` (JSON). Knob values are normalised to 0..1.

### Stream flow control

The amp's stream credit is `512 + bytes it has received` (window 512). The sender must not run past the peer's
latest credit (taken from SYN/DATA/WND packets); the amp sends WND packets as it consumes data. Needed for
anything larger than 512 bytes, such as a preset upload.

### Preset files (`L6SPePresetSerializer`, `L6A36Preset::SerializeToDeviceForGroup`)

Flat stream of `{key u32, type u16, value}`; types 0 none, 1 bool, 2 int, 3 float, 4 string `{len incl. NUL,
bytes}`, 6 array `{count, count×u32}`, 7 symbol index. Directive keys: `0x80000000` section start (1 DSP, 2 PS),
`…01` section version, `…02` section end, `…03` slot start (symbol), `…04` slot end, `…05` model (symbol),
`…06` param count, followed by that many `{"<Param>State" symbol: value}`.

- PS section: key 0 = name (≤ 63 bytes), plus tempo, footswitch and other metadata.
- DSP section: root slot `GuitarProc` (its params: `FX1EnableState`, `ChannelVolumeState`, …) containing the
  slots Gate, VolumePedal, Wah, FX1–FX5 (`FXnStereo` when stereo), Amp, Air, StudioCab (cab+mic symbol),
  LiveCab (the cab's `cablink`), PostComp, PostEQ, Looper.
- Saving the edit buffer (app: `savePresetToDevice`): build the file and `SetPresetFile(idx)`. This app asks the
  amp to serialise its edit buffer (`GetPresetFile(0xFFFFFFFF)`), sets the name and uploads it.
- **Don't** upload a preset rebuilt from editor values (stored file with models swapped and `…State` lists rebuilt
  from the app's model data): the amp accepts it, but hung when switching presets afterwards and needed a restart.
- A `SetPresetFile` cut off half-way (app killed mid-upload) can leave the librarian waiting for the rest; switch
  the amp off and on.

### Tuner (`A36EditorLibrarian::engageTunerInternal`)

- Engage: `SetDspModelParam(Tuner, Enable, bool true)`, then `RegisterDspEvent(Tuner, Note)` (cmd 0x0B).
  Disengage: `Enable = false`, then cmd 0x0C.
- Readings arrive as type-2 DSP events `{Tuner, Note, float}`. The float is semitones above A, 0 ≤ v < 12
  (out of range = no signal). `convertA36TunerValueToNoteCents`: f = 440·2^(v/12) (v ≥ 11.5 wraps to v − 12),
  nearest note, cents = 1200·log2(f/f_note); i.e. note = round(v) mod 12 (0 = A, 1 = B♭ … 11 = A♭),
  cents = (v − round(v))·100.
- The amp's tuner footswitch sends a type-4 param event `Tuner/Enable`; the app then registers for `Note`
  without setting `Enable` itself. On connect the app reads `Tuner/Enable` to pick up a tuner that is already on.
- UI (`TunerFragment`): 11-segment bar, segment 5 green when within ±2 cents, others red, per `CENT_SEGMENT`.

## About the S25 arm64 shim

This is a ~1 kLoC C reimplementation that lets the app run on 64-bit-only phones. It:
- replays the captured SYN and mode frames (the source of the validation bytes above)
- opens **only** the Librarian
- builds GetPresetInfo frames with the stream seq set to the preset index and a counter in the CRC field, so most requests are likely dropped (hence its "Preset N" fallback)
- stubs every write function (`nativeSetEditBufferParam` and the rest return 0)
- serves a hard-coded default preset for the edit buffer

It also parses an unsolicited `"PSRP"` message (preset name plus 10-byte `{id u32, type u16, value}` records), which isn't in the original library. Check `watch -v` logs for it.
