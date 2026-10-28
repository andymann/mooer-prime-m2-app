"""Prime M2 protocol session over either link (port of PrimeM2Protocol.swift).

- Every command is an `aa 55` frame, written to FFF3 over BLE or as a HID
  report over USB: the M2 answers the same command set on both. USB is
  preferred whenever the pedal is plugged in.
- Over BLE replies arrive as FFF2 notifications, possibly split; they are
  reassembled by the frame's length field. USB hands over whole frames.
- A reply's opcode is the request's opcode + 1. Exactly one request is in
  flight, like the official app does.
- Unsolicited frames: 0x15 (preset changed on the pedal), 0x34 (tempo),
  0x9e (USB heartbeat).

Everything here runs on one thread (the GUI's). Timing goes through the
injected `scheduler.call_later(seconds, fn) -> handle with .cancel()`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import catalog, frame
from .frame import FrameReassembler
from .models import NAME_LENGTH, PrimeModule, PrimePreset, decode_name, encode_name, slot_label

WRITE_CHARACTERISTIC = "FFF3"
NOTIFY_CHARACTERISTIC = "FFF2"

LINK_USB = "USB"
LINK_BLUETOOTH = "Bluetooth"

Handler = Callable[[bytes], None]
Writer = Callable[[bytes], None]

_RETRYABLE_READS = {0x01, 0x11, 0x21, 0x23, 0x29, 0x33, 0x91, 0xA9}


@dataclass
class _Request:
    payload: bytes
    # Requests with the same key replace each other while still queued, so
    # dragging a slider only ever leaves the latest value pending.
    coalesce_key: Optional[str]
    handler: Handler
    retried: bool = False

    @property
    def reply(self) -> int:
        return (self.payload[0] + 1) & 0xFF

    @property
    def is_background(self) -> bool:
        """Periodic polls don't count as "busy"."""
        return self.coalesce_key in ("state", "battery")

    @property
    def is_retryable_read(self) -> bool:
        return self.payload[0] in _RETRYABLE_READS


class PrimeM2Device:
    def __init__(self, scheduler, log: Callable[[str], None] = print) -> None:
        self.scheduler = scheduler
        self.log = log
        self.listeners: List[Callable[[], None]] = []

        self.is_ready = False
        self.firmware: Optional[str] = None
        self.serial: Optional[str] = None
        self.battery: Optional[int] = None
        self.tempo: Optional[int] = None
        self.preset_names: Dict[int, str] = {}
        self.active_preset_index: Optional[int] = None
        # Preset the user just picked, until the pedal confirms or rejects
        # it, so the list highlight doesn't jump back while waiting.
        self.requested_preset_index: Optional[int] = None
        # Last refused edit worth showing in the editor.
        self.notice: Optional[str] = None
        self.preset: Optional[PrimePreset] = None
        # Every preset read so far, filled by a background scan after sync.
        self.preset_cache: Dict[int, PrimePreset] = {}
        self.is_busy = False
        self.link: Optional[str] = None

        self._bluetooth_writer: Optional[Writer] = None
        self._usb_writer: Optional[Writer] = None
        # The 99-byte system-state payload from 0x12/0x15, opcode included.
        # Preset select is "send it back as 0x13 with [1] changed".
        self._system_state: Optional[bytearray] = None
        self._rx = FrameReassembler()
        self._queue: List[_Request] = []
        self._in_flight: Optional[_Request] = None
        self._timeout = None
        # Over USB the pedal ignores every command for ~200 ms after a preset
        # select, so nothing is sent before this time (time.monotonic()).
        self._quiet_until: Optional[float] = None
        self._quiet_timer = None
        self._battery_timer = None
        # USB only: the pedal doesn't push preset/tempo changes, so poll.
        self._state_timer = None
        # Preset indices still to read in the background, only when idle.
        self._scan_queue: List[int] = []
        self._scan_logged = False
        # Echoed back by `2e`; just a running number.
        self._module_edit_id = 0

    @property
    def displayed_preset_index(self) -> Optional[int]:
        return self.requested_preset_index if self.requested_preset_index is not None else self.active_preset_index

    @property
    def scan_remaining(self) -> int:
        return len(self._scan_queue)

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    # ---- Links -------------------------------------------------------------

    def bluetooth_ready(self, write: Writer) -> None:
        """BLE: FFF2 notifications are on and FFF3 is writable."""
        self._bluetooth_writer = write
        self._choose_link()

    def bluetooth_gone(self) -> None:
        self._bluetooth_writer = None
        self._choose_link()

    def usb_ready(self, write: Writer) -> None:
        """USB: the HID interface is open."""
        self._usb_writer = write
        self._choose_link()

    def usb_gone(self) -> None:
        self._usb_writer = None
        self._choose_link()

    def _choose_link(self) -> None:
        """Prefers USB. Switching links restarts the session (full resync)."""
        preferred = LINK_USB if self._usb_writer else (LINK_BLUETOOTH if self._bluetooth_writer else None)
        if preferred == self.link:
            return
        self.link = preferred
        if preferred:
            self.log(f"Prime M2 session over {preferred}")
            self._session_did_start()
        else:
            self._reset()
        self._changed()

    @property
    def _writer(self) -> Optional[Writer]:
        if self.link == LINK_USB:
            return self._usb_writer
        if self.link == LINK_BLUETOOTH:
            return self._bluetooth_writer
        return None

    # ---- Session lifecycle -------------------------------------------------

    def _session_did_start(self) -> None:
        """Runs the same sync sequence the official app runs after connecting."""
        self._reset()
        self.log("Prime M2 session starting")

        def on_firmware(payload: bytes) -> None:
            # 02 41 01 "V1.5.0" 00 ...
            self.firmware = decode_name(payload[3:]) if len(payload) > 3 else None

        def on_serial(payload: bytes) -> None:
            self.serial = decode_name(payload[3:]) if len(payload) > 3 else None

        def on_state(payload: bytes) -> None:
            self._apply_system_state(payload)
            self.is_ready = True
            active = slot_label(self.active_preset_index) if self.active_preset_index else "?"
            self.log(f"Synced over {self.link}: firmware {self.firmware or '?'}, "
                     f"{len(self.preset_names)} preset names, active {active}, tempo {self.tempo or 0}")
            if self.active_preset_index is not None:
                self.load_preset(self.active_preset_index)
            self._scan_queue = list(range(1, 81))

        self._send(b"\x01", on_firmware)
        self._request_preset_names(1, 40)
        self._request_preset_names(41, 80)
        self._send(b"\xa9", on_serial)
        self._send(b"\x11", on_state)
        self._poll_battery()
        self._battery_timer = self._repeat(30, self._poll_battery)
        if self.link == LINK_USB:
            self._state_timer = self._repeat(1, self._poll_state)

    def _repeat(self, seconds: float, fn: Callable[[], None]):
        """A cancellable repeating timer built on `call_later`."""
        device = self

        class Repeating:
            handle = None
            cancelled = False

            def fire(self):
                if self.cancelled:
                    return
                fn()
                if not self.cancelled:
                    self.handle = device.scheduler.call_later(seconds, self.fire)

            def cancel(self):
                self.cancelled = True
                if self.handle:
                    self.handle.cancel()

        timer = Repeating()
        timer.handle = self.scheduler.call_later(seconds, timer.fire)
        return timer

    def _reset(self) -> None:
        for timer in (self._battery_timer, self._state_timer, self._timeout, self._quiet_timer):
            if timer:
                timer.cancel()
        self._battery_timer = self._state_timer = self._timeout = self._quiet_timer = None
        self._queue.clear()
        self._in_flight = None
        self._quiet_until = None
        self.requested_preset_index = None
        self._rx.clear()
        self._scan_queue.clear()
        self._scan_logged = False
        self.preset_cache = {}
        self._system_state = None
        self.is_ready = False
        self.is_busy = False
        self.firmware = None
        self.serial = None
        self.battery = None
        self.tempo = None
        self.preset_names = {}
        self.active_preset_index = None
        self.preset = None

    # ---- Commands ----------------------------------------------------------

    def select_preset(self, index: int) -> None:
        state = self._system_state
        if state is None or len(state) <= 1 or not (1 <= index <= 80):
            self.log(f"Preset select {index} skipped: no system state yet")
            return
        self.log(f"Selecting preset {slot_label(index)}")
        self.requested_preset_index = index
        request = bytearray(state)
        request[0] = 0x13
        request[1] = index

        def on_reply(payload: bytes) -> None:
            if self.requested_preset_index == index:
                self.requested_preset_index = None
            if len(payload) <= 1 or payload[1] != 0x01:
                self.log(f"Preset select {index} rejected: {frame.hex_bytes(payload)}")
                return
            if self._system_state is not None:
                self._system_state[1] = index
            self.active_preset_index = index
            self.load_preset(index)

        self._send(bytes(request), on_reply)
        self._changed()

    def load_preset(self, index: int) -> None:
        def on_preset(payload: bytes) -> None:
            preset = PrimePreset.from_payload(payload)
            if preset is None:
                self.log(f"Could not parse preset {index} ({len(payload)} bytes)")
                return
            self.preset = preset
            self._cache(preset)
            chain = [f"{m.slot}:{m.type_name} {catalog.model_label(m.type, m.model or 0)}"
                     for m in preset.modules if not m.is_empty]
            self.log(f"Loaded preset {slot_label(index)} “{preset.name}”: "
                     + (", ".join(chain) if chain else "empty"))

        self._send(bytes([0x21, index]), on_preset)
        self._send(bytes([0x33, index]), self._apply_tempo)

    def update_module(self, module: PrimeModule) -> None:
        """Live edit of one slot (`25 slot type n data`). Affects the edit
        buffer immediately; persists only after `save_preset`."""
        self._store_module(module)

        def on_reply(payload: bytes) -> None:
            if len(payload) < 2 or payload[1] != 0x01:
                self.log(f"Module {module.slot} update rejected: {frame.hex_bytes(payload)}")

        self._send(b"\x25" + module.encoded(), on_reply, coalesce_key=f"module-{module.slot}")
        self._changed()

    def _store_module(self, module: PrimeModule) -> None:
        if self.preset is None:
            return
        for i, existing in enumerate(self.preset.modules):
            if existing.slot == module.slot:
                self.preset.modules[i] = module
                self.preset_cache[self.preset.index] = self.preset
                return

    # ---- Adding, changing and removing modules -----------------------------

    def place_module(self, slot: int, kind: int, model: int) -> None:
        """`2d id op slot type model` -> `2e id ok`. op 1 puts an effect into
        an empty slot, op 2 changes the effect in a used one; the pedal loads
        that model's defaults. A full DSP still answers ok but changes
        nothing, so the slot is read back and compared."""
        occupied = False
        if self.preset:
            occupied = any(m.slot == slot and not m.is_empty for m in self.preset.modules)
        wanted = f"{catalog.type_name(kind)} {catalog.model_label(kind, model)}"
        self.notice = None
        self._module_edit_id = (self._module_edit_id + 1) & 0xFF
        self.log(f"{'Changing' if occupied else 'Adding'} slot {slot} → {wanted}")

        def on_reply(payload: bytes) -> None:
            if len(payload) <= 2 or payload[2] != 0x01:
                self.notice = f"The pedal rejected {wanted} in slot {slot}."
                self.log(self.notice)
                return

            def on_slot(module: Optional[PrimeModule]) -> None:
                if module:
                    words = " ".join(map(str, module.words))
                    self.log(f"Slot {slot} now: {module.type_name} "
                             f"{catalog.model_label(module.type, module.model or 0)} | {words}")
                else:
                    self.log(f"Slot {slot} now: unreadable")
                if module is None or module.type != kind or module.model != model:
                    self.notice = (f"The pedal didn't take {wanted} in slot {slot}, probably because its DSP "
                                   "is full. Remove or change another effect first.")
                    self.log(self.notice)

            self._read_slot(slot, on_slot)

        self._send(bytes([0x2D, self._module_edit_id, 2 if occupied else 1, slot, kind, model & 0xFF]), on_reply)
        self._changed()

    def delete_module(self, slot: int) -> None:
        """`2d id 3 slot` deletes the slot; later slots move up, so the whole
        edit buffer is re-read."""
        if self.preset is None:
            return
        index = self.preset.index
        self.notice = None
        self._module_edit_id = (self._module_edit_id + 1) & 0xFF
        self.log(f"Removing slot {slot}")

        def on_reply(payload: bytes) -> None:
            if len(payload) <= 2 or payload[2] != 0x01:
                self.notice = f"The pedal refused to remove slot {slot}."
                self.log(self.notice)
                return
            self.load_preset(index)

        self._send(bytes([0x2D, self._module_edit_id, 3, slot, 0, 0]), on_reply)
        self._changed()

    def _read_slot(self, slot: int, done: Callable[[Optional[PrimeModule]], None]) -> None:
        """`23 slot` -> `24 slot type n data` from the edit buffer."""
        def on_reply(payload: bytes) -> None:
            parsed = PrimeModule.parse(payload, 1)
            module = parsed[0] if parsed else None
            if module:
                self._store_module(module)
            done(module)

        self._send(bytes([0x23, slot]), on_reply)

    def save_preset(self, index: int, name: str) -> None:
        """Saves the edit buffer into preset `index` under `name`
        (`2f pp name[20]`), then selects it, as the official app does."""
        def on_reply(payload: bytes) -> None:
            if len(payload) <= 1 or payload[1] != 0x01:
                self.log(f"Save to {index} rejected: {frame.hex_bytes(payload)}")
                return
            self.preset_names[index] = name
            self.select_preset(index)

        self._send(bytes([0x2F, index]) + encode_name(name), on_reply)

    def _request_preset_names(self, start: int, end: int) -> None:
        def on_reply(payload: bytes) -> None:
            # 2a start end name[20] x (end - start + 1)
            if len(payload) < 3:
                return
            index = payload[1]
            offset = 3
            while offset + NAME_LENGTH <= len(payload):
                self.preset_names[index] = decode_name(payload[offset:offset + NAME_LENGTH])
                offset += NAME_LENGTH
                index += 1

        self._send(bytes([0x29, start, end]), on_reply)

    def _cache(self, preset: PrimePreset) -> None:
        self.preset_cache[preset.index] = preset
        self.preset_names[preset.index] = preset.name
        self._scan_queue = [i for i in self._scan_queue if i != preset.index]
        if not self._scan_logged and len(self.preset_cache) == 80 and not self._scan_queue:
            self._scan_logged = True
            self.log("All 80 presets read")

    def _scan_next(self) -> bool:
        """Reads one not-yet-cached preset; called from `_pump` when idle."""
        while self._scan_queue:
            index = self._scan_queue.pop(0)
            if index in self.preset_cache:
                continue

            def on_preset(payload: bytes) -> None:
                preset = PrimePreset.from_payload(payload)
                if preset:
                    self._cache(preset)

            self._send(bytes([0x21, index]), on_preset)
            return True
        return False

    def _poll_state(self) -> None:
        """Picks up preset and tempo changes made on the pedal itself (USB)."""
        if not self.is_ready or self._in_flight or self._queue:
            return

        def on_state(payload: bytes) -> None:
            if len(payload) <= 42:
                return
            previous = self.active_preset_index
            self._apply_system_state(payload)
            index = self.active_preset_index
            if index is not None and index != previous:
                self.log(f"Pedal switched to preset {slot_label(index)}")
                self.load_preset(index)

        self._send(b"\x11", on_state, coalesce_key="state")

    def _poll_battery(self) -> None:
        def on_battery(payload: bytes) -> None:
            if len(payload) > 1:
                self.battery = payload[1]

        self._send(b"\x91", on_battery, coalesce_key="battery")

    # ---- Request queue -----------------------------------------------------

    def _send(self, payload: bytes, handler: Handler, coalesce_key: Optional[str] = None) -> None:
        request = _Request(bytes(payload), coalesce_key, handler)
        if coalesce_key is not None:
            for i, queued in enumerate(self._queue):
                if queued.coalesce_key == coalesce_key:
                    self._queue[i] = request
                    break
            else:
                self._queue.append(request)
        else:
            self._queue.append(request)
        self._pump()

    def _pump(self) -> None:
        if self._in_flight is None and not self._queue and self.is_ready and self._scan_next():
            return
        busy = (self._in_flight is not None and not self._in_flight.is_background) or \
            any(not r.is_background for r in self._queue)
        if busy != self.is_busy:
            self.is_busy = busy
            self._changed()
        writer = self._writer
        if self._in_flight is not None or not self._queue or writer is None:
            return
        if self._quiet_until is not None:
            remaining = self._quiet_until - time.monotonic()
            if remaining > 0:
                if self._quiet_timer is None:
                    def after_quiet():
                        self._quiet_timer = None
                        self._pump()
                    self._quiet_timer = self.scheduler.call_later(remaining, after_quiet)
                return
        request = self._queue.pop(0)
        self._in_flight = request
        writer(frame.encode(request.payload))
        self._timeout = self.scheduler.call_later(2.0, self._on_timeout)

    def _on_timeout(self) -> None:
        stuck = self._in_flight
        if stuck is None:
            return
        self._in_flight = None
        if stuck.is_retryable_read and not stuck.retried:
            self.log(f"No reply 0x{stuck.reply:02x}, retrying")
            stuck.retried = True
            self._queue.insert(0, stuck)
        else:
            self.log(f"Timeout waiting for reply 0x{stuck.reply:02x}")
            if stuck.payload[0] == 0x13:
                self.requested_preset_index = None
        self._pump()
        self._changed()

    # ---- Receive path ------------------------------------------------------

    def did_receive_usb(self, payload: bytes) -> None:
        """A whole frame's payload from USB."""
        if self.link != LINK_USB or not payload:
            return
        self._handle(payload)

    def did_receive_bluetooth(self, data: bytes, characteristic_uuid: str) -> None:
        """Raw FFF2 notification bytes from BLE."""
        if characteristic_uuid != NOTIFY_CHARACTERISTIC or self.link != LINK_BLUETOOTH:
            return
        for raw in self._rx.feed(data):
            decoded = frame.decode(raw)
            if decoded is None or not decoded.crc_ok or not decoded.payload:
                self.log(f"Dropped frame with bad CRC: {frame.hex_bytes(raw)}")
                continue
            self._handle(decoded.payload)

    def _handle(self, payload: bytes) -> None:
        opcode = payload[0]
        request = self._in_flight
        if request is not None and request.reply == opcode:
            if self._timeout:
                self._timeout.cancel()
                self._timeout = None
            self._in_flight = None
            # A preset select (14) or save (30) leaves the pedal deaf for a
            # moment over USB; adding/removing a module (2e) rebuilds its DSP
            # chain, so give it the same pause.
            if self.link == LINK_USB and opcode in (0x14, 0x30, 0x2E):
                self._quiet_until = time.monotonic() + 0.3
            request.handler(payload)
            self._pump()
            self._changed()
            return
        if opcode == 0x15:
            # Preset changed on the pedal itself.
            self._apply_system_state(payload)
            if self.active_preset_index is not None:
                self.load_preset(self.active_preset_index)
        elif opcode == 0x34:
            self._apply_tempo(payload)
        elif opcode == 0x9E:
            pass  # USB heartbeat, every ~6 s
        else:
            self.log(f"Unsolicited 0x{opcode:02x}: {frame.hex_bytes(payload[1:])}")
        self._changed()

    def _apply_system_state(self, payload: bytes) -> None:
        if len(payload) <= 42:
            return
        self._system_state = bytearray(payload)
        self.active_preset_index = payload[1]
        self.tempo = payload[41] | payload[42] << 8

    def _apply_tempo(self, payload: bytes) -> None:
        if len(payload) > 2:
            self.tempo = payload[1] | payload[2] << 8
