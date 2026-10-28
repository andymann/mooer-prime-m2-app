"""Hardware test for the Python app against a real Prime M2 over USB, driving
the app's own protocol code headless. One step per run, so you can stop
between writes. See README.md, "Testing on the pedal". SLOT is 1...80.

    hwtest.py backup             read-only: whole-preset read (ab) of all 80 presets to files
    hwtest.py state              read-only: active preset, edit buffer vs stored
    hwtest.py edits              writes 25 to the active preset's edit buffer, each undone
    hwtest.py chain              writes 2d: add / change / delete in the next free slot
    hwtest.py reselect [SLOT]    writes 13: select SLOT (default: active), discarding edits
    hwtest.py save SLOT NAME     writes 2f: save the edit buffer to SLOT, verify via ab
    hwtest.py audition SLOT      writes ad 0: play SLOT's backup without storing it
    hwtest.py restore SLOT       writes ad SLOT from the backup, verify byte-identical
    hwtest.py verify             read-only: compare all 80 stored presets with the backup

Backups go to $HW_BACKUP, by default a "hwtest" folder next to the app's
backups. Set HW_VERBOSE=1 to print the protocol log.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from mooer_prime import catalog  # noqa: E402
from mooer_prime.app import MainThreadInvoker, QtScheduler  # noqa: E402
from mooer_prime.models import slot_label  # noqa: E402
from mooer_prime.protocol import PrimeM2Device  # noqa: E402
from mooer_prime.usb import CloudPreset, PrimeUSBDevice, backup_directory  # noqa: E402

BACKUP = Path(os.environ.get("HW_BACKUP", backup_directory().parent / "hwtest"))
qapp = QCoreApplication(sys.argv)
scheduler = QtScheduler()
invoker = MainThreadInvoker()
logs = []


def log(line):
    logs.append(line)
    if os.environ.get("HW_VERBOSE"):
        print("   log:", line)


def spin(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def wait(cond, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        spin(0.02)
    raise SystemExit(f"FAIL: timed out waiting for {what}")


def call(fn, *args, timeout=10.0):
    """Runs a completion-style USB call and returns (value, error)."""
    box = []
    fn(*args, lambda value, error: box.append((value, error)))
    wait(lambda: box, timeout, fn.__name__)
    return box[0]


def check(ok, message):
    print(("  PASS " if ok else "  FAIL ") + message)
    if not ok:
        raise SystemExit(1)


STEPS = {"backup", "state", "edits", "chain", "reselect", "save", "audition", "restore", "verify"}
if len(sys.argv) < 2 or sys.argv[1] not in STEPS:
    raise SystemExit(__doc__)
if sys.argv[1] not in ("backup", "state") and not any(BACKUP.glob("*.preset")):
    raise SystemExit(f"No backup in {BACKUP}. Run `hwtest.py backup` first.")

usb = PrimeUSBDevice(scheduler, invoker.post, log)
wait(lambda: usb.is_connected, 3, "the USB pedal")


def start_session():
    device = PrimeM2Device(scheduler, log)
    usb.on_frame = device.did_receive_usb
    device.usb_ready(usb.write_frame)
    wait(lambda: device.is_ready and device.preset is not None, 5, "sync")
    wait(lambda: not device.is_busy, 5, "idle")
    return device


def read_slot(device, slot):
    box = []
    device._read_slot(slot, box.append)
    wait(lambda: box, 3, f"slot {slot}")
    return box[0]


def read_buffer(device, index):
    """21 on the active preset = edit buffer."""
    device.load_preset(index)
    wait(lambda: not device.is_busy, 5, "edit buffer")
    return [m for m in device.preset.modules if not m.is_empty]


def stored_records(cloud):
    out = []
    for k in range(CloudPreset.MODULE_COUNT):
        o = CloudPreset.MODULES_OFFSET + k * CloudPreset.MODULE_SIZE
        kind = cloud.data[o] | cloud.data[o + 1] << 8
        if kind:
            words = [cloud.data[o + 2 + 2 * i] | cloud.data[o + 3 + 2 * i] << 8 for i in range(17)]
            out.append((kind, words))
    return out


def describe(modules):
    return ", ".join(f"{m.slot}:{m.type_name} {catalog.model_label(m.type, m.model or 0)}"
                     f"{'' if m.is_enabled else ' (off)'}" for m in modules)


def buffer_matches_stored(chain, cloud):
    stored = stored_records(cloud)
    if len(stored) != len(chain):
        return False
    return all(kind == m.type and words[:len(m.words)] == m.words for (kind, words), m in zip(stored, chain))


def backup_path(index):
    return BACKUP / f"{index:02d}-{slot_label(index)}.preset"


step = sys.argv[1]

if step == "backup":
    # Never replace a backup: after `save` it would hold the overwritten slot.
    if any(BACKUP.glob("*.preset")):
        raise SystemExit(f"{BACKUP} already holds a backup. Move it away or set HW_BACKUP to a new folder.")
    BACKUP.mkdir(parents=True, exist_ok=True)
    for index in range(1, 81):
        cloud, error = call(usb.read_preset, index)
        check(error is None and cloud.crc_is_valid, f"read {slot_label(index)} “{cloud.name if cloud else '?'}”")
        backup_path(index).write_bytes(cloud.file_data)
    print(f"Backed up 80 presets to {BACKUP}")
    step = "state"

if step == "state":
    device = start_session()
    index = device.active_preset_index
    chain = [m for m in device.preset.modules if not m.is_empty]
    print(f"Active {slot_label(index)} “{device.preset.name}”, tempo {device.tempo}: {describe(chain)}")
    for m in chain:
        print(f"   slot {m.slot}: {m.words}")
    names = dict(device.preset_names)
    print("Names:", "; ".join(f"{slot_label(i)} {names[i]}" for i in sorted(names)))
    device.usb_gone()   # stop the session's polling before the whole-preset read
    cloud, error = call(usb.read_preset, index)
    print("Edit buffer matches stored preset:", error or buffer_matches_stored(chain, cloud))

elif step == "edits":
    device = start_session()
    index = device.active_preset_index
    original = [m for m in device.preset.modules if not m.is_empty]
    print(f"Active {slot_label(index)} “{device.preset.name}”: {describe(original)}")
    first = original[0]

    device.update_module(first.with_enabled(False))
    check(read_slot(device, first.slot).words[0] == 0, f"slot {first.slot} switched off")
    device.update_module(first)
    check(read_slot(device, first.slot).words == first.words, f"slot {first.slot} switched back on")

    gain = first.words[2]
    nudged = gain + 1 if gain < 100 else gain - 1
    device.update_module(first.with_word(2, nudged))
    check(read_slot(device, first.slot).words[2] == nudged, f"slot {first.slot} param 1: {gain} -> {nudged}")
    sent_before = len(logs)
    for value in list(range(nudged, nudged + 15)) + [gain]:   # a fast slider drag back
        device.update_module(first.with_word(2, min(value, 100)))
    check(read_slot(device, first.slot).words == first.words, f"slider drag ended on original value {gain}")

    delay = next((m for m in original if m.type == 0x07), None)
    if delay:
        time_word = 4
        current = delay.words[time_word]
        if catalog.sync_index(catalog.ParamKind.MILLISECONDS, current) is None:
            other, label = catalog.synced_value(catalog.ParamKind.MILLISECONDS, 10), "tempo-synced to 1/4"
        else:
            other, label = 500, "set to free time 500 ms"
        device.update_module(delay.with_word(time_word, other))
        got = read_slot(device, delay.slot).words[time_word]
        check(got == other, f"delay time {label} (0x{got:04x})")
        device.update_module(delay)
        check(read_slot(device, delay.slot).words == delay.words,
              f"delay time back to {catalog.format_value(current, catalog.ParamKind.MILLISECONDS)}")

    after = read_buffer(device, index)
    check([m.words for m in after] == [m.words for m in original], "whole edit buffer equals the original")
    check(not any("rejected" in line or "Timeout" in line for line in logs), "no rejected or timed-out requests")

elif step == "chain":
    device = start_session()
    index = device.active_preset_index
    original = [m for m in device.preset.modules if not m.is_empty]
    slot = len(original) + 1
    print(f"Active {slot_label(index)}: {describe(original)}; testing slot {slot}")

    device.place_module(slot, 0x08, 0)
    wait(lambda: not device.is_busy, 5)
    module = read_slot(device, slot)
    check(device.notice is None and module.type == 0x08 and module.model == 0,
          f"added Reverb Room in slot {slot} ({device.notice or 'ok'})")
    device.place_module(slot, 0x08, 2)
    wait(lambda: not device.is_busy, 5)
    module = read_slot(device, slot)
    check(device.notice is None and module.model == 2, f"changed slot {slot} to Reverb Plate")
    device.delete_module(slot)
    wait(lambda: not device.is_busy, 5)
    after = [m for m in device.preset.modules if not m.is_empty]
    check([m.words for m in after] == [m.words for m in original] and [m.type for m in after] == [m.type for m in original],
          f"removed slot {slot}; chain is back to {describe(after)}")
    check(not any("rejected" in line or "Timeout" in line for line in logs), "no rejected or timed-out requests")

elif step == "save":
    target, name = int(sys.argv[2]), sys.argv[3]
    device = start_session()
    source = device.active_preset_index
    chain = [m for m in device.preset.modules if not m.is_empty]
    print(f"Saving edit buffer of {slot_label(source)} ({describe(chain)}) to {slot_label(target)} as “{name}”")
    device.save_preset(target, name)
    wait(lambda: device.active_preset_index == target and not device.is_busy, 8, "save + select")
    check(device.preset.name == name, f"pedal selected {slot_label(target)} “{device.preset.name}”")
    cloud, error = call(usb.read_preset, target)
    check(error is None and cloud.name == name, f"stored name is “{cloud.name if cloud else error}”")
    check(buffer_matches_stored(chain, cloud), "stored modules equal the saved edit buffer")

elif step == "restore":
    target = int(sys.argv[2])
    original = CloudPreset.from_file_data(backup_path(target).read_bytes())
    usb.last_backup = None
    _, error = call(usb._write_and_verify, target, original, timeout=20)
    check(error is None, f"restored {slot_label(target)} “{original.name}”, read back byte-identical ({error or 'ok'})")

elif step == "verify":
    changed = []
    for index in range(1, 81):
        cloud, error = call(usb.read_preset, index)
        backup = CloudPreset.from_file_data(backup_path(index).read_bytes())
        if error or cloud.data != backup.data:
            changed.append(slot_label(index))
    check(not changed, f"all 80 stored presets byte-identical to the backup (changed: {changed or 'none'})")
    device = start_session()
    print(f"Active preset {slot_label(device.active_preset_index)} “{device.preset.name}”")

elif step == "audition":
    # ad 0 ...: plays the backup without storing it; ad ff 00 00 00 00 ends the audition.
    target = int(sys.argv[2])
    original = CloudPreset.from_file_data(backup_path(target).read_bytes())
    _, error = call(usb.write_preset, 0, original, timeout=20)
    check(error is None, f"wrote backup of {slot_label(target)} to the audition slot ({error or 'ok'})")
    payload, error = call(lambda done: usb._send(bytes([0xAD, 0xFF, 0, 0, 0, 0]), 0xAE, done))
    check(error is None and payload == bytes([0xAE, 0, 0, 1]), f"audition ended ({payload.hex(' ') if payload else error})")

elif step == "reselect":
    target = int(sys.argv[2]) if len(sys.argv) > 2 else None
    device = start_session()
    target = target or device.active_preset_index
    device.select_preset(target)
    wait(lambda: device.active_preset_index == target and device.requested_preset_index is None
         and not device.is_busy, 5, "select")
    chain = [m for m in device.preset.modules if not m.is_empty]
    cloud = CloudPreset.from_file_data(backup_path(target).read_bytes())
    print(f"Selected {slot_label(target)} “{device.preset.name}”: {describe(chain)}")
    check(buffer_matches_stored(chain, cloud), "edit buffer equals the backed-up stored preset")

usb.close()
