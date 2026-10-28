"""Generic BLE central plus the Prime M2 hookup (port of BluetoothManager.swift).

It scans, connects, discovers every service and characteristic on whatever
peripheral you connect to, reads and subscribes to everything it can and
logs raw hex. Once FFF2 notifications are on and FFF3 exists, the
`PrimeM2Device` session starts over Bluetooth.

bleak is asyncio-based, so it runs on its own event-loop thread. Every state
change is handed to the GUI thread through `post`; the GUI only reads state.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import frame
from .protocol import NOTIFY_CHARACTERISTIC, WRITE_CHARACTERISTIC, PrimeM2Device

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover - reported in the log at runtime
    BleakClient = BleakScanner = None

DISCONNECTED = "Disconnected"
SCANNING = "Scanning"
CONNECTING = "Connecting"
DISCOVERING = "Discovering services"
CONNECTED = "Connected"

_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def short_uuid(uuid: str) -> str:
    """"0000fff2-0000-1000-8000-00805f9b34fb" -> "FFF2", like CoreBluetooth."""
    uuid = uuid.lower()
    if uuid.endswith(_BASE_UUID_SUFFIX) and uuid.startswith("0000"):
        return uuid[4:8].upper()
    return uuid.upper()


@dataclass
class DiscoveredPeripheral:
    id: str
    name: str
    rssi: int
    device: object  # bleak BLEDevice


class BluetoothManager:
    def __init__(self, prime: PrimeM2Device, post: Callable[[Callable[[], None]], None],
                 log: Callable[[str], None] = print) -> None:
        self.prime = prime
        self.post = post
        self.log = log
        self.listeners: List[Callable[[], None]] = []

        self.available = BleakScanner is not None
        self.is_powered_on = self.available
        self.discovered: Dict[str, DiscoveredPeripheral] = {}
        self.connection_state = DISCONNECTED
        self.connected_name: Optional[str] = None

        self._seen_ids = set()
        self._scanner = None
        self._client = None
        self._characteristics: Dict[str, object] = {}
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="ble-loop", daemon=True).start()
        if not self.available:
            self.log("Bluetooth: the `bleak` package isn't installed; Bluetooth is disabled")

    # ---- Helpers -----------------------------------------------------------

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    def _on_main(self, fn: Callable[[], None]) -> None:
        def run():
            fn()
            self._changed()
        self.post(run)

    def _run(self, coroutine) -> None:
        asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def _set_state(self, state: str) -> None:
        def apply():
            self.connection_state = state
        self._on_main(apply)

    # ---- Scanning (called on the GUI thread) -------------------------------

    def start_scan(self) -> None:
        if not self.available:
            self.log("Cannot scan: bleak isn't installed")
            return
        self.discovered.clear()
        self._seen_ids.clear()
        self.connection_state = SCANNING
        self.log("Scanning for peripherals (all services)…")
        self._run(self._start_scan())
        self._changed()

    async def _start_scan(self) -> None:
        try:
            self._scanner = BleakScanner(detection_callback=self._did_discover)
            await self._scanner.start()
            self._on_main(lambda: setattr(self, "is_powered_on", True))
        except Exception as error:  # noqa: BLE001
            self._scanner = None
            self.log(f"Cannot scan: {error}")

            def failed():
                self.is_powered_on = False
                if self.connection_state == SCANNING:
                    self.connection_state = DISCONNECTED
            self._on_main(failed)

    def stop_scan(self) -> None:
        self._run(self._stop_scan())
        if self.connection_state == SCANNING:
            self.connection_state = DISCONNECTED
        self.log("Scan stopped.")
        self._changed()

    async def _stop_scan(self) -> None:
        scanner, self._scanner = self._scanner, None
        if scanner:
            try:
                await scanner.stop()
            except Exception as error:  # noqa: BLE001
                self.log(f"Stopping the scan failed: {error}")

    def _did_discover(self, device, advertisement) -> None:
        name = device.name or advertisement.local_name or "Unnamed device"
        if device.address not in self._seen_ids:
            self._seen_ids.add(device.address)
            self.log(f"Discovered: {name} [{device.address}] RSSI {advertisement.rssi} adv={advertisement}")
        item = DiscoveredPeripheral(device.address, name, advertisement.rssi, device)
        self._on_main(lambda: self.discovered.__setitem__(item.id, item))

    # ---- Connection --------------------------------------------------------

    def connect(self, item: DiscoveredPeripheral) -> None:
        self.stop_scan()
        self.connection_state = CONNECTING
        self.log(f"Connecting to {item.name} ({item.id})…")
        self._run(self._connect(item))
        self._changed()

    def disconnect(self) -> None:
        self._run(self._disconnect())

    async def _disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception as error:  # noqa: BLE001
                self.log(f"Disconnect failed: {error}")

    async def _connect(self, item: DiscoveredPeripheral) -> None:
        # Let a scan stop issued just before finish first.
        await self._stop_scan()
        client = BleakClient(item.device, disconnected_callback=self._did_disconnect)
        self._client = client
        try:
            await client.connect()
        except Exception as error:  # noqa: BLE001
            self.log(f"Failed to connect: {error}")
            self._client = None
            self._set_state(DISCONNECTED)
            return

        name = item.name

        def connected():
            self.connected_name = name
            self.connection_state = DISCOVERING
        self._on_main(connected)
        self.log(f"Connected to {name}. Discovering services…")

        characteristics: Dict[str, object] = {}
        notifying = set()
        for service in client.services:
            self.log(f"Service {short_uuid(service.uuid)}")
            for characteristic in service.characteristics:
                uuid = short_uuid(characteristic.uuid)
                props = characteristic.properties
                self.log(f"  Characteristic {uuid} props=[{','.join(props)}]")
                characteristics[uuid] = characteristic
                for descriptor in characteristic.descriptors:
                    self.log(f"    Descriptor {short_uuid(descriptor.uuid)} on {uuid}")
                if "read" in props:
                    try:
                        value = await client.read_gatt_char(characteristic)
                        self._did_update_value(uuid, bytes(value))
                    except Exception as error:  # noqa: BLE001
                        self.log(f"Value update error for {uuid}: {error}")
                if "notify" in props or "indicate" in props:
                    try:
                        await client.start_notify(
                            characteristic, lambda _, data, uuid=uuid: self._did_update_value(uuid, bytes(data)))
                        self.log(f"Notify on for {uuid}")
                        notifying.add(uuid)
                    except Exception as error:  # noqa: BLE001
                        self.log(f"Notify subscribe error for {uuid}: {error}")
        self._characteristics = characteristics
        self._set_state(CONNECTED)

        if NOTIFY_CHARACTERISTIC in notifying and WRITE_CHARACTERISTIC in characteristics:
            self._on_main(lambda: self.prime.bluetooth_ready(lambda data: self.write(data, WRITE_CHARACTERISTIC)))

    def _did_disconnect(self, _client) -> None:
        self.log("Disconnected.")
        self._client = None
        self._characteristics = {}

        def gone():
            self.connection_state = DISCONNECTED
            self.connected_name = None
            self.prime.bluetooth_gone()
        self._on_main(gone)

    # ---- Data --------------------------------------------------------------

    def _did_update_value(self, uuid: str, value: bytes) -> None:
        """Runs on the BLE thread; the protocol is fed on the GUI thread."""
        self.log(f"Value {uuid} <- {frame.hex_bytes(value) or '<empty>'}")
        decoded = frame.decode(value)
        if decoded:
            self.log(f"  ↳ {frame.describe(decoded)}")
        self.post(lambda: self.prime.did_receive_bluetooth(value, uuid))

    def write(self, data: bytes, characteristic_uuid: str) -> None:
        """Raw write to a discovered characteristic. Uses write-with-response
        when the characteristic supports it."""
        client = self._client
        characteristic = self._characteristics.get(characteristic_uuid)
        if client is None or characteristic is None:
            self.log(f"Cannot write: {characteristic_uuid} not available (not connected?)")
            return
        with_response = "write" in characteristic.properties
        self.log(f"Write {characteristic_uuid} -> {frame.hex_bytes(data)}{'' if with_response else ' (no response)'}")
        self._run(self._write(client, characteristic, characteristic_uuid, data, with_response))

    async def _write(self, client, characteristic, uuid: str, data: bytes, with_response: bool) -> None:
        try:
            await client.write_gatt_char(characteristic, data, response=with_response)
            if with_response:
                self.log(f"Write confirmed for {uuid}")
        except Exception as error:  # noqa: BLE001
            self.log(f"Write error for {uuid}: {error}")

    def shutdown(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            future.result(timeout=3)
        except Exception:  # noqa: BLE001
            pass

    async def _shutdown(self) -> None:
        await self._stop_scan()
        await self._disconnect()
