import Foundation
import IOKit.hid

/// Prime M2 over USB HID (VID 0x34DB, PID 13), see docs/RESEARCH-NOTES.md,
/// "Mooer Studio for M2 — USB protocol" and "USB on the real M2".
///
/// - One HID interface with 1024-byte reports and no report ID. Output
///   reports are `[frameLength, frame…]`; input reports start with the
///   `aa 55` frame and are zero-padded. Frames are the same `PrimeFrame`s as
///   on BLE, and over USB the M2 answers the BLE command set too.
/// - What USB adds is Mooer Studio's whole-preset transfer ("cloud backup /
///   recovery"), which BLE lacks: read with `171` → `172`, write with
///   `173` → `174`, in frames. Removing an effect is: read the stored preset,
///   drop its 36-byte module record, fix the CRC, write it back.
final class PrimeUSBDevice: ObservableObject {
    static let vendorID = 0x34DB   // 13531
    static let productID = 13      // M2 (P2 = 2)

    enum Command {
        static let backupRead: UInt8 = 171     // idx n(u16) → 172
        static let backupReadReply: UInt8 = 172
        static let recoveryWrite: UInt8 = 173  // idx n(u16) len(u16) data → 174
        static let recoveryWriteReply: UInt8 = 174
    }

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    struct Backup {
        let presetIndex: Int   // 1-based, like PrimeM2Device
        let url: URL
    }

    @Published private(set) var isConnected = false
    @Published private(set) var isBusy = false
    @Published var notice: String?
    /// Preset saved before the last removal, for undo.
    @Published private(set) var lastBackup: Backup?

    var log: (String) -> Void = { print($0) }
    /// Every frame that isn't a reply to our own transfer commands (all the
    /// BLE-style replies and the pedal's unsolicited pushes), payload only.
    var onFrame: (([UInt8]) -> Void)?
    var onAvailabilityChange: ((Bool) -> Void)?

    private let manager: IOHIDManager
    private var device: IOHIDDevice?
    private var reportBuffer: UnsafeMutablePointer<UInt8>?
    private var rxBuffer: [UInt8] = []

    private struct Request {
        var payload: [UInt8]
        var reply: UInt8
        var handler: (Result<[UInt8], Error>) -> Void
    }
    private var queue: [Request] = []
    private var inFlight: Request?
    private var timeoutWork: DispatchWorkItem?

    init() {
        manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        let matching: [String: Any] = [
            kIOHIDVendorIDKey: Self.vendorID,
            kIOHIDProductIDKey: Self.productID,
        ]
        IOHIDManagerSetDeviceMatching(manager, matching as CFDictionary)
        let context = Unmanaged.passUnretained(self).toOpaque()
        IOHIDManagerRegisterDeviceMatchingCallback(manager, { context, _, _, device in
            guard let context else { return }
            Unmanaged<PrimeUSBDevice>.fromOpaque(context).takeUnretainedValue().attach(device)
        }, context)
        IOHIDManagerRegisterDeviceRemovalCallback(manager, { context, _, _, device in
            guard let context else { return }
            Unmanaged<PrimeUSBDevice>.fromOpaque(context).takeUnretainedValue().detach(device)
        }, context)
        IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue)
        let result = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        if result != kIOReturnSuccess {
            log(String(format: "USB: IOHIDManagerOpen failed 0x%08x", result))
        }
    }

    // MARK: - Device lifecycle (main run loop)

    private func attach(_ candidate: IOHIDDevice) {
        let inputSize = IOHIDDeviceGetProperty(candidate, kIOHIDMaxInputReportSizeKey as CFString) as? Int ?? 1024
        log("USB: Prime M2 found (input reports \(inputSize) bytes)")
        releaseReportBuffer()
        device = candidate
        let size = max(inputSize, 64)
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: size)
        reportBuffer = buffer
        let context = Unmanaged.passUnretained(self).toOpaque()
        IOHIDDeviceRegisterInputReportCallback(candidate, buffer, size, { context, _, _, _, _, report, length in
            guard let context else { return }
            let bytes = Array(UnsafeBufferPointer(start: report, count: length))
            let me = Unmanaged<PrimeUSBDevice>.fromOpaque(context).takeUnretainedValue()
            // Handle outside the callback: replies trigger the next write, and a
            // synchronous IOHIDDeviceSetReport from inside it stalls further input.
            DispatchQueue.main.async { me.didReceive(bytes) }
        }, context)
        rxBuffer.removeAll()
        let wasConnected = isConnected
        isConnected = true
        if !wasConnected { onAvailabilityChange?(true) }
    }

    private func detach(_ removed: IOHIDDevice) {
        guard removed == device else { return }
        log("USB: Prime M2 disconnected")
        device = nil
        releaseReportBuffer()
        isConnected = false
        failAll(Failure(message: "USB device disconnected"))
        onAvailabilityChange?(false)
    }

    private func releaseReportBuffer() {
        reportBuffer?.deallocate()
        reportBuffer = nil
    }

    // MARK: - Whole-preset transfer

    /// Reads stored preset `index` (1-based).
    func readPreset(_ index: Int, completion: @escaping (Result<CloudPreset, Error>) -> Void) {
        send([Command.backupRead, UInt8(index), 0, 0], reply: Command.backupReadReply) { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let payload):
                // 172 | frameCount u16
                guard payload.count >= 3 else {
                    completion(.failure(Failure(message: "Preset \(PrimeM2Names.slotLabel(index)) is empty or unreadable")))
                    return
                }
                let count = Int(payload[1]) | Int(payload[2]) << 8
                self.readFrames(index, frame: 1, of: count, collected: [], completion: completion)
            }
        }
    }

    private func readFrames(_ index: Int, frame: Int, of count: Int, collected: [[UInt8]],
                            completion: @escaping (Result<CloudPreset, Error>) -> Void) {
        guard frame <= count else {
            completion(CloudPreset(frames: collected).map(Result.success)
                       ?? .failure(Failure(message: "Unexpected preset format (\(collected.map(\.count)) bytes)")))
            return
        }
        send([Command.backupRead, UInt8(index), UInt8(frame & 0xff), UInt8(frame >> 8)],
             reply: Command.backupReadReply) { [weak self] result in
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let payload):
                // 172 | frame u16 | len u16 | data
                guard payload.count >= 5 else {
                    completion(.failure(Failure(message: "Short reply for preset frame \(frame)")))
                    return
                }
                let length = Int(payload[3]) | Int(payload[4]) << 8
                guard payload.count >= 5 + length else {
                    completion(.failure(Failure(message: "Preset frame \(frame) truncated")))
                    return
                }
                self?.readFrames(index, frame: frame + 1, of: count,
                                 collected: collected + [Array(payload[5..<(5 + length)])], completion: completion)
            }
        }
    }

    /// Overwrites stored preset `index` (1-based; 0 would be the audition slot).
    func writePreset(_ index: Int, _ preset: CloudPreset, completion: @escaping (Result<Void, Error>) -> Void) {
        writeFrames(index, preset.frames, frame: 1, completion: completion)
    }

    private func writeFrames(_ index: Int, _ frames: [[UInt8]], frame: Int,
                             completion: @escaping (Result<Void, Error>) -> Void) {
        guard frame <= frames.count else {
            completion(.success(()))
            return
        }
        let data = frames[frame - 1]
        let header: [UInt8] = [Command.recoveryWrite, UInt8(index), UInt8(frame & 0xff), UInt8(frame >> 8),
                               UInt8(data.count & 0xff), UInt8(data.count >> 8)]
        send(header + data, reply: Command.recoveryWriteReply) { [weak self] result in
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let payload):
                // 174 | frame u16 | status (1 = ok)
                guard payload.count >= 4, payload[3] == 1 else {
                    completion(.failure(Failure(message: "Pedal rejected preset frame \(frame) (\(PrimeFrame.hex(payload)))")))
                    return
                }
                self?.writeFrames(index, frames, frame: frame + 1, completion: completion)
            }
        }
    }

    /// Removes the effect at `position` (0-based, chain order) from stored
    /// preset `index`, after checking the stored copy matches the chain the
    /// BLE side shows (name, module types, on/off). The original is saved
    /// first and kept as `lastBackup`.
    func removeEffect(presetIndex index: Int, position: Int, expectedName: String,
                      expectedChain: [(type: UInt8, enabled: Bool)],
                      completion: @escaping (Result<Void, Error>) -> Void) {
        readPreset(index) { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let original):
                if let problem = original.mismatch(name: expectedName, chain: expectedChain) {
                    completion(.failure(Failure(message: problem)))
                    return
                }
                let updated: CloudPreset
                let backupURL: URL
                do {
                    updated = try original.removingModule(at: position)
                    backupURL = try original.saveBackup(presetIndex: index)
                } catch {
                    completion(.failure(error))
                    return
                }
                self.log("USB: backup of preset \(PrimeM2Names.slotLabel(index)) saved to \(backupURL.path)")
                self.lastBackup = Backup(presetIndex: index, url: backupURL)
                self.writeAndVerify(index, updated, completion: completion)
            }
        }
    }

    /// Writes `lastBackup` back to its preset slot.
    func restoreLastBackup(completion: @escaping (Result<Int, Error>) -> Void) {
        guard let backup = lastBackup else { return }
        guard let data = try? Data(contentsOf: backup.url), let preset = CloudPreset(fileData: [UInt8](data)) else {
            completion(.failure(Failure(message: "Backup file is unreadable: \(backup.url.path)")))
            return
        }
        writeAndVerify(backup.presetIndex, preset) { [weak self] result in
            if case .success = result { self?.lastBackup = nil }
            completion(result.map { backup.presetIndex })
        }
    }

    private func writeAndVerify(_ index: Int, _ preset: CloudPreset, completion: @escaping (Result<Void, Error>) -> Void) {
        writePreset(index, preset) { [weak self] result in
            guard let self else { return }
            if case .failure(let error) = result {
                completion(.failure(error))
                return
            }
            self.readPreset(index) { readBack in
                completion(readBack.flatMap { stored in
                    stored.bytes == preset.bytes
                        ? .success(())
                        : .failure(Failure(message: "Preset \(PrimeM2Names.slotLabel(index)) read back differently after writing"))
                })
            }
        }
    }

    // MARK: - Raw frames (for the PrimeM2Device session)

    /// Writes one already-framed command; the reply comes back via `onFrame`.
    func writeFrame(_ frame: Data) {
        guard let device else { return }
        if let error = writeReport(device, [UInt8](frame)) { log(error) }
    }

    /// Studio (node-hid) writes [0 report ID, len, frame…]; IOKit takes the
    /// report ID separately. Every frame we send fits one 1024-byte report.
    private func writeReport(_ device: IOHIDDevice, _ frame: [UInt8]) -> String? {
        let report = [UInt8(truncatingIfNeeded: frame.count)] + frame
        let result = report.withUnsafeBufferPointer {
            IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, 0, $0.baseAddress!, $0.count)
        }
        return result == kIOReturnSuccess ? nil : String(format: "USB: write failed 0x%08x", result)
    }

    // MARK: - Request queue

    private func send(_ payload: [UInt8], reply: UInt8, handler: @escaping (Result<[UInt8], Error>) -> Void) {
        queue.append(Request(payload: payload, reply: reply, handler: handler))
        pump()
    }

    private func pump() {
        isBusy = inFlight != nil || !queue.isEmpty
        guard inFlight == nil, !queue.isEmpty else { return }
        let request = queue.removeFirst()
        guard let device else {
            request.handler(.failure(Failure(message: "Pedal not connected via USB")))
            pump()
            return
        }
        inFlight = request
        if let error = writeReport(device, [UInt8](PrimeFrame.encode(payload: request.payload))) {
            log(error)
            finish(.failure(Failure(message: error)))
            return
        }

        let work = DispatchWorkItem { [weak self] in
            guard let self, let stuck = self.inFlight else { return }
            self.log("USB: timeout waiting for reply \(stuck.reply)")
            self.finish(.failure(Failure(message: "The pedal didn't answer over USB (waiting for reply \(stuck.reply))")))
        }
        timeoutWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
    }

    private func finish(_ result: Result<[UInt8], Error>) {
        timeoutWork?.cancel()
        guard let request = inFlight else { return }
        inFlight = nil
        request.handler(result)
        pump()
    }

    private func failAll(_ error: Error) {
        let pending = (inFlight.map { [$0] } ?? []) + queue
        inFlight = nil
        queue.removeAll()
        timeoutWork?.cancel()
        pending.forEach { $0.handler(.failure(error)) }
        isBusy = false
    }

    // MARK: - Receive path

    private func didReceive(_ report: [UInt8]) {
        rxBuffer.append(contentsOf: report)
        while true {
            while rxBuffer.count >= 2, !(rxBuffer[0] == 0xaa && rxBuffer[1] == 0x55) {
                rxBuffer.removeFirst()
            }
            guard rxBuffer.count >= 4 else { return }
            let length = Int(rxBuffer[2]) | Int(rxBuffer[3]) << 8
            guard rxBuffer.count >= length + 6 else { return }
            let frame = Data(rxBuffer[0..<(length + 6)])
            rxBuffer.removeFirst(length + 6)
            guard let decoded = PrimeFrame.decode(frame), decoded.crcOK, let cmd = decoded.payload.first else {
                log("USB: dropped frame with bad CRC (\(frame.count) bytes)")
                continue
            }
            if let request = inFlight, request.reply == cmd {
                finish(.success(decoded.payload))
            } else {
                onFrame?(decoded.payload)
            }
        }
    }
}

/// A stored preset in Mooer Studio's "cloud" format: 3 frames
/// (322 + 512 + 364 bytes on the M2), decoded in docs/RESEARCH-NOTES.md.
struct CloudPreset {
    static let bpmOffset = 390
    static let nameOffset = 392
    static let nameLength = 20
    static let modulesOffset = 412
    static let moduleSize = 36        // type u16 + 17 × u16 words
    static let moduleCount = 20
    static let crcStart = 322
    static let crcOffset = 1196       // CRC-16/GSM over 322..<1196, little-endian
    static let size = 1198

    let frameLengths: [Int]
    private(set) var bytes: [UInt8]

    init?(frames: [[UInt8]]) {
        let bytes = frames.flatMap { $0 }
        guard bytes.count == Self.size else { return nil }
        frameLengths = frames.map(\.count)
        self.bytes = bytes
    }

    /// Studio's `.preset` file: u16 frameCount, u16 lengths…, frame data.
    init?(fileData data: [UInt8]) {
        guard data.count >= 2 else { return nil }
        let count = Int(data[0]) | Int(data[1]) << 8
        guard data.count >= 2 + 2 * count else { return nil }
        let lengths = (0..<count).map { Int(data[2 + 2 * $0]) | Int(data[3 + 2 * $0]) << 8 }
        var offset = 2 + 2 * count
        var frames: [[UInt8]] = []
        for length in lengths {
            guard offset + length <= data.count else { return nil }
            frames.append(Array(data[offset..<(offset + length)]))
            offset += length
        }
        self.init(frames: frames)
    }

    var fileData: [UInt8] {
        [UInt8(frameLengths.count & 0xff), UInt8(frameLengths.count >> 8)]
            + frameLengths.flatMap { [UInt8($0 & 0xff), UInt8($0 >> 8)] } + bytes
    }

    var frames: [[UInt8]] {
        var offset = 0
        return frameLengths.map { length in
            defer { offset += length }
            return Array(bytes[offset..<(offset + length)])
        }
    }

    var name: String { PrimeM2Names.decode(Array(bytes[Self.nameOffset..<(Self.nameOffset + Self.nameLength)])) }

    var crcIsValid: Bool { storedCRC == computedCRC }
    private var storedCRC: UInt16 { UInt16(bytes[Self.crcOffset]) | UInt16(bytes[Self.crcOffset + 1]) << 8 }
    private var computedCRC: UInt16 { PrimeFrame.crc16(bytes[Self.crcStart..<Self.crcOffset]) }

    private func word(_ offset: Int) -> UInt16 { UInt16(bytes[offset]) | UInt16(bytes[offset + 1]) << 8 }

    /// Type and on/off of each used slot, in chain order.
    var chain: [(type: UInt8, enabled: Bool)] {
        (0..<Self.moduleCount).compactMap { k in
            let offset = Self.modulesOffset + k * Self.moduleSize
            let type = word(offset)
            return type == 0 ? nil : (UInt8(truncatingIfNeeded: type), word(offset + 2) != 0)
        }
    }

    /// Nil when this stored preset looks like the chain the app shows.
    func mismatch(name expectedName: String, chain expected: [(type: UInt8, enabled: Bool)]) -> String? {
        guard crcIsValid else { return "The stored preset's checksum doesn't match. Nothing was changed." }
        if name != expectedName {
            return "The stored preset is named “\(name)”, but the app shows “\(expectedName)”. Nothing was changed."
        }
        let stored = chain
        func describe(_ list: [(type: UInt8, enabled: Bool)]) -> String {
            list.map { "\(ModuleCatalog.typeName($0.type)) \($0.enabled ? "on" : "off")" }.joined(separator: ", ")
        }
        if stored.map(\.type) != expected.map(\.type) || stored.map(\.enabled) != expected.map(\.enabled) {
            return "The stored preset (\(describe(stored))) differs from what the app shows (\(describe(expected))). "
                + "Save or re-select the preset, then try again. Nothing was changed."
        }
        return nil
    }

    /// Drops one 36-byte module record, shifts the rest up, zeroes the freed
    /// last slot and recomputes the CRC. Everything else is kept byte for byte.
    func removingModule(at position: Int) throws -> CloudPreset {
        let used = chain.count
        guard (0..<used).contains(position) else {
            throw PrimeUSBDevice.Failure(message: "No effect at position \(position + 1) (preset has \(used))")
        }
        var records = (0..<Self.moduleCount).map { k -> [UInt8] in
            let start = Self.modulesOffset + k * Self.moduleSize
            return Array(bytes[start..<(start + Self.moduleSize)])
        }
        records.remove(at: position)
        records.append([UInt8](repeating: 0, count: Self.moduleSize))
        var result = self
        result.bytes.replaceSubrange(Self.modulesOffset..<(Self.modulesOffset + Self.moduleCount * Self.moduleSize),
                                     with: records.flatMap { $0 })
        let crc = result.computedCRC
        result.bytes[Self.crcOffset] = UInt8(crc & 0xff)
        result.bytes[Self.crcOffset + 1] = UInt8(crc >> 8)
        return result
    }

    func saveBackup(presetIndex: Int) throws -> URL {
        let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MooerPrimeMac/Backups", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let url = directory.appendingPathComponent(
            "\(PrimeM2Names.slotLabel(presetIndex))-\(formatter.string(from: Date())).preset")
        try Data(fileData).write(to: url)
        return url
    }
}
