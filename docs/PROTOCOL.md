# Mooer Prime M2 — protocol reference

This is everything learned about how to talk to the **Mooer Prime M2**
(firmware V1.5.0), condensed into a reference. It was worked out from:

- an Android Bluetooth HCI snoop capture of the official **Prime** app
  (v4.0.0, `com.mooer.prime`),
- the readable JavaScript of **Mooer Studio for M2** (macOS, V1.5.0, Electron),
- live probing of a real M2 over BLE and USB, and
- screenshots of the official app, matched against raw values.

The research log with the full history (dead ends included) is in
[RESEARCH-NOTES.md](RESEARCH-NOTES.md). The model names are in
[MODELS.md](MODELS.md).

Confidence markers: **confirmed** = checked on the real pedal;
*assumed* = consistent with everything seen, not proven.

> ⚠️ **Never send command `0x81` (129) to an M2.** It's Mooer Studio's "get
> preset" for the P2. The M2 doesn't answer it, and once it was sent, the
> stored values of the preset at that 0-based index read back as zeros.
> This isn't proven to be the cause, but don't risk it.

---

## 1. Transports

The same frames and the same command set work over both links.

### Bluetooth LE

- Advertised name **`M2Prime M2`**. No pairing or bonding needed.
- One custom service **`FFF0`**:

  | Char   | Properties    | Use                                   |
  |--------|---------------|---------------------------------------|
  | `FFF1` | write, notify | unused                                |
  | `FFF2` | read, notify  | all **device → host** frames          |
  | `FFF3` | write         | all **host → device** frames (write with response) |

- The official app negotiates MTU 517 (the device answers 508) and enables
  notify on `FFF2`. Large replies arrive split over several notifications
  (≈ 244 bytes each), so reassemble them using the frame's length field.

### USB HID

- **VID `0x34DB` (13531), PID `13`.** (P2 = PID 2; the bootloader is VID 8137 / PID 146.)
- One HID interface, usage page 1. **1024-byte input and output reports,
  no report ID.**
- Output: a short report `[frameLength, frame…]` is enough.
- Input reports start directly with `aa 55` and are zero-padded.
- **No pushes over USB** apart from a `9e 01` heartbeat about every 6 s.
  The pedal does *not* send `15` (preset changed) or `34` (tempo) over USB,
  so a host has to poll `11` (≈ 1 s) to follow pedal-side changes.
- **Deaf period:** for ~180–200 ms after a preset select (`13` → `14`) the
  pedal ignores requests (measured: a request sent 0–180 ms after the
  reply goes unanswered; at 200 ms it's answered in 6 ms). The Mac app
  waits 300 ms after `14`, `30` (save) and `2e` (module add/change/delete),
  and retries read requests once.
- macOS pitfall: calling `IOHIDDeviceSetReport` from inside the
  input-report callback stalls input until each timeout. Handle input
  asynchronously.

---

## 2. Framing

```
aa 55 | len (u16 LE) | payload[len] | CRC-16 (u16 BE)
```

- `len` counts the payload only.
- **CRC-16/GSM**: poly `0x1021`, init `0x0000`, not reflected, xorout
  `0xFFFF`, computed over **the length field plus the payload** (not over
  `aa 55`). Example: payload `15` → `aa 55 01 00 15 8a 5b`.
- `payload[0]` is the opcode. **The reply's opcode is the request's + 1.**
- Unsolicited device frames: `15` (preset changed on the pedal, BLE only),
  `34` (tempo), `43 xx`, `97 00`, `9e 01` (USB heartbeat).
- Multi-byte values are little-endian unless stated otherwise.

---

## 3. Commands

Preset numbers are **1…80** (20 banks × A–D: 0A = 1, 0B = 2, … 19D = 80;
`number = bank × 4 + slot + 1`).

| Request | Reply | Meaning | Status |
|---|---|---|---|
| `01` | `02 …` | device info: `41 01 "V1.5.0" 00 08 50 0a 0a 3c 0a 0a 0a 0a` | confirmed |
| `07` | `08 01 01` | unknown | seen |
| `11` | `12 state[98]` | get system state (§4) | confirmed |
| `13 state[98]` | `14 01` | set system state = **select preset** (§4) | confirmed |
| `21 pp` | `22 preset` | read preset `pp` (§5). For the active preset this is the **edit buffer** | confirmed |
| `23 slot` | `24 slot type n data[n]` | read one module slot | confirmed |
| `25 slot type n data[n]` | `26 01` | set one module (live edit, §6) | confirmed |
| `29 start end` | `2a …` | preset names, 20 bytes each (`29 01 28`, `29 29 50`) | confirmed |
| `2b …` | `2c` + 11 bytes | per-category availability (DSP budget?) | partly |
| `2d id op slot type model` | `2e id ok` | add / change / delete module (§6) | confirmed |
| `2f pp name[20]` | `30 01` | **save** the edit buffer to preset `pp` with that name | confirmed |
| `33 pp` | `34 bpm(u16)` | tempo of preset `pp` | confirmed |
| `53 01 0a` | `54 …` | 10 × "no record" (looper recordings?) | seen |
| `55` | `56 04` | unknown | seen |
| `65 cat start(u16) end(u16)` | `66 …` | named lists; category 7 = drum patterns ("POP01" … "METRONOME02") | seen |
| `91` | `92 pct 00` | battery percent | confirmed |
| `a9` | `aa "iPedal…"` | serial number string | confirmed |
| `ab idx n(u16)` | `ac …` | whole-preset read, "cloud" format (§7), **USB** | confirmed |
| `ad idx n(u16) len(u16) data` | `ae n(u16) status` | whole-preset write (§7), **USB** | confirmed |

Connect sequence used by the official app (and the Mac app):
`01`, `29 01 28`, `29 29 50`, `a9`, `11`, `21 pp` + `33 pp` for the active
preset. The Mac app then reads all 80 presets with `21`. Over USB the whole
sync takes under a second.

---

## 4. System state (`11` / `12`, `13` / `14`, push `15`)

A 98-byte struct (99-byte payload including the opcode).

| Payload offset | Meaning |
|---|---|
| `[1]` | **active preset** (1…80) |
| `[9..28]` | device name, NUL-padded (`"M2Prime M2"`) |
| `[41..42]` | tempo, u16 LE (BPM) |
| rest | global settings, not mapped |

**Select a preset** by reading the state (`11`), changing `[1]` and writing
the whole struct back with `13`. The pedal answers `14 01` (over BLE it also
pushes `34`). Then re-read the preset with `21 pp` (on USB, after the deaf
period).

---

## 5. Preset struct (`21 pp` → `22`)

```
22 pp | name[20] NUL-padded | 20 × module record | 7-byte trailer (zeros)
module record = slot# (1…20) | type | n | data[n]
empty slot    = slot# 00 00
data          = n/2 × u16 LE words: [enabled 0/1] [model] [param…]
```

- The chain is **free-form**: any category in any slot, gaps allowed.
  The official app offers 9 slots; the pedal has 20 record positions.
- **The DSP has a limit.** It depends on which models are used (heavy ones:
  Pre-Amp, Guitar Sim). See `2d` below.

### Module types

| Type | Category | Default record length (bytes) |
|---|---|---|
| `0x01` | Drive | 10 |
| `0x02` | Pre-Amp | 16 |
| `0x03` | Cab | 10 |
| `0x04` | Dyna | 12 |
| `0x05` | Filter | 12 |
| `0x06` | Mod | 12 |
| `0x07` | Delay | 14 |
| `0x08` | Reverb | 12 |
| `0x09` | EQ | 34 |
| `0x0a` | Guitar Sim | 34 |
| `0x0b` | Entire Amp | 16 |

The official app shows the model as **wire value + 1** (`AMPOG SVT 4` = app
55 = wire 54). Guitar Sim models are numbered straight through the app's
five subgroups (0–56).

### Parameter words (after enabled + model)

Knobs are 0…100 unless listed otherwise.

| Category | Words |
|---|---|
| Drive | Gain, Tone, Vol |
| Pre-Amp, Entire Amp | Gain, Bass, Mid, Treble, Presence, Master |
| Cab | Gain, Low Cut, High Cut — **plain Hz, 0 = OFF**. App ranges: Low OFF…600, High OFF…2000 (factory presets also store 8300 and 20000) |
| Dyna | Attack, Ratio, Threshold, Level. Red Comp: Sensitivity, Level. Noise Killer: Threshold, Level (+2 hidden) |
| Filter | Rate, Peak, Range, Level. Touch Wah: Peak, Sensitivity, Level (*order assumed*). Q-Filter: Rate, Q, Mix |
| Mod | Rate (sync-able), Mix, Tone, Depth. Phaser / Step Phaser: Rate, Level, Depth. Flanger / Jet Flanger: Rate, Mix, Feedback. Tremolo, Stutter, Vibrato, Rotary, Ring: Rate, Mix, Tone. Pitch Shift: Mix, Tone, Pitch. Lofi: Sample, Mix, Bit. Slow Gear: Rise, Level |
| Delay | Level, Feedback, Time (ms 40…2500, sync-able), then 2 words that only Crystal and Rainbow show: Depth, Rate |
| Reverb | Pre Delay (ms 0…200), Level, Decay, Tone |
| EQ — 5 Bands EQ | 100 Hz, 250 Hz, 630 Hz, 1.6 kHz, 4 kHz (gain), Level, then 9 hidden words |
| EQ — Custom EQ | 4 bands (Low, Low Mid, High Mid, High) × (Gain, Freq Hz, Q), then Low Cut (OFF…600 Hz), High Cut (OFF…2000 Hz), Level. Band freq ranges: 40–600, 200–2000, 600–6000, 1500–20000 Hz |
| Guitar Sim | Gain, Blend, Low, Mid, Mid Shift, High, then 9 hidden words (DSP data the pedal fills in on a model change) |

### Value encodings

| Kind | Encoding | Range (raw) |
|---|---|---|
| EQ gain | dB = (raw − 32) / 2 | −16…+16 dB (0…64) |
| EQ Q | Q = (raw + 2) / 10 | 0.2…10.0 (0…98) |
| Guitar Sim Low/Mid/High | dB = (raw − 120) / 10 | −12…+12 dB (0…240) |
| Guitar Sim Mid Shift | Hz = raw + 200 | 200…2000 Hz (0…1800) |
| Pitch Shift pitch | semitones = raw / 10 − 12 | −12…+12 (0…240) |
| Frequencies, cuts | plain Hz, 0 = OFF | |
| Times | plain ms | |
| Delay Time, synced | `0x8000 \| index` | |
| Mod Rate, synced | `101 + index` (unsynced: 0…100) | |

### Tempo-sync note index (shared by Delay and Mod)

Measured by stepping the official app's slider. In slider order:

| Note | 1/32 | 1/32D | 1/32T | 1/16 | 1/16D | 1/16T | 1/8 | 1/8D | 1/8T | 1/4 | 1/4D | 1/4T | 1/2 | 1/2D | 1/2T | 1/1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Index | 2 | 4 | 0 | 5 | 7 | 1 | 8 | 9 | 3 | 10 | 12 | 6 | 13 | 14 | 11 | 15 |

---

## 6. Editing the edit buffer

Every edit changes only the **edit buffer** of the active preset. It's lost
when you select another preset unless you save it with `2f pp name[20]`.
To check what's stored, select another preset and come back: `21` on the
active preset returns the edit buffer.

### `25 slot type n data[n]` → `26 ok` — set a module

- Always send the whole record. The official app streams one `25` about every
  90 ms while a slider is dragged.
- **Same type and model as before:** the words are applied literally (zeros
  too, hidden words included).
- **New type or model:** the pedal loads that model's **defaults** and
  ignores the words sent. Send the record a second time to apply values.
- The pedal normalises the record length. A record that's too short for an
  existing module was rejected (`26 00`).
- `25 slot 00 00` does **not** clear a slot (the type stays, the words become 0).
- `25` doesn't check the DSP limit (12 Drives were accepted).

### `2d id op slot type model` → `2e id ok` — add, change, delete

`id` is echoed back; `ok` is 1 or 0.

| op | Effect |
|---|---|
| `01` | put a module into `slot` (empty or occupied) with the model's defaults |
| `02` | change the slot's category and/or model (defaults loaded) |
| `03` | **delete** the slot; later slots shift up (type and model ignored) |

- A slot beyond the end of the chain → `2e id 00`.
- **When the DSP is full, op 1/2 answer `ok = 1` but change nothing.** Always
  read the slot back (`23 slot`) afterwards.
- `2d 00 01 01 09 00` (from the capture) = EQ into slot 1.

### `2b` → `2c` (availability, *meaning partly open*)

`2b 01`, `2b 02 00`, `2b 02 01` → `2c` + 11 bytes, one per category. On an
empty preset: `03 07 03 03 03 03 03 03 03 03 07`. On a full buffer:
`00 01 00 03 03 03 03 00 03 03 01`. Probably what can still be added
before the DSP limit.

---

## 7. Whole-preset transfer over USB (Mooer Studio's "cloud" format)

Mooer Studio backs up and restores M2 presets with these commands. They've
only been used over USB.

**Read:** `ab idx 00 00` → `ac frameCount(u16)` (3), then for n = 1…3:
`ab idx n(u16)` → `ac n(u16) len(u16) data[len]`.

**Write:** `ad idx n(u16) len(u16) data[len]` → `ae n(u16) status` (1 = ok)
for each frame.

- `idx` **0 = audition ("try") slot**: plays the preset without storing it.
  `ad ff 00 00 00 00` → `ae 00 00 01` ends the audition.
- `idx` 1…80 = stored presets.
- **Confirmed:** a write to the audition slot leaves stored presets unchanged.
  A write to a stored preset reads back byte-identical.

### Layout (1198 bytes, frames of 322 + 512 + 364)

| Offset | Size | Meaning |
|---|---|---|
| 0–321 | 322 | zeros, `ff ff` at 320 |
| 322–389 | 68 | `44 aa 20 01` + stale bytes from other presets (copy unchanged) |
| **390** | u16 | BPM |
| **392** | 20 | name, NUL-padded |
| **412** | 20 × 36 | module slots: **type u16 + 17 u16 words** (the same words as §5, enabled first). Filled from the front in chain order; empty = all zeros. Trailing words may hold leftovers |
| 1132 | u16 | unknown (`e1e8` on most presets, `a023` on 0A); not a CRC. Copy unchanged |
| 1134–1195 | | `ff` |
| **1196** | u16 **LE** | **CRC-16/GSM over bytes 322…1195** |

Removing a module this way: drop its 36-byte record, shift the rest up,
zero the last record, recompute the CRC and write all three frames.
(The Mac app now uses `2d` op 3 instead; the code for this path is in
`PrimeUSBDevice.swift` as `CloudPreset`.)

---

## 8. Mooer Studio's own USB command set (P2-style, mostly not for the M2)

Mooer Studio's JavaScript also contains a different command set used for
the P2. **The M2 doesn't speak it**: it answers the BLE command set over USB
instead, and `0x81` may be destructive (see the warning at the top).

For reference: `129 idx` get preset (852-byte struct: name[28], BPM
float32, effect count, 12 × 68-byte records with float32 params);
`59 idx …` import preset; `47 idx name[28]` save; `5` effect on/off;
`7 from to` move; `37` set param; `41` names; `13` BPM; `163/165`
backup/recovery. Indices are 0-based.

---

## 9. Open questions

- Meaning of the `2b`/`2c` bytes, `43`, `55`/`56`, `53`/`54`, `97`, `9e`,
  `07`/`08`, and the 7-byte preset trailer.
- The unknown u16 at offset 1132 of the cloud format.
- Most system-state bytes (global EQ, levels, footswitch, MIDI…).
- Cab High Cut: the official app's range ends at 2000 Hz, but factory presets
  store 8300 and 20000.
- Settings of Reverb 06 Shimmer and Dyna 04 Intel Reducer (they currently
  use their category defaults).
- The exact DSP cost per model.
- Looper, drum machine, tuner, MIDI and expression-pedal settings haven't
  been explored.
