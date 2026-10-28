# Capturing the real Prime M2 BLE protocol

> Historical: this is how the protocol was captured. The GATT Inspector and
> Frame Sender tabs it mentions have since been removed from the app, and
> the results are in [PROTOCOL.md](PROTOCOL.md).

Why: the official app's command logic is compiled Dart (AOT, no symbols for
individual functions) — static analysis can recover the UI text and feature
list but not the exact bytes sent over Bluetooth. A live capture is the
reliable way to learn that.

Two independent captures, both useful, neither depends on the other:

## 1. BLE GATT discovery straight off the pedal (no Android needed)

As soon as you have the M2:
1. Power it on and put it in Bluetooth pairing mode (check its manual/menu —
   likely a long-press on a settings button, similar to the app's "wireless
   footswitch" pairing flow).
2. On the Mac, build and run this project (see `README.md`) and use the
   "Scan" button, or use a generic BLE explorer like
   [LightBlue](https://apps.apple.com/us/app/lightblue/id639944780) (there's
   a Mac version) or `nRF Connect`.
3. Connect to whatever advertises a Mooer/Prime-looking name. Our app's
   "GATT Inspector" tab logs every service, characteristic (with
   read/write/notify properties), and descriptor it finds, plus the raw hex
   of anything readable.
4. Send me the full log output (the app's log panel, or `Console.app`
   filtered to `MooerPrimeMac` — everything is also printed to stdout).

This alone tells us the real service/characteristic UUIDs — no app
reversing required, since any BLE central can read GATT structure.

## 2. Android Bluetooth HCI snoop capture (for the actual command bytes)

> **Update for the M2 (2026-10-27):** GATT layout and framing are already
> known (`FFF0`; `aa 55` + LE length + payload + CRC-16; see [RESEARCH-NOTES.md](RESEARCH-NOTES.md)),
> and guessed commands got no response. So the most valuable part of
> this capture is now the **first few seconds after connecting**: the
> handshake/state request the app sends before anything else. Things to do:
> - **Quit the Mac app (or Disconnect) first.** BLE peripherals like this
>   usually accept only one central, and the phone may fail to connect or
>   silently miss traffic otherwise.
> - Start the capture (enable snoop + reboot) *before* opening the Mooer
>   app, so the very first connect is in the log.
> - On top of the steps below, also note: toggle a module, change a
>   parameter to its **min** and then its **max** value, select presets
>   0A → 0B → 0C *from the app*, and rename a preset if the app allows it.
>   Min/max and known preset slots make the bytes easy to pick out.

You said you can do this — here's the exact sequence. The goal is a `.log`
file containing clearly separated, timestamped chunks so we can match each
app action to the bytes it sent.

1. On the Android phone: **Settings → About phone → tap "Build number" 7
   times** to enable Developer Options (skip if already enabled).
2. **Settings → System → Developer options → Enable Bluetooth HCI snoop
   log** (exact wording varies by Android version/OEM — search "HCI snoop"
   in Settings search if you can't find it).
3. **Reboot the phone** (snoop logging only starts cleanly after a reboot
   once enabled).
4. Install `Prime_4.0.0_apkcube.apks` (use an installer that handles split
   APKs, e.g. the "APKs"/"SAI" app, or `adb install-multiple` if you have
   `adb` set up).
5. Open the app, pair with the Prime M2, and go to its main effects screen.
6. Perform this **exact sequence**, pausing 2-3 seconds between each step so
   they're separable in the log, and **write down each step with a rough
   timestamp** (just note the time on your phone's clock, or dictate a voice
   memo as you go — either is fine):
   - Connect to the device (if not already connected).
   - Toggle **one** effect module OFF, then back ON (pick any single
     module, e.g. the first Drive/Distortion block).
   - Change **one parameter** on that same module (e.g. drag its Gain knob
     noticeably up, then down).
   - Repeat the "toggle on/off" + "change one parameter" pair for 2 more
     *different* modules (e.g. an EQ block and a Delay/Reverb block).
   - Switch to a **different preset/patch** (just pick the next one in the
     list).
   - **Save** the current settings as a preset (any name is fine).
   - Disconnect, then reconnect.
7. Pull the log off the phone:
   ```
   adb pull /sdcard/btsnoop_hci.log
   ```
   (Location varies by device — also check `/sdcard/Android/data/` or use
   the Developer Options screen's "share" option if present, or
   `bugreport` as a fallback: `adb bugreport bugreport.zip`, which embeds
   the snoop log.)
8. Send me the `.log` (or `.zip`) file **plus your step-by-step notes with
   timestamps** — the notes are what let us tell "this chunk of bytes =
   toggling module 2 off" apart from everything else in the log.

I'll decode it with Wireshark-compatible tooling (it understands Bluetooth
ATT/GATT) and correlate the writes/notifications against your step list to
pin down the exact command format.
