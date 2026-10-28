"""Prime M2 over USB HID (VID 0x34DB, PID 13), port of PrimeUSBDevice.swift.

- One HID interface with 1024-byte reports and no report ID. Output reports
  are `[frameLength, frame...]`; input reports start with the `aa 55` frame
  and are zero-padded. Over USB the M2 answers the BLE command set too.
- What USB adds is Mooer Studio's whole-preset transfer ("cloud backup /
  recovery"): read with `171` -> `172`, write with `173` -> `174`, in
  frames. `CloudPreset` is that format (not used by the UI, like in Swift).

hidapi has no hot-plug callbacks, so the device list is polled once a
second, and a reader thread blocks on input reports and hands them to the
GUI thread through `post`.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import catalog, frame
from .frame import FrameReassembler
from .models import decode_name, slot_label

try:
    import hid  # the `hidapi` package (cython-hidapi)
except ImportError:  # pragma: no cover - reported in the log at runtime
    hid = None

VENDOR_ID = 0x34DB   # 13531
PRODUCT_ID = 13      # M2 (P2 = 2)

BACKUP_READ = 171
BACKUP_READ_REPLY = 172
RECOVERY_WRITE = 173
RECOVERY_WRITE_REPLY = 174

Completion = Callable[[object, Optional[str]], None]   # (value, error message)


@dataclass
class Backup:
    preset_index: int   # 1-based
    path: Path


@dataclass
class _Request:
    payload: bytes
    reply: int
    handler: Completion


class PrimeUSBDevice:
    def __init__(self, scheduler, post: Callable[[Callable[[], None]], None],
                 log: Callable[[str], None] = print) -> None:
        self.scheduler = scheduler
        self.post = post
        self.log = log
        self.is_connected = False
        self.is_busy = False
        self.notice: Optional[str] = None
        self.last_backup: Optional[Backup] = None
        # Every frame that isn't a reply to our own transfer commands.
        self.on_frame: Optional[Callable[[bytes], None]] = None
        self.on_availability_change: Optional[Callable[[bool], None]] = None

        self._device = None
        self._path: Optional[bytes] = None
        self._reader: Optional[threading.Thread] = None
        self._stop_reader = threading.Event()
        self._write_lock = threading.Lock()
        self._rx = FrameReassembler()
        self._queue: List[_Request] = []
        self._in_flight: Optional[_Request] = None
        self._timeout = None

        if hid is None:
            self.log("USB: the `hidapi` package isn't installed; USB is disabled")
            return
        self._poll_devices()

    # ---- Device lifecycle (GUI thread) -------------------------------------

    def _poll_devices(self) -> None:
        try:
            found = hid.enumerate(VENDOR_ID, PRODUCT_ID)
        except Exception as error:  # noqa: BLE001
            self.log(f"USB: enumerate failed: {error}")
            found = []
        paths = [d["path"] for d in found]
        if self._device is None and paths:
            self._attach(paths[0])
        elif self._device is not None and self._path not in paths:
            self._detach()
        self.scheduler.call_later(1.0, self._poll_devices)

    def _attach(self, path: bytes) -> None:
        try:
            device = hid.device()
            device.open_path(path)
        except Exception as error:  # noqa: BLE001
            self.log(f"USB: could not open the Prime M2: {error}")
            return
        self.log("USB: Prime M2 found")
        self._device = device
        self._path = path
        self._rx.clear()
        self._stop_reader.clear()
        self._reader = threading.Thread(target=self._read_loop, args=(device,), name="usb-reader", daemon=True)
        self._reader.start()
        was_connected = self.is_connected
        self.is_connected = True
        if not was_connected and self.on_availability_change:
            self.on_availability_change(True)

    def _detach(self) -> None:
        if self._device is None:
            return
        self.log("USB: Prime M2 disconnected")
        device = self._device
        self._device = None
        self._path = None
        self._stop_reader.set()
        if self._reader:
            self._reader.join(timeout=1)
        self._reader = None
        try:
            device.close()
        except Exception:  # noqa: BLE001
            pass
        self.is_connected = False
        self._fail_all("USB device disconnected")
        if self.on_availability_change:
            self.on_availability_change(False)

    def close(self) -> None:
        self._detach()

    def _read_loop(self, device) -> None:
        """Reader thread: blocks on input reports, posts them to the GUI thread."""
        while not self._stop_reader.is_set():
            try:
                report = device.read(1024, 100)
            except Exception:  # noqa: BLE001 - unplugged
                self.post(self._detach)
                return
            if report:
                data = bytes(report)
                self.post(lambda data=data: self._did_receive(data))

    # ---- Whole-preset transfer ---------------------------------------------

    def read_preset(self, index: int, completion: Completion) -> None:
        """Reads stored preset `index` (1-based)."""
        def on_header(payload, error):
            if error:
                return completion(None, error)
            # 172 | frameCount u16
            if len(payload) < 3:
                return completion(None, f"Preset {slot_label(index)} is empty or unreadable")
            count = payload[1] | payload[2] << 8
            self._read_frames(index, 1, count, [], completion)

        self._send(bytes([BACKUP_READ, index, 0, 0]), BACKUP_READ_REPLY, on_header)

    def _read_frames(self, index: int, number: int, count: int, collected: List[bytes],
                     completion: Completion) -> None:
        if number > count:
            preset = CloudPreset.from_frames(collected)
            if preset is None:
                return completion(None, f"Unexpected preset format ({[len(f) for f in collected]} bytes)")
            return completion(preset, None)

        def on_frame(payload, error):
            if error:
                return completion(None, error)
            # 172 | frame u16 | len u16 | data
            if len(payload) < 5:
                return completion(None, f"Short reply for preset frame {number}")
            length = payload[3] | payload[4] << 8
            if len(payload) < 5 + length:
                return completion(None, f"Preset frame {number} truncated")
            self._read_frames(index, number + 1, count, collected + [payload[5:5 + length]], completion)

        self._send(bytes([BACKUP_READ, index, number & 0xFF, number >> 8]), BACKUP_READ_REPLY, on_frame)

    def write_preset(self, index: int, preset: "CloudPreset", completion: Completion) -> None:
        """Overwrites stored preset `index` (1-based; 0 is the audition slot)."""
        self._write_frames(index, preset.frames, 1, completion)

    def _write_frames(self, index: int, frames: List[bytes], number: int, completion: Completion) -> None:
        if number > len(frames):
            return completion(None, None)
        data = frames[number - 1]
        header = bytes([RECOVERY_WRITE, index, number & 0xFF, number >> 8, len(data) & 0xFF, len(data) >> 8])

        def on_reply(payload, error):
            if error:
                return completion(None, error)
            # 174 | frame u16 | status (1 = ok)
            if len(payload) < 4 or payload[3] != 1:
                return completion(None, f"Pedal rejected preset frame {number} ({frame.hex_bytes(payload)})")
            self._write_frames(index, frames, number + 1, completion)

        self._send(header + data, RECOVERY_WRITE_REPLY, on_reply)

    def remove_effect(self, preset_index: int, position: int, expected_name: str,
                      expected_chain: List[Tuple[int, bool]], completion: Completion) -> None:
        """Removes the effect at `position` (0-based) from stored preset
        `preset_index`, after checking the stored copy matches what the app
        shows. The original is saved first and kept as `last_backup`."""
        def on_read(original, error):
            if error:
                return completion(None, error)
            problem = original.mismatch(expected_name, expected_chain)
            if problem:
                return completion(None, problem)
            try:
                updated = original.removing_module(position)
                backup_path = original.save_backup(preset_index)
            except (ValueError, OSError) as failure:
                return completion(None, str(failure))
            self.log(f"USB: backup of preset {slot_label(preset_index)} saved to {backup_path}")
            self.last_backup = Backup(preset_index, backup_path)
            self._write_and_verify(preset_index, updated, completion)

        self.read_preset(preset_index, on_read)

    def restore_last_backup(self, completion: Completion) -> None:
        """Writes `last_backup` back to its preset slot."""
        backup = self.last_backup
        if backup is None:
            return
        try:
            preset = CloudPreset.from_file_data(backup.path.read_bytes())
        except OSError:
            preset = None
        if preset is None:
            return completion(None, f"Backup file is unreadable: {backup.path}")

        def on_done(_, error):
            if error is None:
                self.last_backup = None
            completion(backup.preset_index if error is None else None, error)

        self._write_and_verify(backup.preset_index, preset, on_done)

    def _write_and_verify(self, index: int, preset: "CloudPreset", completion: Completion) -> None:
        def on_written(_, error):
            if error:
                return completion(None, error)

            def on_read_back(stored, read_error):
                if read_error:
                    return completion(None, read_error)
                if stored.data != preset.data:
                    return completion(None, f"Preset {slot_label(index)} read back differently after writing")
                completion(None, None)

            self.read_preset(index, on_read_back)

        self.write_preset(index, preset, on_written)

    # ---- Raw frames (for the PrimeM2Device session) ------------------------

    def write_frame(self, data: bytes) -> None:
        """Writes one already-framed command; the reply comes via `on_frame`."""
        if self._device is None:
            return
        error = self._write_report(data)
        if error:
            self.log(error)

    def _write_report(self, data: bytes) -> Optional[str]:
        """Studio (node-hid) writes [0 report ID, len, frame...]; so does hidapi."""
        report = bytes([0, len(data) & 0xFF]) + data
        try:
            with self._write_lock:
                written = self._device.write(report)
        except Exception as error:  # noqa: BLE001
            return f"USB: write failed: {error}"
        return None if written >= 0 else "USB: write failed"

    # ---- Request queue -----------------------------------------------------

    def _send(self, payload: bytes, reply: int, handler: Completion) -> None:
        self._queue.append(_Request(payload, reply, handler))
        self._pump()

    def _pump(self) -> None:
        self.is_busy = self._in_flight is not None or bool(self._queue)
        if self._in_flight is not None or not self._queue:
            return
        request = self._queue.pop(0)
        if self._device is None:
            request.handler(None, "Pedal not connected via USB")
            return self._pump()
        self._in_flight = request
        error = self._write_report(frame.encode(request.payload))
        if error:
            self.log(error)
            return self._finish(None, error)

        def on_timeout():
            stuck = self._in_flight
            if stuck is None:
                return
            self.log(f"USB: timeout waiting for reply {stuck.reply}")
            self._finish(None, f"The pedal didn't answer over USB (waiting for reply {stuck.reply})")

        self._timeout = self.scheduler.call_later(3.0, on_timeout)

    def _finish(self, payload: Optional[bytes], error: Optional[str]) -> None:
        if self._timeout:
            self._timeout.cancel()
            self._timeout = None
        request = self._in_flight
        if request is None:
            return
        self._in_flight = None
        request.handler(payload, error)
        self._pump()

    def _fail_all(self, message: str) -> None:
        pending = ([self._in_flight] if self._in_flight else []) + self._queue
        self._in_flight = None
        self._queue = []
        if self._timeout:
            self._timeout.cancel()
            self._timeout = None
        for request in pending:
            request.handler(None, message)
        self.is_busy = False

    # ---- Receive path ------------------------------------------------------

    def _did_receive(self, report: bytes) -> None:
        for raw in self._rx.feed(report):
            decoded = frame.decode(raw)
            if decoded is None or not decoded.crc_ok or not decoded.payload:
                self.log(f"USB: dropped frame with bad CRC ({len(raw)} bytes)")
                continue
            request = self._in_flight
            if request is not None and request.reply == decoded.payload[0]:
                self._finish(decoded.payload, None)
            elif self.on_frame:
                self.on_frame(decoded.payload)


class CloudPreset:
    """A stored preset in Mooer Studio's "cloud" format: 3 frames
    (322 + 512 + 364 bytes on the M2)."""

    BPM_OFFSET = 390
    NAME_OFFSET = 392
    NAME_LENGTH = 20
    MODULES_OFFSET = 412
    MODULE_SIZE = 36        # type u16 + 17 x u16 words
    MODULE_COUNT = 20
    CRC_START = 322
    CRC_OFFSET = 1196       # CRC-16/GSM over 322..<1196, little-endian
    SIZE = 1198

    def __init__(self, frame_lengths: List[int], data: bytes) -> None:
        self.frame_lengths = frame_lengths
        self.data = bytes(data)

    @classmethod
    def from_frames(cls, frames: List[bytes]) -> Optional["CloudPreset"]:
        data = b"".join(frames)
        if len(data) != cls.SIZE:
            return None
        return cls([len(f) for f in frames], data)

    @classmethod
    def from_file_data(cls, data: bytes) -> Optional["CloudPreset"]:
        """Studio's `.preset` file: u16 frameCount, u16 lengths..., frame data."""
        if len(data) < 2:
            return None
        count = data[0] | data[1] << 8
        if len(data) < 2 + 2 * count:
            return None
        lengths = [data[2 + 2 * k] | data[3 + 2 * k] << 8 for k in range(count)]
        offset = 2 + 2 * count
        frames = []
        for length in lengths:
            if offset + length > len(data):
                return None
            frames.append(data[offset:offset + length])
            offset += length
        return cls.from_frames(frames)

    @property
    def file_data(self) -> bytes:
        header = bytes([len(self.frame_lengths) & 0xFF, len(self.frame_lengths) >> 8])
        lengths = b"".join(bytes([n & 0xFF, n >> 8]) for n in self.frame_lengths)
        return header + lengths + self.data

    @property
    def frames(self) -> List[bytes]:
        out, offset = [], 0
        for length in self.frame_lengths:
            out.append(self.data[offset:offset + length])
            offset += length
        return out

    @property
    def name(self) -> str:
        return decode_name(self.data[self.NAME_OFFSET:self.NAME_OFFSET + self.NAME_LENGTH])

    def _computed_crc(self, data: Optional[bytes] = None) -> int:
        return frame.crc16((data or self.data)[self.CRC_START:self.CRC_OFFSET])

    @property
    def crc_is_valid(self) -> bool:
        stored = self.data[self.CRC_OFFSET] | self.data[self.CRC_OFFSET + 1] << 8
        return stored == self._computed_crc()

    def _word(self, offset: int) -> int:
        return self.data[offset] | self.data[offset + 1] << 8

    @property
    def chain(self) -> List[Tuple[int, bool]]:
        """Type and on/off of each used slot, in chain order."""
        out = []
        for k in range(self.MODULE_COUNT):
            offset = self.MODULES_OFFSET + k * self.MODULE_SIZE
            kind = self._word(offset)
            if kind:
                out.append((kind & 0xFF, self._word(offset + 2) != 0))
        return out

    def mismatch(self, expected_name: str, expected: List[Tuple[int, bool]]) -> Optional[str]:
        """None when this stored preset looks like the chain the app shows."""
        if not self.crc_is_valid:
            return "The stored preset's checksum doesn't match. Nothing was changed."
        if self.name != expected_name:
            return (f"The stored preset is named “{self.name}”, but the app shows "
                    f"“{expected_name}”. Nothing was changed.")
        stored = self.chain

        def describe(items):
            return ", ".join(f"{catalog.type_name(t)} {'on' if on else 'off'}" for t, on in items)

        if stored != list(expected):
            return (f"The stored preset ({describe(stored)}) differs from what the app shows "
                    f"({describe(expected)}). Save or re-select the preset, then try again. Nothing was changed.")
        return None

    def removing_module(self, position: int) -> "CloudPreset":
        """Drops one 36-byte module record, shifts the rest up, zeroes the
        freed last slot and recomputes the CRC."""
        used = len(self.chain)
        if not 0 <= position < used:
            raise ValueError(f"No effect at position {position + 1} (preset has {used})")
        start, size = self.MODULES_OFFSET, self.MODULE_SIZE
        records = [self.data[start + k * size:start + (k + 1) * size] for k in range(self.MODULE_COUNT)]
        del records[position]
        records.append(bytes(size))
        data = bytearray(self.data)
        data[start:start + self.MODULE_COUNT * size] = b"".join(records)
        crc = self._computed_crc(bytes(data))
        data[self.CRC_OFFSET] = crc & 0xFF
        data[self.CRC_OFFSET + 1] = crc >> 8
        return CloudPreset(self.frame_lengths, bytes(data))

    def save_backup(self, preset_index: int) -> Path:
        directory = backup_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{slot_label(preset_index)}-{time.strftime('%Y%m%d-%H%M%S')}.preset"
        path.write_bytes(self.file_data)
        return path


def backup_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/MooerPrimeMac/Backups"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "MooerPrime/Backups"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "MooerPrime/Backups"
