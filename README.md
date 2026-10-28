# Mooer Prime M2 for Mac (unofficial)

A native macOS app to control the **Mooer Prime M2** multi-effects pedal over
**USB** (preferred) or **Bluetooth LE**, plus a documented description of the
pedal's protocol.

Not affiliated with or endorsed by Mooer Audio. Everything here was worked
out by observing the official apps and the pedal. Use at your own risk, and
read the [warning](docs/PROTOCOL.md) about command `0x81` before
experimenting.

## What it does

- Connects automatically when the M2 is plugged in by USB. Without USB, pick
  `M2Prime M2` from the Bluetooth list.
- Lists all 80 presets and selects them with a single click. It follows
  preset and tempo changes made on the pedal.
- Edits all 9 effect slots of a preset:
  - adds any effect from all 11 categories (Guitar Sim, Drive, Pre-Amp, Cab,
    Entire Amp, Dyna, Filter, EQ, Mod, Delay, Reverb) and all 277 models,
  - changes a slot's category or model, and removes effects,
  - turns effects on and off,
  - edits every parameter live with sliders that use the official app's
    ranges and units (dB, Hz, ms, semitones),
  - syncs Delay Time and Mod Rate to the tempo (1/32 … 1/1, dotted, triplet).
- Saves the edited preset under any name to any of the 80 slots.
- Shows a warning when the pedal's DSP is full.
- "Copy raw dump" copies every preset's raw values for analysis.

## Requirements

- macOS 13 or later.
- Swift 5.9+. The Xcode **Command Line Tools** are enough; Xcode.app isn't
  needed.

## Build and run

```bash
swift build
./scripts/make_app.sh debug
open .build/debug/MooerPrimeMac.app
```

Use `swift build -c release` and `./scripts/make_app.sh release` for a
release build.

The app has to run from the `.app` bundle: macOS only honours the
Bluetooth permission text (`NSBluetoothAlwaysUsageDescription`) from a real
bundle, and the bare executable is killed on its first Bluetooth access.
macOS asks for Bluetooth permission on the first launch.

The code deliberately avoids SwiftUI's `@State` and `@Observable` and uses
`ObservableObject` classes with `@StateObject` instead. With only the
Command Line Tools installed, `@State` fails to compile
(`plugin for module 'SwiftUIMacros' not found`), because that compiler
plugin ships only with Xcode.app. Keep this pattern when adding views.

## Project layout

| Path | Contents |
|---|---|
| `Sources/MooerPrimeMac/PrimeFrame.swift` | Frame encoding and decoding (`aa 55`, length, CRC-16/GSM) |
| `Sources/MooerPrimeMac/PrimeM2Protocol.swift` | `PrimeM2Device`: the protocol session over either link. Sync, request queue, preset select/read, module edit/add/change/delete, save, polling |
| `Sources/MooerPrimeMac/PrimeUSBDevice.swift` | USB HID link (IOKit). Also `CloudPreset`, the whole-preset read/write format (not used by the UI) |
| `Sources/MooerPrimeMac/BluetoothManager.swift` | CoreBluetooth link and the app's log |
| `Sources/MooerPrimeMac/Models.swift` | Preset and module structs, wire-exact |
| `Sources/MooerPrimeMac/ModuleCatalog.swift` | Categories, model names, parameter labels, ranges and the tempo-sync table |
| `Sources/MooerPrimeMac/*View.swift` | SwiftUI: device list and the effects chain editor |
| `scripts/make_app.sh` | Packages the executable as a `.app` |
| `docs/` | Protocol documentation (below) |

## Documentation

- **[docs/PROTOCOL.md](docs/PROTOCOL.md)**: the protocol reference. It covers
  the BLE and USB transports, framing and CRC, every known command, the
  preset and module formats, value encodings and ranges, the tempo-sync
  table, editing semantics, the whole-preset transfer format and the open
  questions.
- **[docs/MODELS.md](docs/MODELS.md)**: all 277 model names by category,
  with the app numbers and wire values.
- **[docs/RESEARCH-NOTES.md](docs/RESEARCH-NOTES.md)**: the chronological
  research log, including the dead ends.
- **[docs/CAPTURE_RUNBOOK.md](docs/CAPTURE_RUNBOOK.md)**: how the Android
  Bluetooth capture was made (historical).

### Key findings in short

- The M2 uses a serial-style protocol: `aa 55 | len16 LE | payload | CRC-16/GSM BE`.
  The same frames work over BLE (write `FFF3`, notify `FFF2`) and USB HID
  (VID `0x34DB`, PID 13).
- A reply's opcode is the request's + 1. Presets are 1…80. A module record
  is `slot type n words…`: the words are on/off, model, then parameters.
- `2d id op slot type model` adds (op 1), changes (op 2) or deletes (op 3) a
  module, which the official Android app can't do. A full DSP still answers
  "ok", so always read the slot back.
- Over USB the pedal pushes nothing but a heartbeat, so the app polls.
  After a preset select the pedal ignores requests for about 200 ms.
- Mooer Studio's whole-preset backup format has been decoded: 1198 bytes in
  3 frames, with a CRC at 1196. It can be written to an audition slot first.

## Status

Tested on a Prime M2 with firmware V1.5.0. The looper, drum machine,
tuner, MIDI, global settings and the user IR / sample slots aren't
supported yet.

## License

[MIT](LICENSE). Mooer and Prime are trademarks of their respective owner.
