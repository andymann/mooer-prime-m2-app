"""Protocol tests against a simulated pedal (no hardware needed).

    python -m unittest discover -s tests
"""

import heapq
import itertools
import unittest

from mooer_prime import catalog, frame, protocol
from mooer_prime.models import PrimeModule, PrimePreset, encode_name, slot_label
from mooer_prime.usb import CloudPreset


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class FakeScheduler:
    """`call_later` on a virtual clock; `run(seconds)` advances it."""

    def __init__(self, clock):
        self.clock = clock
        self.heap = []
        self.counter = itertools.count()

    def call_later(self, seconds, fn):
        entry = [self.clock.now + seconds, next(self.counter), fn, False]
        heapq.heappush(self.heap, entry)

        class Handle:
            def cancel(self_inner):
                entry[3] = True
        return Handle()

    def run(self, seconds):
        end = self.clock.now + seconds
        while self.heap and self.heap[0][0] <= end:
            when, _, fn, cancelled = heapq.heappop(self.heap)
            self.clock.now = max(self.clock.now, when)
            if not cancelled:
                fn()
        self.clock.now = end


def module_words(kind, model):
    return [1, model] + [50] * {1: 3, 7: 3}.get(kind, 4)


class FakePedal:
    """Answers the M2 command set like the real pedal does (simplified)."""

    def __init__(self, scheduler, deliver, chunk=None, full_dsp=False):
        self.scheduler = scheduler
        self.deliver = deliver
        self.chunk = chunk
        self.full_dsp = full_dsp
        self.sent = []
        self.active = 5
        self.names = {i: f"Preset {i}" for i in range(1, 81)}
        self.edit = [PrimeModule(1, 1, module_words(1, 1)), PrimeModule(2, 7, module_words(7, 0))]

    def chain_bytes(self):
        out = b""
        for slot in range(1, 21):
            module = next((m for m in self.edit if m.slot == slot), PrimeModule(slot, 0, []))
            out += module.encoded()
        return out

    def write(self, data):
        decoded = frame.decode(data)
        assert decoded and decoded.crc_ok
        payload = decoded.payload
        self.sent.append(payload)
        reply = self.answer(payload)
        if reply is not None:
            self.scheduler.call_later(0.01, lambda: self.send(reply))

    def send(self, payload):
        data = frame.encode(payload)
        if self.chunk:
            for i in range(0, len(data), self.chunk):
                self.deliver(data[i:i + self.chunk])
        else:
            self.deliver(data)

    def state(self):
        state = bytearray(99)
        state[0] = 0x12
        state[1] = self.active
        state[41], state[42] = 120, 0
        return bytes(state)

    def answer(self, p):
        op = p[0]
        if op == 0x01:
            return b"\x02\x41\x01V1.5.0\x00"
        if op == 0x29:
            return bytes([0x2A, p[1], p[2]]) + b"".join(encode_name(self.names[i]) for i in range(p[1], p[2] + 1))
        if op == 0xA9:
            return b"\xaa\x00\x00iPedal-TEST\x00"
        if op == 0x11:
            return self.state()
        if op == 0x91:
            return b"\x92\x55"
        if op == 0x21:
            return bytes([0x22, p[1]]) + encode_name(self.names[p[1]]) + self.chain_bytes() + b"\x00\x00"
        if op == 0x33:
            return b"\x34\x78\x00"
        if op == 0x13:
            self.active = p[1]
            return b"\x14\x01"
        if op == 0x25:
            parsed = PrimeModule.parse(p, 1)
            module = parsed[0]
            self.edit = [module if m.slot == module.slot else m for m in self.edit]
            return b"\x26\x01"
        if op == 0x2D:
            edit_id, action, slot, kind, model = p[1:6]
            if action == 3:
                self.edit = [m for m in self.edit if m.slot != slot]
                for i, m in enumerate(self.edit):
                    self.edit[i] = PrimeModule(i + 1, m.type, m.words)
            elif not self.full_dsp:
                module = PrimeModule(slot, kind, module_words(kind, model))
                self.edit = [m for m in self.edit if m.slot != slot] + [module]
                self.edit.sort(key=lambda m: m.slot)
            return bytes([0x2E, edit_id, 0x01])
        if op == 0x23:
            module = next((m for m in self.edit if m.slot == p[1]), PrimeModule(p[1], 0, []))
            return b"\x24" + module.encoded()
        if op == 0x2F:
            self.names[p[1]] = p[2:22].rstrip(b"\x00").decode()
            return b"\x30\x01"
        return None


class SessionTest(unittest.TestCase):
    def make(self, link):
        clock = FakeClock()
        self._real_time = protocol.time
        protocol.time = clock
        self.addCleanup(lambda: setattr(protocol, "time", self._real_time))
        scheduler = FakeScheduler(clock)
        self.logs = []
        device = protocol.PrimeM2Device(scheduler, self.logs.append)
        if link == "usb":
            def deliver(data):
                decoded = frame.decode(data)
                device.did_receive_usb(decoded.payload)
            pedal = FakePedal(scheduler, deliver)
            device.usb_ready(pedal.write)
        else:
            pedal = FakePedal(scheduler, lambda d: device.did_receive_bluetooth(d, "FFF2"), chunk=20)
            device.bluetooth_ready(pedal.write)
        return device, pedal, scheduler

    def test_sync_and_scan(self):
        for link in ("usb", "ble"):
            with self.subTest(link=link):
                device, pedal, scheduler = self.make(link)
                scheduler.run(30)
                self.assertTrue(device.is_ready)
                self.assertEqual(device.firmware, "V1.5.0")
                self.assertEqual(device.serial, "iPedal-TEST")
                self.assertEqual(device.battery, 0x55)
                self.assertEqual(device.tempo, 120)
                self.assertEqual(device.active_preset_index, 5)
                self.assertEqual(len(device.preset_names), 80)
                self.assertEqual(device.preset.index, 5)
                self.assertEqual(len(device.preset_cache), 80)
                self.assertIn("All 80 presets read", self.logs)
                self.assertFalse(device.is_busy)

    def test_select_edit_coalesce_save(self):
        device, pedal, scheduler = self.make("usb")
        scheduler.run(30)
        device.select_preset(9)
        self.assertEqual(device.displayed_preset_index, 9)
        scheduler.run(2)
        self.assertEqual(device.active_preset_index, 9)
        self.assertEqual(device.preset.index, 9)

        # Quiet period after a select over USB: nothing for 300 ms.
        before = len(pedal.sent)
        device.select_preset(10)
        scheduler.run(0.05)
        module = device.preset.modules[0]
        for value in range(10, 60):   # a slider drag
            module = module.with_word(2, value)
            device.update_module(module)
        scheduler.run(0.1)
        self.assertEqual(len(pedal.sent), before + 1)   # only the select so far
        scheduler.run(2)
        edits = [p for p in pedal.sent[before:] if p[0] == 0x25]
        self.assertLess(len(edits), 5)                   # coalesced
        self.assertEqual(pedal.edit[0].words[2], 59)     # latest value wins

        device.save_preset(12, "My Tone")
        scheduler.run(3)
        self.assertEqual(pedal.names[12], "My Tone")
        self.assertEqual(device.active_preset_index, 12)
        self.assertEqual(device.preset_names[12], "My Tone")

    def test_place_and_delete(self):
        device, pedal, scheduler = self.make("ble")
        scheduler.run(30)
        device.place_module(3, 0x08, 2)        # add Reverb Plate
        scheduler.run(2)
        self.assertIsNone(device.notice)
        self.assertEqual(device.preset.modules[2].type, 0x08)
        self.assertEqual(device.preset.modules[2].model, 2)
        self.assertEqual(pedal.sent[-2][:4], bytes([0x2D, 1, 1, 3]))   # op 1 = add

        device.place_module(1, 0x01, 4)        # change slot 1 (op 2)
        scheduler.run(2)
        self.assertEqual(pedal.sent[-2][2], 2)
        self.assertEqual(device.preset.modules[0].model, 4)

        device.delete_module(1)
        scheduler.run(2)
        self.assertEqual([m.type for m in device.preset.modules if not m.is_empty], [7, 8])

    def test_full_dsp_notice(self):
        device, pedal, scheduler = self.make("ble")
        pedal.full_dsp = True
        scheduler.run(30)
        device.place_module(3, 0x08, 0)
        scheduler.run(2)
        self.assertIn("DSP is full", device.notice)

    def test_timeout_retry(self):
        device, pedal, scheduler = self.make("usb")
        scheduler.run(30)
        original = pedal.answer
        dropped = []

        def flaky(p):
            if p[0] == 0x21 and not dropped:
                dropped.append(p)
                return None
            return original(p)

        pedal.answer = flaky
        device.load_preset(7)
        scheduler.run(5)
        self.assertTrue(any("retrying" in line for line in self.logs))
        self.assertEqual(device.preset.index, 7)

    def test_link_switch_resyncs(self):
        device, pedal, scheduler = self.make("ble")
        scheduler.run(30)
        usb_pedal = FakePedal(scheduler, lambda d: device.did_receive_usb(frame.decode(d).payload))
        device.usb_ready(usb_pedal.write)
        self.assertEqual(device.link, protocol.LINK_USB)
        scheduler.run(30)
        self.assertTrue(device.is_ready)
        device.usb_gone()
        self.assertEqual(device.link, protocol.LINK_BLUETOOTH)


class FormatTest(unittest.TestCase):
    def test_frame_roundtrip(self):
        data = frame.encode(b"\x21\x05")
        self.assertEqual(data[:4], b"\xaa\x55\x02\x00")
        decoded = frame.decode(data)
        self.assertTrue(decoded.crc_ok)
        self.assertEqual(decoded.payload, b"\x21\x05")
        self.assertFalse(frame.decode(data[:-1] + bytes([data[-1] ^ 1])).crc_ok)
        self.assertEqual(frame.parse_hex("0xaa, 0x55:0a"), b"\xaa\x55\x0a")

    def test_reassembler_resyncs(self):
        reassembler = frame.FrameReassembler()
        a, b = frame.encode(b"\x01"), frame.encode(b"\x02\x03")
        stream = b"\x00\x00\xaa" + a + bytes(1000) + b
        frames = []
        for i in range(0, len(stream), 7):
            frames += list(reassembler.feed(stream[i:i + 7]))
        self.assertEqual(frames, [a, b])

    def test_catalog(self):
        self.assertEqual(sum(len(v) for v in catalog.MODEL_NAMES.values()), 277)
        self.assertEqual(catalog.model_label(1, 1), "02 Flex Boost")
        self.assertEqual(catalog.format_value(0x800A, catalog.ParamKind.MILLISECONDS), "♩ 1/4")
        self.assertEqual(catalog.format_value(111, catalog.ParamKind.RATE), "♩ 1/4")
        self.assertEqual(catalog.format_value(40, catalog.ParamKind.EQ_GAIN), "+4.0 dB")
        self.assertEqual(catalog.format_value(189, catalog.ParamKind.PITCH), "6.9")
        self.assertIsNone(catalog.slider_range(catalog.param(7, 0, 2), 0x800A))
        self.assertEqual(catalog.slider_range(catalog.param(7, 0, 2), 500), (40, 2500))
        self.assertEqual(slot_label(1), "0A")
        self.assertEqual(slot_label(80), "19D")

    def test_preset_parse(self):
        modules = b"".join(PrimeModule(s, 1 if s == 1 else 0, [1, 2, 3] if s == 1 else []).encoded()
                           for s in range(1, 21))
        preset = PrimePreset.from_payload(b"\x22\x05" + encode_name("Clean") + modules + b"\x09")
        self.assertEqual(preset.name, "Clean")
        self.assertEqual(preset.modules[0].words, [1, 2, 3])
        self.assertEqual(preset.trailer, b"\x09")

    def test_cloud_preset_remove(self):
        data = bytearray(CloudPreset.SIZE)
        data[CloudPreset.NAME_OFFSET:CloudPreset.NAME_OFFSET + 5] = b"Clean"
        for k, kind in enumerate((1, 3, 8)):
            offset = CloudPreset.MODULES_OFFSET + k * CloudPreset.MODULE_SIZE
            data[offset] = kind
            data[offset + 2] = 1
        crc = frame.crc16(data[CloudPreset.CRC_START:CloudPreset.CRC_OFFSET])
        data[CloudPreset.CRC_OFFSET:CloudPreset.CRC_OFFSET + 2] = bytes([crc & 0xFF, crc >> 8])
        preset = CloudPreset.from_frames([bytes(data[:322]), bytes(data[322:834]), bytes(data[834:])])
        self.assertTrue(preset.crc_is_valid)
        self.assertIsNone(preset.mismatch("Clean", [(1, True), (3, True), (8, True)]))
        removed = preset.removing_module(1)
        self.assertEqual(removed.chain, [(1, True), (8, True)])
        self.assertTrue(removed.crc_is_valid)
        again = CloudPreset.from_file_data(removed.file_data)
        self.assertEqual(again.data, removed.data)


if __name__ == "__main__":
    unittest.main()
