# Research notes — Mooer Prime M2

> Chronological lab notebook, kept as written, so earlier sections contain
> guesses that later sections correct. For the current, consolidated
> reference see [PROTOCOL.md](PROTOCOL.md).

Source: `Prime_4.0.0_apkcube.apks` (v4.0.0, package `com.mooer.prime`),
Android App Bundle for the "Prime" companion app covering multiple Mooer
pedals (P1, P1+S1, P2, M-series incl. M2).

## Confirmed (static analysis)

- **Flutter app**, not native Android. Business logic is Dart, AOT-compiled
  to `lib/arm64-v8a/libapp.so` inside `split_config.arm64_v8a.apk`. The only
  Android-native code (`com.mooer.mooer_ai.*`) is for an unrelated AI
  stem-separation/jam feature (FFmpeg + ONNX + a Demucs model) — irrelevant
  to pedal control, out of scope.
- **BLE via `flutter_blue_plus`** only (confirmed in `GeneratedPluginRegistrant.java`
  and the Dart string pool) — standard GATT, not classic SPP. Manifest only
  declares `BLUETOOTH_SCAN`/`BLUETOOTH_CONNECT` + `bluetooth_le` feature.
- Dart code is organized per device family under `package:prime_series/`:
  `p1/`, `p1s1/`, `p2/` (plus shared `ble/`, `common/ble_parse_helper.dart`).
  **No separate `m2` code module** exists — only `assets/m2/...` images —
  so the M2 almost certainly reuses `p2`'s logic (richest feature set: MIDI,
  tempo sync, looper w/ recording, drum machine, tuner, cloud presets —
  consistent with a flagship floor multi-fx unit), with device identity/UI
  skin picked at runtime from what the device reports.
- Release build is **not obfuscated**: class/method names (`PrimeBleHelper`,
  `BleDispatcher`, `FxManager`, `DeviceManager`, …) and **every UI string**
  (effect names, parameter labels, help text) are recoverable verbatim from
  the Dart string pool (`strings` on `libapp.so`).
- `inspect_dart_aot` (REA) confirms the ELF only exports the 4 big snapshot
  blob symbols — no per-function symbols, so there's no decompiler path to
  the actual command-encoding logic. GATT UUIDs and command bytes are
  almost certainly integer literals baked into AOT machine code, not
  strings — **not recoverable as plain text**.
- Effect-chain concept confirmed from UI strings: ordered modules, each with
  on/off + parameters. Seen category/module name fragments: Noise Gate,
  Comp(Low-Freq), Drive, D-Drive, GNR Drive Pedals, Automatic Cabinet
  Simulation, Cab (Hi-Freq), Cab Sim, Cabinet Style, 3/5 Bands EQ, Custom EQ,
  Global EQ, Ana Chorus, Auto Wah, Funky Wah, Jet Flanger, Delay/Delay Tap,
  DI Output, "DSP limit reached. Disable some effects and try again."
  — plus Looper (with recording), Drum Machine, Metronome, Tuner, MIDI
  Learning/Settings, wireless footswitch + expression-pedal pairing
  (the M2 itself pairs with separate BLE accessories, configured through
  the phone app).
- Preset/patch model exists (`generated/json/preset_entity.g.dart` et al.,
  `PresetEntity`, `CloudPresetModel`, `CollectionPresetEntity`) — presets
  have cloud sync, but local save/select/patch-switch is clearly also a
  core on-device feature.

## Confirmed (live, Mac app connected to a real Prime M2 — 2026-10-27)

Connected from `MooerPrimeMac` (GATT Inspector tab) to the physical pedal.

- **Advertised name: `M2Prime M2`** (the CoreBluetooth peripheral id is per-Mac, not a stable device ID).
  Connects without any pairing/bonding prompt.
- **One custom service: `FFF0`** (the only service CoreBluetooth lists;
  standard GAP/GATT services are hidden by macOS). Characteristics:

  | Char   | Handle | Properties    | Role (confirmed by HCI capture)          |
  |--------|--------|---------------|------------------------------------------|
  | `FFF1` | 0x0005 | write, notify | **unused** by the official app           |
  | `FFF2` | 0x0008 | read, notify  | **all device→host frames** (CCCD 0x0009) |
  | `FFF3` | 0x000b | write         | **all host→device frames** (write req)   |

- The 16-bit `FFF0`/`FFF1`/`FFF2`/`FFF3` layout is the stock layout of cheap
  serial-over-BLE ("transparent UART") modules, which suggests the pedal's
  DSP talks a plain byte-stream protocol tunnelled through a BLE bridge
  rather than a GATT-per-parameter design. Expect framed messages
  (header/length/checksum) on the write characteristics and replies via
  notify. *Inference — not yet confirmed by traffic.*
- Our app already subscribes to notify on `FFF1` and `FFF2` after discovery.
  As of the screenshot no notification had arrived, i.e. the pedal does not
  spontaneously stream state just because a central subscribed — it likely
  waits for a handshake/request command from the app.
- `FFF2` reads back empty on connect.

### First traffic: unsolicited preset-state frames on `FFF2` (2026-10-27)

Selecting a preset **on the pedal** (no command sent from the Mac) pushes a
notification on `FFF2`. Two captures, presets "0A" and "0B":

```
0A: aa 55 63 00 15 01 01 18 18 18 18 09 ff 4d 32 50 72 69 6d 65 20 4d 32 00 …
    … 32 32 32 32 01 01 01 01 03 01 c2 a2
0B: identical except byte 5 = 02 and CRC = 2a 3b
```

Full 0A frame with offsets:
```
  0 aa 55 63 00 15 01 01 18 18 18 18 09 ff 4d 32 50
 16 72 69 6d 65 20 4d 32 00 00 00 00 00 00 00 00 00
 32 00 00 3b 00 3b 00 3b 00 00 3c 3c 00 3c 78 00 00
 48 09 01 01 3c 01 00 ff 01 3c 01 00 46 00 14 00 01
 64 00 18 00 18 00 18 00 01 00 28 00 32 00 32 00 19
 80 00 06 08 07 00 00 00 00 00 00 00 00 00 32 32 32
 96 32 01 01 01 01 03 01 c2 a2
```

**Frame format (confirmed — the CRC checks out on both frames):**

| Offset   | Bytes      | Meaning                                                    |
|----------|------------|------------------------------------------------------------|
| 0–1      | `aa 55`    | sync header                                                |
| 2–3      | `63 00`    | payload length, **little-endian** (0x63 = 99 = bytes 4…102) |
| 4…n-3    | payload    | 99 bytes here                                              |
| n-2…n-1  | CRC        | **big-endian** CRC-16                                      |

CRC = poly `0x1021`, init `0x0000`, no reflection, xorout `0xFFFF`
(= CRC-16/GSM), computed over **bytes 2…n-3, i.e. the length field plus
the payload** (not the `aa 55`). Found by brute force: the two frames differ
in one payload byte, which pins the polynomial; init/xorout/range then came
out as the only "clean" combination. Proven only on these two frames, and
only for messages from the pedal — assume, but verify, that commands to the
pedal use the same framing.

**Payload (tentative, offsets are absolute in the frame):**
- `[4] = 0x15` — message type/opcode ("preset/patch dump"?).
- `[5]` — preset index: `01` = 0A, `02` = 0B. So it's either 1-based, or
  slot 0 is something else. Need more presets (0C, 1A, …) to get the
  bank/slot mapping.
- `[13..32]` — 20-byte NUL-padded ASCII name: `"M2Prime M2"`. The same on
  both presets, so it's probably the factory default patch name (or the
  device name). Renaming a preset would tell us which.
- `[33..102]` — the rest is identical between 0A and 0B (they're presumably
  both factory-default patches), so these are the module/parameter values.
  Many values look like 0–100 knobs (`0x32` = 50, `0x3c` = 60, `0x46` = 70,
  `0x28` = 40, `0x19` = 25, `0x14` = 20, `0x78` = 120 — maybe a BPM/time), and
  some pairs like `18 00`, `3b 00`, `32 00` look like LE uint16 fields. The
  layout is still unmapped.

**More presets (0C, 2D) — CRC confirmed on all 4 frames:**

| Preset | `[5]` | `[45]`          | CRC     |
|--------|-------|-----------------|---------|
| 0A     | `01`  | `78` (120)      | `c2 a2` |
| 0B     | `02`  | `78` (120)      | `2a 3b` |
| 0C     | `03`  | `61` (97)       | `fb 67` |
| 2D     | `0c`  | `61` (97)       | `81 f9` |

- **Slot index `[5]` = bank × 4 + slot + 1**, where slot A–D = 0–3
  (0A = 1, 0C = 3, 2D = 2·4+3+1 = 12 ✓). 1-based, 4 slots per bank.
- `[45]` is the only other byte that changes. 120 vs 97 looks like a
  **tempo in BPM** (120 is the usual default). It's a guess, but tempo is
  per-preset on most Mooer units.
- **This frame is probably not a full patch dump.** Factory presets on 0A,
  0C and 2D would have very different effect chains, yet ~70 of the 99
  payload bytes are identical across all four. More likely `0x15` is a
  **"current state / preset changed" status frame**: device name (`"M2Prime M2"` is the
  device name, not a patch name), the active slot, tempo, and global
  settings (global EQ, levels, footswitch/MIDI config…). The real patch
  contents probably have to be requested with a command, which only the
  Android capture will show.

**Limits of pedal-side probing (confirmed with the user):** the M2's only
hardware controls are preset selectors. Parameters can't be edited and
presets can't be renamed on the device, so the only things it can push
unprompted are preset changes. Everything else (module on/off, parameters,
patch read/write, save) needs the **Android HCI snoop capture**
([CAPTURE_RUNBOOK.md](CAPTURE_RUNBOOK.md) step 2), or careful probing by sending
our own `aa 55 … CRC` frames to `FFF1`/`FFF3`.

The scratch scripts for the CRC search (`dec.py`, `bf.py`, `init.py`) are in
a scratch folder (not in this repo). They're throwaway; the algorithm above is all
that's needed.

### Probe tooling (2026-10-27)

The Mac app now has a **Frame Sender** tab (`PrimeFrame.swift`,
`FrameSenderView.swift`). It wraps a hex payload as `aa 55 len … CRC` and
writes it to `FFF1`/`FFF3` (with response when the characteristic supports it, so
ATT errors are logged). Every incoming notification that parses as a frame
gets a `↳ frame len=… crc ok type=0x.. preset NX` line in the log. The
encoder round-trips the captured 0A/2D frames byte-for-byte. Example: payload
`15` encodes to `aa 55 01 00 15 8a 5b`.

**Probe result (2026-10-27): no observable reaction.** The user sent the
guess payloads (`15`, `15 01`, `15 03`) to both `FFF1` and `FFF3`, framed and
unframed: no notification, no preset change. So either `0x15` is a
device→host-only type, or commands need a different opcode, a handshake
first, or a different frame layout for host→device traffic. We're not
guessing blindly any further; the next step is the Android HCI snoop capture.
(We didn't record whether each write was ATT-confirmed or returned an error;
check that in the log if probing again.)

### Not yet checked
- ~~Whether preset switching on the pedal pushes notifications~~ — it does
  (see above). The pedal has no other pedal-side controls to test.
- ~~FFF1 write type / descriptors~~ — answered by the HCI capture (FFF1 is
  unused; the CCCD for FFF2 is handle 0x0009).

## Protocol decoded from Android HCI snoop capture (2026-10-27)

Source: `btsnoop_hci_202610272121…2148.cfa`, five
**cumulative** snapshots of the same rolling log (the 2148 file contains
everything). btsnoop timestamps, as decoded on this Mac, run **+1 h** relative
to the phone clock and filenames (22:16 in the parse = 21:16 phone time).
Parser scripts (throwaway): `snoop.py` (ATT extraction) and `frames.py`
(frame reassembly + CRC check) in a scratch folder (not in this repo) `tools/`.

### Transport
- The app negotiates MTU 517 (device answers 508), enables notify on
  `FFF2` (CCCD handle 0x0009 ← `01 00`), and from then on **writes
  every command to `FFF3` with write-request** and gets **every reply as
  notifications on `FFF2`**. `FFF1` is never touched. That explains why our
  probe got nothing: wrong characteristic for the reply path, plus `0x15` is
  a device→host-only opcode.
- Framing in **both directions** is exactly the `aa 55 | len LE | payload |
  CRC-16/GSM BE` decoded earlier. All 216 frames in the capture pass the CRC.
- Large replies are split across several notifications (≈ 244-byte chunks);
  reassemble by the length field.
- `payload[0]` is the opcode. **A reply's opcode is the request's + 1.**
  Most writes get a reply; unsolicited device frames are 0x15, 0x43, 0x97.
- Preset numbering everywhere is **1…80** (20 banks × A–D; 0A = 1).

### Connect / sync sequence (what the app sends after connecting)
```
01                       → 02  device info: 41 01 "V1.5.0" 00 08 50 0a 0a 3c 0a 0a 0a 0a
                           (+ unsolicited 97 00)
29 01 28                 → 2a  preset names 1…40   (start, count; 20-byte names each)
29 29 50                 → 2a  preset names 41…80  (start=41, end=80)
53 01 0a                 → 54  10 × "no record" entries (looper/recordings?)
65 cc 01 00 0a 00        → 66  per-category list, cc = 01…05 (all empty here)
65 07 01 00 28 00        → 66  category 07 items 1…40: "POP01".."REGGAE04"  (drum patterns)
65 07 29 00 3c 00        → 66  category 07 items 41…60: "WORLD01".."METRONOME02"
a9                       → aa  serial/ID string "iPedal…" (unit-specific)
11                       → 12  system state (same 99-byte struct as the 0x15 push)
21 pp                    → 22  full preset pp (see below)
33 pp                    → 34  61 00 = 97 → tempo (BPM, u16 LE)
55                       → 56  04  (unknown)
91  (every ~30 s)        → 92  xx 00 — battery %, falls 0x4a→0x3d (74 %→61 %) over the session
```

### System state struct (0x11 get → 0x12, 0x13 set → 0x14 01, 0x15 push)
The 99-byte struct described above under "First traffic". `[1]` (payload
index) is the active preset. **The app selects a preset by writing `13` +
the full struct with `[1]` changed.** The device acks with `14 01` and
pushes `34` (tempo). The app then re-reads `21 pp` and `33 pp`. The pedal pushes
the same struct as opcode `15` when the preset is changed on the hardware.
Payload `[41..42]` = tempo u16 LE (matches the 0x34 value).

### Preset struct (0x21 pp → 0x22)
```
pp | name[20] NUL-padded | 20 × module slot | 7 bytes trailer (zeros so far)
module slot = slot# (1…20) | type | n | data[n]      empty slot = slot# 00 00
data        = n/2 × uint16 LE: [enabled 0/1] [model] [param…]
```
Preset 1 "FX 01" (factory), annotated:
```
01 04 0c  00 00 01 00 14 00 0f 00 3c 00 46 00         type 04, off, model 1, 20 15 60 70
02 01 0a  01 00 01 00 00 00 32 00 47 00               type 01, on,  model 1, 0 50 71
03 02 10  01 00 36 00 54 00 30 00 34 00 31 00 33 00 25 00   type 02, model 54, 6 params
04 03 0a  01 00 37 00 32 00 00 00 00 00               type 03, model 55, …
05 09 22  00 00 01 00 24 00 64 00 08 00 26 00 da 02 08 00 24 00 78 05 08 00 20 00 88 13 08 00 00 00 00 00 3c 00
                                                      type 09 — contains 730/1400/5000 → EQ bands (Hz)
06 06 0c  00 00 0a 00 6f 00 1e 00 21 00 32 00
07 07 0e  01 00 01 00 1d 00 2c 00 f4 01 00 00 00 00   type 07 — 0x01f4 = 500 → delay time ms?
08 08 0c  01 00 02 00 7b 00 5e 00 58 00 12 00         type 08 — reverb?
09 05 0c  01 00 01 00 32 00 47 00 39 00 46 00
0a 00 00 … 14 00 00                                   slots 10–20 empty
```
Words can carry flags: preset "Delay 1/8d" has a delay word `0x8009`
(32777). That's probably the tempo-sync bit plus subdivision index 9 (1/8 dotted?).
Module **type → category is a guess** from typical Mooer chains (01 = FX/
comp?, 02 = amp, 03 = cab, 04 = drive/pre-FX?, 05 = mod?, 06 = ?,
07 = delay, 08 = reverb, 09 = EQ). The chain is **free-form**: up to 20 slots, any
type in any slot. The empty preset ("Empty") has all 20 slots `nn 00 00`.

### Labels (from official-app screenshots of preset 0A, 2026-10-27)

Every value in the screenshots matches the captured words. **The app displays
the model as wire value + 1.** Encoded in `swift/Sources/MooerPrimeMac/ModuleCatalog.swift`.

| Type | Category (confirmed) | 0A model (wire → UI) | Params after enabled+model                          |
|------|----------------------|----------------------|-----------------------------------------------------|
| 01   | Drive                | 1 → 02 Flex Boost    | Gain, Tone, Vol                                     |
| 02   | Pre-Amp              | 54 → 55 AMPOG SVT 4  | Gain, Bass, Mid, Treble, Presence*, Master*         |
| 03   | Cab                  | 55 → 56 SV810 U47    | Gain, Low Cut (0 = OFF), High Cut (0 = OFF)         |
| 04   | Dyna                 | 1 → 02 Yellow Comp   | Attack, Ratio, Threshold, Level                     |
| 05   | Filter               | 1 → 02 Auto Wah      | Rate, Peak, Range, Level                            |
| 06   | Mod                  | 10 → 11 Ana Chorus   | Rate (111 shown as "1/4", synced), Mix, Tone, Depth |
| 07   | Delay                | 1 → 02 Analog        | Level, Feedback, Time ms (+2 unknown words)         |
| 08   | Reverb               | 2 → 03 Plate         | Pre Delay ms, Level, Decay, Tone                    |
| 09   | EQ                   | 1 → 02 Custom EQ     | 4 × (Gain, Freq Hz, Q) + 3 unknown words (0 0 60)   |

\* from the amp faceplate artwork; the sliders were off-screen.
- EQ "Low" band: gain 36 shows as 2.0, Q 8 shows as 1.0. Fitted as
  dB = (raw − 32)/2 and Q = raw/8 from **one sample each**. Unverified.
- Tempo-synced values: Mod Rate 111 = "1/4". Delay Time `0x8009` in the
  "Delay 1/8d" preset (high bit = sync?). The note-value tables are unknown.
- Parameter labels are for the model seen in each type; other models
  (e.g. a noise gate under Dyna) may use different labels or word counts.
- More screenshots (presets 0B, 1A, 1B, 3B, 3C, 3D) added model names, keyed by
  app number (wire = number − 1):
  Drive 01 Pure Boost, 05 Gold Clon · Pre-Amp 06 US Blues OD, 61 AKUILA 751 ·
  Cab 02 US DLX 112, 03 Sonic 112, 08 US TW 212, 63 Akuila U47 ·
  Dyna 03 Noise Killer (params: Threshold, Level) · Mod 03 Flanger (params:
  Rate, Mix, Feedback) · Delay 07 PingPong · Reverb 01 Room, 04 Spring.
  1A "Delay 1/8d" (captured wire model 6) shows as "07 PingPong", which
  confirms the +1 offset a second time. Its Time `0x8009` displays as "1/8D".
- Batch 2 (46 screenshots, presets 0C–4A, 2026-10-28) added: Drive 03 Tube DR,
  04 808, 06 D-Drive · Pre-Amp 01 65 US DLX, 02 65 US TW, 11 Mark V CL, 30 J800,
  31 J900, 32 PLX 100, 56 AMPOG SVT VALVE · Cab 15 1960 412, 18 Rec 412,
  62 HK 421 · Dyna 01 Red Comp (Sensitivity, Level) · Filter 03 Q-Filter (Rate,
  Q, Mix) · Mod 09 Ring (Rate, Mix, Tone), 12 Tri Chorus · Delay 01 Digital ·
  EQ 01 5 Bands EQ (100Hz/250Hz/630Hz/1.6kHz/… gains in dB, ±0.5 steps).
- **Cab Low/High Cut are frequencies**, not just on/off: 0D SV810 U47 shows
  Low 300 Hz / High 20000 Hz, and 2A 1960 412 shows Low 60 Hz / High 8300 Hz. The raw
  encoding is unknown; the Mac app shows "raw N" until it's mapped.
- Synced delay times seen: 2A Analog 1/8, 2C Digital 1/4, 3D PingPong 1/4,
  3B Digital 1/8D (1A = `0x8009` = 1/8D). Unsynced: 3A Digital 41 ms.
- EQ gains seen: 0B Custom EQ Low 1.5, 0C 3.0, 0D 3.5, 2B 0.0. All fit the
  (raw − 32)/2 guess, which still needs the raw values to confirm.
- **Decoded from the raw dump (2026-10-28)**, matched against those screenshots:
  - Cab Low/High Cut words are **plain Hz** (0D: 300/20000, 2A: 60/8300);
    0 = OFF. Other values seen: 50, 75, 79.
  - **Tempo-sync note index** n: Delay Time = `0x8000 | n`, Mod Rate = `101 + n`.
    n = 8 → 1/8 (2A), 9 → 1/8D (1A, 3B), 10 → 1/4 (2C, 3D; Mod 111 in 0A).
    Indices 5, 6, 11, 12, 13 also occur, but their note values aren't seen yet.
  - EQ gain dB = (raw − 32)/2 is **confirmed** (35→1.5, 38→3.0, 39→3.5,
    32→0.0, 27→−2.5). EQ Freq is plain Hz. Q = raw/8 is still one sample
    (Q words 11, 19, 48, 50 also occur).
  - Delay words 4–5 (usually 50 50) and Custom EQ's last 3 words are still
    unlabelled. The Mac app's "Copy raw dump" button reproduces this dump.
- Batch 3 (34 screenshots, presets 4A–5D, 2026-10-28): Drive 09 Beebee Pre ·
  Pre-Amp 35 Blacknight DS, 37 Mark V DS, 48 EV 5050 DS · Cab 01 Regal Tone 110,
  16 Eagle P412, 23 EV 5050 412 · Mod 05 Tremolo (Rate, Mix, Tone), 06 Stutter
  (Rate, Mix, Tone), 13 Lofi (Sample, Mix, Bit), 14 Slow Gear (Rise, Level) ·
  Delay 04 Tape, 06 Reverse · Reverb 02 Hall, 05 Mod.
  More sync notes: **n = 5 → 1/16** (Stutter rate 106), **n = 13 → 1/2**
  (Reverse time `0x800d`). Known so far: 5 1/16, 8 1/8, 9 1/8D, 10 1/4, 13 1/2.
  Not seen yet: 6, 7, 11, 12 (and 0–4).
- Still unnamed after batch 3 (app numbers): Drive 12, 13 · Mod 01, 07, 10 ·
  Delay 03, 08, 09 · Pre-Amp 03, 05, 07, 08, 10, 17–20, 28, 41, 45–47, 49 ·
  Cab 04, 05, 07, 09–14, 17, 19, 21, 22, 24.
- Batch 4 (90 screenshots, presets 13A–17D, 2026-10-28): Drive 13 Muffy ·
  Pre-Amp 03 59 US Bass, 05 US Blues CL, 08 Powerbell CL, 10 Mark III CL,
  17 Jazz 120, 18 UK 30 CL, 19 UK 30 OD, 20 HWT 103, 28 Hugen CL, 45 PV 5050 DS,
  47 Carol OD, 49 HT Club DS · Cab 04 Blues 112, 05 Mark 112, 11 Jazz 212,
  12 UK 212, 13 Tow Stones 212, 14 US Bass 410, 22 PV 5050 412, 24 HT 412 ·
  Mod 07 Vibrato (Rate, Mix, Tone) · Delay 03 Real Echo.
- **Correction: Delay and Mod use different sync tables.** Delay
  (`0x8000 | n`): 8 1/8, 9 1/8D, 10 1/4, 11 1/2T (17B), 12 1/4D (13D), 13 1/2
  (5D), in increasing note length. Mod (`101 + n`): 5 1/16 (5B), 6 1/4T (17A
  Tremolo 107), 10 1/4 (0A). Mod 6 = 1/4T can't share a table with delay
  8 = 1/8 and 9 = 1/8D. Both tables are in `ModuleCatalog.swift`.
- Still unnamed after batch 4 (app numbers, with presets that use them):
  Drive 12 (11D) · Mod 01 (19A), 10 (7A, 12B) · Delay 08 (6A), 09 (7A) ·
  Pre-Amp 07 (19A), 41 (8D, 18D, 19C), 46 (11B) · Cab 07 (6A, 7B, 11C, 11D),
  09 (10D), 10 (10A), 17 (6B, 10B), 19 (9C, 18D), 21 (7D, 9D).
- Batch 5 (48 screenshots, 6A, 7A, 10A, 10D, 11B, 11D, 12B, 18D, 19A,
  2026-10-28): Drive 12 Grey Faze · Pre-Amp 07 E650 CL, 41 Citrus 50,
  46 Regal Tone OD2 · Cab 07 Cardeff 112, 09 Citrus 212, 10 Dr Zee 212,
  19 Citrus 412 · Mod 01 Phaser (Rate, Level, Depth), 10 Pitch Shift (Mix, Tone,
  Pitch) · Delay 08 Crystal, 09 Rainbow.
  - **Pitch Shift pitch = raw/10 − 12 semitones** (189 → 6.9, 70 → −5.0).
  - Cab Low Cut 79 → "79Hz" and EQ gain 28 → −2.0 both confirm the earlier
    formulas.
  - Crystal and Rainbow have extra delay words (Crystal: a 4th row starting
    with "D…" = 50, plus 60). They're unlabelled; the overlay hid the label.
- Batch 6 (19 screenshots, 6B, 7D, 9D, 10B): Cab 17 Eagle S412, 21 HWT 412.
  **Every model used by the 80 factory presets now has a name** (93 models
  across the 9 types). Models only reachable through the model picker are
  still unnamed.
- Batch 7 (20 scrolled screenshots, 2026-10-28) labels every remaining
  visible setting:
  - Delay 08 Crystal and 09 Rainbow: Level, Feedback, Time, **Depth, Rate**.
    The official app hides delay words 4–5 on every other delay model.
  - Custom EQ: bands **Low, Low Mid, High Mid, High** (Gain/Freq/Q each),
    then **Low Cut (Hz, 0 = OFF), High Cut (Hz, 0 = OFF), Level**.
  - **EQ Q = (raw + 2)/10** (8 → 1.0, 11 → 1.3, 48 → 5.0, 50 → 5.2). This
    replaces the earlier raw/8 guess.
  - 5 Bands EQ: 100Hz, 250Hz, 630Hz, 1.6kHz, **4kHz**, **Level**. The
    remaining 9 words aren't shown in the official app.
  - Pre-Amp **Presence, Master** confirmed as sliders.
  - Values the official app never shows (and the Mac app labels P*): delay
    words 4–5 (except Crystal and Rainbow), mod/filter trailing 0, Noise Killer
    words 3–4, 5 Bands EQ words 7–15.
- **The full model lists aren't recoverable from the APK**: the Dart string pool
  is hash-ordered and there are no data assets. They need screenshots of each
  type's model picker, or a capture that steps through models.
- Batch 8 (2026-10-28) fills in the models no factory preset uses:
  - **Cab: the complete list, 01–65**, from the model picker. It ends with
    63 Akuila U47, 64 Akuila 7B, 65 Akuila 121. The user-IR section after it
    ("GIR&IR Samples") is out of scope. The names already in the catalog all
    matched.
    New names include 06 Dr Zee 112, 20 Slow 412, 25 Diesel 412,
    26–55 CT-… cabs, 57 SV810 121, 58 SV810 7B, 59 HK U47, 60 HK 7B and
    61 HK 121.
  - Drive 07 Jimmy OD, 08 Full DR, 10 Beebee+, 11 Black Rat.
  - **Pre-Amp: the complete list, 01–62** (04 US Sonic, 09 Blacknight CL,
    … 60 AKUILA 750 DS, 61 AKUILA 751, **62 Basser Crunch**, the last one).
    Basser Crunch has the standard Gain, Bass, Mid, Treble, Presence and
    Master. "AMPOG 2OD" (53) is spelled with the letter O, confirmed by the user.
  - Filter 01 Touch Wah: Peak, Sensitivity, Level. The word order is assumed
    from the display order. Its pedal graphic also shows an Attack knob.
  - Mod 02 Step Phaser (Rate, Level, Depth, like Phaser); 04 Jet Flanger
    (Rate, Mix, Feedback, like Flanger); 08 Rotary (Rate, Mix, Tone).
  - Delay 05 Mod: Level, Feedback, Time.
  - Drive, Filter, Mod and Delay now have no gaps up to their highest seen
    number. Cab and Pre-Amp are complete.
  - Reverb has a 6th model, **06 Shimmer** (settings not yet seen; the app
    uses the reverb defaults). The user confirmed the lists for Filter
    (3), Mod (14), Delay (9), Reverb (6) and EQ (2) are complete. Drive and
    Dyna were checked from picker screenshots:
  - **Drive 14–29**: 14 Full DS, 15 Shred, 16 Riet, 17 MTL Zone, 18 MTL Master,
    19 Obsessive Dist, 20 Rumble Drive, 21 Solo, 22 Blues Mood, 23 Blues Crab,
    24 Blade, 25 TS9 Boost, 26 ROD881, 27 Tethys 21, 28 TS Drive,
    29 Blackglass X8 (the last one).
  - **All model lists are now complete**: Drive 29, Pre-Amp 62, Cab 65, Dyna 4,
    Filter 3, Mod 14, Delay 9, Reverb 6, EQ 2 (194 models).
- Batch 9 (12 picker screenshots, 2026-10-28): two types no factory preset
  uses.
  - **Guitar Sim**: Acoustic → Electric / Piezo Pickup / Silent Guitar, each
    01–15 (GBS 45 a/b/c, GBS SJ200 a/b/c, GLD D55 a/b/c, MTN D45 a/b/c,
    TLR 614a/b/c); Classical → Piezo Pickup / Silent Guitar, each 01–06
    (Corba GK a/b/c, YMH 42S a/b/c). That makes 57 entries. Settings (from
    the user): Gain, Blend, Low, Mid, Mid Shift, High.
  - **Entire Amp** 01–26: 55 US TD, 59 US Bass, UK30 CL/CR/DS,
    ODS 100 CL/CR/DS, Dividers CL/DS, CAA OD100 CH1/CH2/CH3, Rock Vrb CL/DS,
    J800 CL/CR/DS, PLX 100 CL/DS, EV 5050 CH1/CH2/CH3, Cali BLD CL/CR/DS,
    followed by a "GNR Samples" section of user slots (out of scope).
    Settings (from the user): Treble, Presence, Master.
  - **Type codes confirmed** from a test preset the user saved to 1D:
    `1 Guitar Sim | 1 0 60 100 80 75 649 130 10736 53249 63487 47862 10488
    53249 63487 47841 46128` and `2 Entire Amp | 1 15 65 50 50 50 50 50`.
    So **0x0a = Guitar Sim, 0x0b = Entire Amp**. Entire Amp model 15 is
    16 J800 CL, the entry highlighted in the picker screenshot.
  - **Guitar Sim model numbers run straight through the subgroups** (0–56 in
    list order). Classical Silent 06 YMH 42S c was saved as model 56.
  - Guitar Sim words (checked against screenshots: 60 / 100 / 80 / 75 / 649 /
    130 shown as 60 / 100 / −4.0 / −4.5 / 849 / 1.0):
    Gain, Blend (knobs), **Low, Mid, High: dB = (raw − 120)/10**,
    **Mid Shift: Hz = raw + 200**. These are followed by 9 hidden words that
    look like DSP coefficients (0xD001, 0xF7FF pairs).
  - **Entire Amp: Gain, Bass, Mid, Treble, Presence, Master**, the same
    layout as a Pre-Amp. Confirmed when the user set 1D to 17/33/50/59/69/91
    and the dump read `1 15 17 33 50 59 69 91`.
  - **Dyna 04 Intel Reducer** (the last one). Settings not yet seen.

### Editing commands
- **Set module (live edit): `25 slot type n data[n]`** → `26 01`. Same
  record format as in the preset struct, always sent whole. Seen:
  - toggle on/off = first data word: `25 01 04 0c 00 00 …` (off) / `01 00 …` (on)
    (your "21:16 toggle first module" note matches the off→on pairs
    at 22:15:43/48 and 22:16:03/08 in the parse, slots 1 and 2)
  - param sweep slot 1, word 3: `00 → 0x64` (0…100) and back; slot 2,
    word 2: 0…100; slot 3, word 2: 0…100 → **knob params are 0–100**.
    The app streams one `25` frame per ~90 ms while you drag.
- **Get module: `23 slot` → `24 slot type n data`.**
- **Add module: `2d 00 01 01 09 00` → `2e 00 01`** — on the empty preset 7,
  this put a type-09 module into slot 1 (layout of the 5 bytes is still
  unclear: probably [?, slot, ?, type, ?]).
- **Save preset: `2f pp name[20]` → `30 01`.** The capture saved the
  current edit buffer (preset 5 "Delay 1/8d") into slot 7 as
  "Test hci snoop". The device then pushes `34` and the app re-selects
  preset 7 via `13`.
- `2b 01` / `2b 02 00` / `2b 02 01` → `2c` + 11 bytes (`03 03 …`, `03 07 …
  07`). The bytes changed after the type-09 module was added, so this is
  probably per-module-type availability/DSP usage. Unconfirmed.
- Unsolicited `43 02` / `43 04` near the end of the capture: unknown
  (footswitch? app backgrounded?).

### Still unknown
- Module type ↔ name mapping and per-model parameter names/ranges
  (cross-reference with the APK's UI strings next).
- Meaning of the remaining system-state bytes, the `2b`/`2c`, `53`/`54`,
  `55`/`56`, `43` and `97` messages, and the 7-byte preset trailer.
- Delete/move module commands (not exercised in this capture).
  The official Android app has no delete. **The Mac app removes effects
  over USB** with Studio's cloud transfer (see "USB on the real M2"): it reads
  the stored preset (`171`), checks its CRC, name, module types and on/off
  against the BLE view, backs it up, drops the 36-byte module record, fixes
  the CRC, writes it (`173`), reads it back to verify, then re-selects the
  preset over BLE.
- **Whole-preset write (not yet captured)**: the official app can push a
  complete preset into a slot from its Library/cloud. The libapp.so strings
  `sendPresetData`, `writePresetData`, `sendPresetComplete`,
  `uploadPresetFile` and `cancelTryPreset` point to this, as does the help
  text "You can download the application to target preset slot. If the
  target slot is not empty, the old one will be replaced". An HCI capture
  of downloading (or "trying") a Library preset would reveal the command.
  It would also give a reliable way to remove modules: rewrite the preset
  struct without them.

## Toolchain notes (this machine)

- Only Xcode **Command Line Tools** installed (no Xcode.app). The CLT SDK's
  SwiftUI framework requires the `SwiftUIMacros` compiler plugin for
  `@State` (and similar), which only ships inside Xcode.app — using `@State`
  fails to compile here. Worked around in `MooerPrimeMac` by using plain
  `ObservableObject` classes + `@StateObject`/manual `Binding` instead of
  `@State`. `@Published`/`@StateObject`/`@EnvironmentObject` compile fine.
- A macOS app's Bluetooth permission (`NSBluetoothAlwaysUsageDescription`)
  is only honored by TCC from a **real `.app` bundle** `Info.plist` — an
  Info.plist embedded in the executable's `__TEXT,__info_plist` Mach-O
  section (the common trick for bundle-less SwiftPM CLI tools) is NOT
  sufficient and the process aborts (SIGABRT, TCC privacy violation) on
  first Bluetooth access. `swift/scripts/make_app.sh` packages a
  real `.app`; always run via `open ...app`, not the bare binary.
- `brew install openjdk jadx` were installed on this machine to decompile
  the Android side (`classes.dex`) — confirms the above, no other findings
  needed from that side.

## Project status

See the [README](../README.md). Phase A (BLE scan/connect +
generic GATT inspector + placeholder effects-chain UI) is built and verified
running and has connected to the real M2. The Android HCI capture has been
decoded (see "Protocol decoded from Android HCI snoop capture").
`PrimeM2Protocol.swift` implements the connect sequence, preset
names/select/read, live module edit (`25`) and save (`2f`), and drives the
Effects Chain tab. Parsers/encoders round-trip every preset and module frame in
the capture. **Tested against the real pedal (2026-10-27): the effects chain,
slider values and on/off all show and work correctly.** Module types and
0A's model/param labels are confirmed from screenshots (see "Labels"). Open
items: full model lists per type, labels for other models, EQ/sync value
formulas, add/delete/move module.

## Mooer Studio for M2 (macOS, V1.5.0) — USB protocol (2026-10-28)

`Mooer Studio For M2_V1.5.0_Setup_Mac.dmg` contains "Prime Studio For M2.app",
an Electron app (Vue renderer, `node-hid`). Its JS, extracted from
`app.asar` (`out/main/index.js`, `out/renderer/assets/index-*.js`), is
readable. It talks to the pedal over **USB HID**, not BLE.

- **USB IDs: M2 = VID 13531 (0x34DB), PID 13**; P2 = PID 2. Bootloader is
  VID 8137 / PID 146.
- **Same `aa 55` frame and CRC** as BLE: `aa 55 | len16 LE | cmd | data | crc`.
  HID writes are `[0x00 report id, frameLen, frame…]`, split into 1023-byte
  chunks. The reader scans the input stream for `aa 55`.
- **The command numbers differ from BLE.** USB host→device:
  `129 idx` → `45` get preset (payload after 4 bytes = 852-byte preset);
  `59 idx 0 0 0 preset[852]` → `65` **import (write) whole preset into a slot**;
  `1 idx name[28]` save; `47 idx name[28]` → `48` save current / rename;
  `5 idx status` effect on/off; `7 from to` effect move;
  `37 …` → `38` set param; `33 idx` → `34` current preset;
  `41` preset names; `13` BPM; `67 0/1` lock/unlock; `42` factory reset;
  `163/165` backup/recovery; `56` IR import; `55` IR remove; `7` → `8` firmware.
  Device→host events: `1` preset-save, `4` effect-add, `5` effect status,
  **`6` effect-remove**, `7` effect move, `8` param change.
  Preset indices are **0-based** (0–79).
- **USB preset struct (852 bytes)**: name[28] ASCII, BPM float32 LE @28,
  effectNum u8 @32, pad to 36, then **12 × 68-byte effect records**:
  effectIndex u16 LE (a global effect id, not BLE's type/model),
  paramNum u8, status u8, subStatus u8, pad 3, params 15 × float32 LE
  (normalized 0…1). Records ≥ effectNum are zeroed.
- **There is no "delete effect" command.** Studio's `deleteEffect` is
  commented out. Its "clear preset" serializes the preset with
  effectNum = 0 / name "Empty" / BPM 120 and writes it with `59` (import).
  So the way to remove one effect is: read (`129`), drop that 68-byte
  record and shift the rest up, decrement effectNum, write back (`59`).

### USB on the real M2 (probed 2026-10-28)
- One HID interface, usage page 1, **1024-byte input/output reports, no
  report ID**. A short output report `[len, frame…]` works. Input reports
  start directly with `aa 55` and are zero-padded.
- **Over USB the M2 speaks the BLE command set**: `07` → `08 01 01`,
  `11` → `12` system state, `21 pp` → `22` preset (same struct as BLE).
- **`129` (0x81) is not answered.** It's Studio's P2-style "get preset".
  `129 7` was sent twice (once by the Mac app, once by a probe). After that,
  stored 1D (1-based 8, 0-based 7) read back with Guitar Sim `0 0 0…` and
  Entire Amp `1 0 0…`, where earlier the values were
  `1 56 60 100 80 75 649 130 10736 53249 63487 47862 10488 53249 63487 47841 46128`
  and `1 15 17 33 50 59 69 91`. 1C was unaffected. So **0x81 may be
  destructive (0-based index); never send it.** This isn't proven.
- **M2 whole-preset transfer = Studio's "cloud backup/recovery"**:
  read `171 (0xab) idx 00 00` → `172 (0xac)` u16 frameCount (1D: 3), then
  `171 idx n_lo n_hi` → `172` n u16, len u16, data[len].
  Write `173 (0xad) idx n u16 len u16 data` → `174` n u16, status (1 = ok).
  Index 0 = audition ("try") slot, 255 = end audition; stored presets are
  1-based. 1D's frames: 322 bytes (zeros + `ff ff`), 512 bytes (name and
  module types 0x0a/0x0b at fixed offsets), and 364 bytes. Layout not decoded yet.
- **1D restored (2026-10-28)** over USB using the BLE commands: `25` for
  slot 1 and slot 2, `2f 08 name`, then confirmed after `13` to 1C and back
  to 1D, and `21 08`.
  - **A `25` that changes a module's model makes the pedal load that
    model's default parameters and ignore the ones sent.** Slot 2 came back
    `1 15 65 50 50 50 50 50`. Sending the same record again, once the model
    is already set, applies the parameters.
  - **`21 pp` for the active preset returns the edit buffer, not the stored
    copy.** To check what's stored, select another preset and come back first.
- **Cloud preset format decoded** (read-only `171` dumps of 0A, 1A, 1B, 1C,
  1D; 3 frames of 322 + 512 + 364 = 1198 bytes, the same for every preset):
  - @0–321 (frame 1): zeros, `ff ff` at @320.
  - @322–389: `44 aa 20 01` followed by leftover bytes from other presets
    (stale buffer). Copy unchanged.
  - **@390 u16 BPM**, **@392 name[20]**.
  - **@412: 20 module slots × 36 bytes = type u16 + 17 u16 words**. The
    words are the BLE module words; unused trailing words may hold leftovers
    (1B Noise Killer has `60 60` past its BLE length). Empty slot = all zero.
    Slots are filled from the front, in chain order.
  - @1132 u16: unknown (`e1e8` on four presets, `a023` on 0A). It's not a
    CRC of any range. Copy unchanged. @1134–1195: `ff`.
  - **@1196 u16 LE = CRC-16/GSM over @322–1195** (confirmed on all 5).
  - The dump reflects the *stored* preset (1D showed the zeroed values
    before the restore and the restored values after it).
- **Write path confirmed (2026-10-28)**: 1D without the Entire Amp
  (record 2 removed, records shifted, CRC@1196 recomputed) was first sent
  to the **audition slot 0**: all 3 frames `174 n 0 01`; slot 2 was empty
  in the edit buffer; `173 255 0 0 0 0` → `174 0 0 1` ended the audition and
  brought the amp back, with stored 1D unchanged. Then it was written to
  **1D (idx 8)**: all frames ok, the `171` read-back was byte-identical,
  and after re-selecting, `21 08` showed slot 1 Guitar Sim and slot 2 empty.
  The original 1D is backed up in a scratch folder (not in this repo) as `1D-original.preset`.
- **Confirmed by the user (2026-10-28)**: the Mac app's trash button
  (USB removal) and Undo both work on the pedal, and the official Android
  app shows the result correctly.
- **The Mac app runs over USB alone (2026-10-28).** `PrimeM2Device`
  doesn't care which link it uses and prefers USB whenever the HID device is
  present; Bluetooth is the fallback. A USB-only sync took under 1 s:
  `01`, `29` ×2, `a9`, `11`, `21`/`33` for the active preset, then all 80
  presets via `21`.
  - Pitfall: calling `IOHIDDeviceSetReport` from inside the input-report
    callback (reply → next request) stalled input until each 2 s timeout.
    The app now handles input asynchronously on the main queue.
  - Over USB the pedal pushes **`9e 01` about every 6 s** (heartbeat?),
    which the app ignores. **It does NOT push `15` (preset changed on the
    pedal) or `34` (tempo) over USB** (user test: knob turns and a BPM
    change 120 → 63 went unnoticed). So over USB the app polls `11` about
    once a second whenever its queue is idle. Active preset = [1]; tempo
    [41..42] shows the pedal-side BPM change (63), as does `33 pp` → `34 3f 00`.
  - **Confirmed by the user (2026-10-28)**: with the polling, the app
    follows preset changes and BPM changes made on the pedal while on USB.
- **USB quirk: the pedal stays deaf for ~180–200 ms after a preset select
  (`13`)**. Measured from one process: a `21` sent 0–180 ms after the `14 01`
  reply got no answer; at 200 ms it was answered in 6 ms, and at 250 ms or more
  in 1 ms. The app waits 300 ms after `14` or `30` (save) on USB and re-sends
  timed-out read requests once. Before this fix, clicking a preset over USB
  switched the pedal but never loaded the preset (the old version "needed a
  double click"). Confirmed working by the user. The preset list also shows the
  clicked preset straight away while the select is pending.

### Adding, changing and removing modules (edit-buffer experiments, 1C, 2026-10-28)
All tests ran over USB in the edit buffer of the empty preset 1C. Nothing
was saved; after re-selecting 1C was empty again and its stored copy was
byte-identical to the backup.
- **`2d id op slot type model` → `2e id ok`** (id is echoed; ok 1/0):
  - **op 1 = put effect into slot** (empty slot, or replaces what's there),
    with the pedal's defaults for that model. `2d 00 01 01 09 00` from the
    capture = EQ into slot 1.
  - **op 2 = change the slot's category/model** (slot 4 Drive → Pre-Amp; slot 6
    → Pre-Amp model 61 with Basser Crunch defaults `70 48 52 50 50 70`).
  - **op 3 = delete the slot; later slots shift up** (deleting slot 3 moved
    4… to 3…). **This is a BLE-capable delete.**
  - A slot outside the chain (13 when 12 were used) → `2e 00 00`.
  - Guitar Sim via op 1 model 56 came back with full defaults, including the
    9 hidden words.
- **DSP limit**: with 12 modules (including 2 Pre-Amps and a Guitar Sim),
  op 1/2 answered `2e 00 01` but **changed nothing**. Always read back
  after `2d`. `25` does not check the limit (12 Drives accepted with `25`).
- **`25` with a new type or model** → the pedal loads that model's defaults
  and ignores the words sent. With the same type and model, the words are
  applied literally (zeros included, even Guitar Sim's hidden words).
  The pedal normalises the record length (Drive sent as 12 was stored as 10);
  a 4-byte record for an existing reverb was rejected (`26 00`).
  `25 slot 00 00` does NOT clear a slot: the type stays and the words become 0.
- Gaps are allowed (slot 4 used while slot 3 is empty).
- Default record lengths (bytes): Drive 10, Pre-Amp 16, Cab 10, Dyna 12,
  Filter 12, Mod 12, Delay 14, Reverb 12, EQ 34, Guitar Sim 34, Entire Amp 16.
- **`2b 2 x` → `2c` + 11 bytes**, per-type availability (presumably for the
  DSP limit): on an empty preset `03 07 03 03 03 03 03 03 03 03 07`; on the
  full buffer `00 01 00 03 03 03 03 00 03 03 01` (x = 0) and
  `01 03 03 03 03 03 01 03 03 01` (x = 1). Exact meaning still open.
- **Mac app editor (2026-10-28)**: 9 slot rows. Each used slot has category
  and model menus (→ `2d` op 2, then read back with `23`; a mismatch shows
  "DSP full"), on/off and sliders (→ `25`), and a trash button (→ `2d` op 3,
  then re-read with `21`; works over BLE too). The first empty row has "Add
  effect…" (→ `2d` op 1 into the next free slot). Every change stays in the
  edit buffer until Save. Over USB the app pauses 300 ms after `2e` as well.
  The earlier USB whole-preset removal (`CloudPreset`) is no longer used by
  the UI; the code is still there.
- **Guitar Sim ranges (official app, user)**: Low / Mid / High −12.0…+12.0 dB
  → raw 0…240; Mid Shift 200…2000 Hz → raw 0…1800. Words 7–15 (P7–P15) are
  hidden DSP data that the pedal sets on a model change (the same for all models
  seen); the editor shows them read-only in the raw line only.
- **Cab / EQ ranges (official app, user)**: Cab Low Cut OFF…600 Hz, High Cut
  OFF…2000 Hz (confirmed by the user); raw = Hz, 0 = OFF. Open contradiction:
  factory 2A stores High Cut 8300 and 0D stores 20000, and the earlier screenshots showed them
  as Hz; such values get a number field. 5 Bands EQ gains and Custom EQ gains
  −16.0…+16.0 dB → raw 0…64. Custom EQ Q 0.2…10.0 → raw 0…98. Custom EQ
  frequencies: Low 40…600, Low Mid 200…2000, High Mid 600…6000,
  High 1500…20000 Hz. Custom EQ Low Cut OFF…600 Hz,
  High Cut OFF…2000 Hz.
- **More ranges (official app, user)**: Reverb Pre Delay 0…200 ms; Delay
  Time 40…2500 ms unsynced; Pitch Shift −12.0…+12.0 (raw 0…240). Synced notes: see below.
- **Tempo-sync note table (measured 2026-10-28)**, recorded by stepping
  Rainbow's Time slider in the official app while polling `23 3` over USB.
  Slider order (left → right) with the stored index:
  1/32 = 2, 1/32D = 4, 1/32T = 0, 1/16 = 5, 1/16D = 7, 1/16T = 1, 1/8 = 8,
  1/8D = 9, 1/8T = 3, 1/4 = 10, 1/4D = 12, 1/4T = 6, 1/2 = 13, 1/2D = 14,
  1/2T = 11, 1/1 = 15. **Delay (0x8000 | index) and Mod Rate (101 + index)
  share this table.** It matches every earlier screenshot point (delay
  8/9/10/11/12/13, mod 5/6/10), so the earlier claim that the two use
  different tables was wrong.
- **Confirmed by the user (2026-10-28)**: the 9-slot editor (add, change
  category/model, remove), parameter sliders with the official app's ranges,
  tempo sync for Delay Time and Mod Rate, and saving all work on the pedal.
