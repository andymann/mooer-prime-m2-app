import Foundation
import CoreBluetooth

struct DiscoveredPeripheral: Identifiable, Hashable {
    let id: UUID
    let name: String
    let rssi: Int
    let peripheral: CBPeripheral

    static func == (lhs: DiscoveredPeripheral, rhs: DiscoveredPeripheral) -> Bool {
        lhs.id == rhs.id
    }
    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}

struct GattCharacteristicInfo: Identifiable, Hashable {
    let id: String
    let uuid: String
    var properties: String
    var lastValueHex: String?

    static func == (lhs: GattCharacteristicInfo, rhs: GattCharacteristicInfo) -> Bool { lhs.id == rhs.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

struct GattServiceInfo: Identifiable, Hashable {
    let id: String
    let uuid: String
    var characteristics: [GattCharacteristicInfo]

    static func == (lhs: GattServiceInfo, rhs: GattServiceInfo) -> Bool { lhs.id == rhs.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

enum ConnectionState: String {
    case disconnected = "Disconnected"
    case scanning = "Scanning"
    case connecting = "Connecting"
    case discoveringServices = "Discovering services"
    case connected = "Connected"
}

/// Generic CoreBluetooth central. Deliberately protocol-agnostic right now:
/// it scans, connects, and recursively discovers every service,
/// characteristic, and descriptor on whatever peripheral you connect to,
/// reading and subscribing to everything it can, and logging raw hex bytes.
///
/// This is the tool for Phase B: point it at the real Prime M2 and the log
/// is exactly how we learn the real GATT service/characteristic UUIDs with
/// zero protocol knowledge, before the Android HCI-snoop capture tells us
/// the command byte format for the write characteristic.
final class BluetoothManager: NSObject, ObservableObject {
    @Published var isPoweredOn = false
    @Published var discovered: [DiscoveredPeripheral] = []
    @Published var connectionState: ConnectionState = .disconnected
    @Published var connectedName: String?
    @Published var services: [GattServiceInfo] = []
    @Published var log: [String] = []

    /// Protocol session for the Prime M2; started once FFF2 notifications
    /// are on. Generic GATT logging below keeps working alongside it.
    let prime = PrimeM2Device()
    /// USB side of the same pedal; the session prefers it when plugged in.
    let usb = PrimeUSBDevice()

    private var central: CBCentralManager!
    private var connectedPeripheral: CBPeripheral?
    private var seenPeripheralIDs = Set<UUID>()
    private var characteristicsByUUID: [String: CBCharacteristic] = [:]

    override init() {
        super.init()
        prime.bluetooth = self
        usb.log = { [weak self] in self?.appendLog($0) }
        usb.onFrame = { [weak self] in self?.prime.didReceiveUSB(payload: $0) }
        usb.onAvailabilityChange = { [weak self] available in
            guard let self else { return }
            if available {
                self.prime.usbReady { [weak self] in self?.usb.writeFrame($0) }
            } else {
                self.prime.usbGone()
            }
        }
        if usb.isConnected { usb.onAvailabilityChange?(true) }
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func appendLog(_ line: String) {
        let timestamp = ISO8601DateFormatter().string(from: Date())
        let full = "[\(timestamp)] \(line)"
        DispatchQueue.main.async {
            self.log.append(full)
            print(full)
            fflush(stdout)
        }
    }

    func startScan() {
        guard central.state == .poweredOn else {
            appendLog("Cannot scan: Bluetooth state is \(central.state.rawValue)")
            return
        }
        discovered.removeAll()
        seenPeripheralIDs.removeAll()
        connectionState = .scanning
        appendLog("Scanning for peripherals (all services)…")
        central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    func stopScan() {
        central.stopScan()
        if connectionState == .scanning {
            connectionState = .disconnected
        }
        appendLog("Scan stopped.")
    }

    func connect(_ item: DiscoveredPeripheral) {
        stopScan()
        services = []
        connectionState = .connecting
        connectedPeripheral = item.peripheral
        item.peripheral.delegate = self
        appendLog("Connecting to \(item.name) (\(item.id))…")
        central.connect(item.peripheral, options: nil)
    }

    func disconnect() {
        guard let peripheral = connectedPeripheral else { return }
        central.cancelPeripheralConnection(peripheral)
    }

    /// Raw write to a discovered characteristic (e.g. "FFF1"). Uses
    /// write-with-response when the characteristic supports it so the log
    /// gets a confirmation or an ATT error back.
    func write(_ data: Data, to characteristicUUID: String) {
        guard let peripheral = connectedPeripheral,
              let characteristic = characteristicsByUUID[characteristicUUID] else {
            appendLog("Cannot write: \(characteristicUUID) not available (not connected?)")
            return
        }
        let type: CBCharacteristicWriteType = characteristic.properties.contains(.write) ? .withResponse : .withoutResponse
        let maxLength = peripheral.maximumWriteValueLength(for: type)
        if data.count > maxLength {
            appendLog("Warning: \(data.count) bytes exceeds max write length \(maxLength) for \(characteristicUUID)")
        }
        appendLog("Write \(characteristicUUID) -> \(hex(data))\(type == .withoutResponse ? " (no response)" : "")")
        peripheral.writeValue(data, for: characteristic, type: type)
    }

    private func hex(_ data: Data?) -> String {
        guard let data, !data.isEmpty else { return "<empty>" }
        return data.map { String(format: "%02x", $0) }.joined(separator: " ")
    }
}

extension BluetoothManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        isPoweredOn = central.state == .poweredOn
        appendLog("Central manager state: \(central.state.rawValue)")
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let name = peripheral.name
            ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
            ?? "Unnamed device"
        if !seenPeripheralIDs.contains(peripheral.identifier) {
            seenPeripheralIDs.insert(peripheral.identifier)
            appendLog("Discovered: \(name) [\(peripheral.identifier)] RSSI \(RSSI) adv=\(advertisementData)")
        }
        let item = DiscoveredPeripheral(id: peripheral.identifier, name: name, rssi: RSSI.intValue, peripheral: peripheral)
        DispatchQueue.main.async {
            if let idx = self.discovered.firstIndex(where: { $0.id == item.id }) {
                self.discovered[idx] = item
            } else {
                self.discovered.append(item)
            }
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        connectedName = peripheral.name
        connectionState = .discoveringServices
        appendLog("Connected to \(peripheral.name ?? "device"). Discovering services…")
        peripheral.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        appendLog("Failed to connect: \(error?.localizedDescription ?? "unknown error")")
        connectionState = .disconnected
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        appendLog("Disconnected (\(error?.localizedDescription ?? "no error")).")
        connectionState = .disconnected
        connectedName = nil
        connectedPeripheral = nil
        characteristicsByUUID = [:]
        prime.bluetoothGone()
        services = []
    }
}

extension BluetoothManager: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            appendLog("Service discovery error: \(error.localizedDescription)")
            return
        }
        for service in peripheral.services ?? [] {
            appendLog("Service \(service.uuid.uuidString)\(service.isPrimary ? " (primary)" : "")")
            peripheral.discoverCharacteristics(nil, for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error {
            appendLog("Characteristic discovery error for \(service.uuid): \(error.localizedDescription)")
            return
        }
        var charInfos: [GattCharacteristicInfo] = []
        for characteristic in service.characteristics ?? [] {
            let props = describeProperties(characteristic.properties)
            appendLog("  Characteristic \(characteristic.uuid.uuidString) props=[\(props)]")
            characteristicsByUUID[characteristic.uuid.uuidString] = characteristic
            charInfos.append(GattCharacteristicInfo(id: "\(service.uuid.uuidString)/\(characteristic.uuid.uuidString)", uuid: characteristic.uuid.uuidString, properties: props, lastValueHex: nil))

            peripheral.discoverDescriptors(for: characteristic)
            if characteristic.properties.contains(.read) {
                peripheral.readValue(for: characteristic)
            }
            if characteristic.properties.contains(.notify) || characteristic.properties.contains(.indicate) {
                peripheral.setNotifyValue(true, for: characteristic)
            }
        }
        DispatchQueue.main.async {
            if let idx = self.services.firstIndex(where: { $0.uuid == service.uuid.uuidString }) {
                self.services[idx].characteristics = charInfos
            } else {
                self.services.append(GattServiceInfo(id: service.uuid.uuidString, uuid: service.uuid.uuidString, characteristics: charInfos))
            }
            if self.connectionState != .connected {
                self.connectionState = .connected
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverDescriptorsFor characteristic: CBCharacteristic, error: Error?) {
        for descriptor in characteristic.descriptors ?? [] {
            appendLog("    Descriptor \(descriptor.uuid.uuidString) on \(characteristic.uuid.uuidString)")
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            appendLog("Value update error for \(characteristic.uuid): \(error.localizedDescription)")
            return
        }
        let hexString = hex(characteristic.value)
        appendLog("Value \(characteristic.uuid.uuidString) <- \(hexString)")
        if let value = characteristic.value {
            if let decoded = PrimeFrame.decode(value) {
                appendLog("  ↳ \(PrimeFrame.describe(decoded))")
            }
            prime.didReceive(value, from: characteristic.uuid.uuidString)
        }
        DispatchQueue.main.async {
            for si in self.services.indices {
                if let ci = self.services[si].characteristics.firstIndex(where: { $0.uuid == characteristic.uuid.uuidString }) {
                    self.services[si].characteristics[ci].lastValueHex = hexString
                }
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            appendLog("Notify subscribe error for \(characteristic.uuid): \(error.localizedDescription)")
            return
        }
        appendLog("Notify \(characteristic.isNotifying ? "on" : "off") for \(characteristic.uuid.uuidString)")
        if characteristic.uuid.uuidString == PrimeM2Device.notifyCharacteristic,
           characteristic.isNotifying,
           characteristicsByUUID[PrimeM2Device.writeCharacteristic] != nil {
            prime.bluetoothReady { [weak self] in self?.write($0, to: PrimeM2Device.writeCharacteristic) }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            appendLog("Write error for \(characteristic.uuid): \(error.localizedDescription)")
        } else {
            appendLog("Write confirmed for \(characteristic.uuid.uuidString)")
        }
    }

    private func describeProperties(_ properties: CBCharacteristicProperties) -> String {
        var parts: [String] = []
        if properties.contains(.read) { parts.append("read") }
        if properties.contains(.write) { parts.append("write") }
        if properties.contains(.writeWithoutResponse) { parts.append("writeNoResp") }
        if properties.contains(.notify) { parts.append("notify") }
        if properties.contains(.indicate) { parts.append("indicate") }
        if properties.contains(.broadcast) { parts.append("broadcast") }
        return parts.joined(separator: ",")
    }
}
