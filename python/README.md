# Mooer Prime M2 controller in Python (unofficial)

A Python port of the [Swift Mac app](../README.md): a desktop app that
controls the **Mooer Prime M2** multi-effects pedal over **USB** (preferred)
or **Bluetooth LE**. It has the same features as the Swift app. It uses
[PySide6](https://doc.qt.io/qtforpython-6/) (Qt) for the window,
[bleak](https://github.com/hbldh/bleak) for Bluetooth and
[hidapi](https://github.com/trezor/cython-hidapi) for USB, so it should also
run on Windows and Linux. It has only been tested on macOS.

Not affiliated with or endorsed by Mooer Audio. Use at your own risk, and
read the protocol notes in [`docs/PROTOCOL.md`](../docs/PROTOCOL.md)
(especially the warning about command `0x81`) before experimenting.

## What it does

- Connects automatically when the M2 is plugged in by USB. Without USB, pick
  `M2Prime M2` from the Bluetooth list.
- Lists all 80 presets and selects them with a single click. It follows
  preset and tempo changes made on the pedal.
- Edits all 9 effect slots of a preset:
  - adds any effect from all 11 categories and all 277 models,
  - changes a slot's category or model, and removes effects,
  - turns effects on and off,
  - edits every parameter live, with sliders that use the official app's
    ranges and units (dB, Hz, ms, semitones),
  - syncs Delay Time and Mod Rate to the tempo (1/32 … 1/1, dotted, triplet).
- Saves the edited preset under any name to any of the 80 slots.
- Shows a warning when the pedal's DSP is full.
- "Copy raw dump" copies every preset's raw values to the clipboard.

The log goes to standard output, so start the app from a terminal to see it.

## Requirements

- Python **3.10 or later**. On macOS, the system `/usr/bin/python3` (3.9)
  is too old. Use Homebrew (`brew install python`) or python.org.
- macOS 11 or later, Windows 10 or later, or Linux with BlueZ.
- On Linux, USB access needs a udev rule for VID `34db`, PID `000d`.

## Run it from a virtual environment

```bash
cd python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m mooer_prime
```

On Windows, activate the venv with `.venv\Scripts\activate` instead.

To run it again later, activate the venv and start the app:

```bash
source .venv/bin/activate
python -m mooer_prime
```

You can also run it without activating the venv:

```bash
.venv/bin/python -m mooer_prime
```

**Bluetooth permission on macOS:** when the app runs from a terminal,
macOS asks for Bluetooth access on behalf of the terminal app (Terminal,
iTerm, VS Code …). Allow it, or turn it on under System Settings → Privacy &
Security → Bluetooth. USB needs no permission.

## Build a standalone app with PyInstaller

The script creates the venv if needed, installs the requirements and
PyInstaller, and builds:

```bash
./scripts/build_app.sh
```

It does the same as these steps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean MooerPrime.spec
```

What you get:

- **macOS:** `dist/Mooer Prime.app`. Open it with
  `open "dist/Mooer Prime.app"` or copy it to `/Applications`. The spec
  writes `NSBluetoothAlwaysUsageDescription` into the bundle's
  `Info.plist`. macOS needs that entry, or it kills the app on its first
  Bluetooth access, so use the spec file rather than a bare
  `pyinstaller run.py`. The app isn't signed. If Gatekeeper blocks a copy
  you moved to another Mac, right-click it and choose **Open**.
- **Windows / Linux:** a single executable, `dist/MooerPrime.exe` or
  `dist/MooerPrime`.

PyInstaller can't cross-compile, so build on the OS you want to run on. The
app is built for the architecture of the Python you build with (Apple
silicon or Intel).

## Tests

The protocol tests run against a simulated pedal, so they need no hardware:

```bash
.venv/bin/python -m unittest discover -s tests
```

### Testing on the pedal

`tools/hwtest.py` tests the app's protocol code against a real M2 plugged in
by USB, without opening a window. It runs one step per call, so you can stop
between writes. It starts by backing up every preset and ends by checking
that all 80 stored presets match that backup byte for byte.

> ⚠️ Most steps write to the pedal. Close the app, Mooer Studio and the
> Swift app first, since only one program can use the USB connection. Never
> skip the backup, and run `restore` for every slot you saved to.

Read-only steps:

```bash
.venv/bin/python tools/hwtest.py backup
```

```bash
.venv/bin/python tools/hwtest.py state
```

`backup` reads all 80 presets in Mooer Studio's whole-preset format and
stores them as `.preset` files in a `hwtest` folder next to the app's
backups (on macOS `~/Library/Application Support/MooerPrimeMac/hwtest`). It
refuses to overwrite an existing backup. Use `HW_BACKUP=<folder>` for a new
one. `state` shows the active preset and whether it has unsaved edits. If
it does, save them on the pedal first, because the test steps discard them.

These steps change only the active preset's edit buffer and undo every
change. Afterwards, `reselect` selects the active preset again and checks
that it equals the backup:

```bash
.venv/bin/python tools/hwtest.py edits
```

```bash
.venv/bin/python tools/hwtest.py chain
```

```bash
.venv/bin/python tools/hwtest.py reselect
```

- `edits` switches the first effect off and on, nudges its first parameter,
  simulates a fast slider drag, and switches a Delay's Time between
  tempo-sync and a free time.
- `chain` adds a Reverb to the next free slot, changes its model, and
  removes it again.

`save` overwrites a stored slot, so pick one you can restore, here 80 (19D),
while 11 (2C) is the preset you started on. Select the start preset again
before restoring the slot, then play the backup on the audition slot (which
stores nothing) before writing the slot itself:

```bash
.venv/bin/python tools/hwtest.py save 80 PyTest
```

```bash
.venv/bin/python tools/hwtest.py reselect 11
```

```bash
.venv/bin/python tools/hwtest.py audition 80
```

```bash
.venv/bin/python tools/hwtest.py restore 80
```

Finally, compare everything with the backup. The restore leaves the
restored slot selected, so select your preset again:

```bash
.venv/bin/python tools/hwtest.py verify
```

```bash
.venv/bin/python tools/hwtest.py reselect 11
```

Each check prints `PASS` or `FAIL`, and a failing step stops right there.
Set `HW_VERBOSE=1` to see the protocol log. Slot numbers are 1…80
(0A = 1, 2C = 11, 19D = 80).

## Project layout

| Path | Contents | Swift counterpart |
|---|---|---|
| `mooer_prime/frame.py` | Frame encoding and decoding (`aa 55`, length, CRC-16/GSM), stream reassembly | `PrimeFrame.swift` |
| `mooer_prime/models.py` | Preset and module structs, wire-exact | `Models.swift` |
| `mooer_prime/catalog.py` | Categories, model names, parameter labels, ranges and the tempo-sync table | `ModuleCatalog.swift` |
| `mooer_prime/protocol.py` | `PrimeM2Device`: the protocol session over either link (Qt-free) | `PrimeM2Protocol.swift` |
| `mooer_prime/usb.py` | USB HID link (hidapi) and `CloudPreset`, the whole-preset format (not used by the UI) | `PrimeUSBDevice.swift` |
| `mooer_prime/bluetooth.py` | Bluetooth LE link (bleak, on its own asyncio thread) | `BluetoothManager.swift` |
| `mooer_prime/app.py` | Qt window: device list, preset list, effects chain editor, save bar | `*View.swift`, `MooerPrimeMacApp.swift` |
| `MooerPrime.spec`, `scripts/build_app.sh` | PyInstaller build | `scripts/make_app.sh` |
| `tests/` | Protocol tests with a simulated pedal | — |
| `tools/hwtest.py` | Step-by-step test against a real pedal over USB | — |

### Differences from the Swift app

- hidapi has no hot-plug callbacks, so the app checks for the USB device
  once a second, and a reader thread receives the pedal's reports.
- Bluetooth runs bleak on its own asyncio thread. All protocol state is
  changed only on the GUI thread, as the Swift app does on the main queue.
- The protocol session gets its timers through a small scheduler interface
  instead of `DispatchQueue`. This is what lets the tests run on a virtual
  clock.

## License

[MIT](../LICENSE). Mooer and Prime are trademarks of their respective owner.
