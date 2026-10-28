import Foundation

/// Prime M2 protocol session, reconstructed from an Android HCI snoop capture
/// of the official app (see docs/RESEARCH-NOTES.md, "Protocol decoded from Android HCI
/// snoop capture").
///
/// - Every command is an `aa 55` frame (`PrimeFrame`), written to FFF3 over
///   BLE or as a HID report over USB: the M2 answers the same command set on
///   both. USB is preferred whenever the pedal is plugged in.
/// - Over BLE replies arrive as notifications on FFF2, possibly split across
///   several notifications; we reassemble by the frame's length field. Over
///   USB `PrimeUSBDevice` hands us whole frames.
/// - A reply's opcode is the request's opcode + 1. We keep exactly one
///   request in flight, like the official app does.
/// - Unsolicited frames: 0x15 (preset changed on the pedal), 0x34 (tempo),
///   0x43 / 0x97 (unknown).
final class PrimeM2Device: ObservableObject {
    static let writeCharacteristic = "FFF3"
    static let notifyCharacteristic = "FFF2"

    @Published private(set) var isReady = false
    @Published private(set) var firmware: String?
    @Published private(set) var serial: String?
    @Published private(set) var battery: Int?
    @Published private(set) var tempo: Int?
    @Published private(set) var presetNames: [Int: String] = [:]
    @Published private(set) var activePresetIndex: Int?
    /// Preset the user just picked, until the pedal confirms or rejects it,
    /// so the list highlight doesn't jump back while waiting.
    @Published private(set) var requestedPresetIndex: Int?
    var displayedPresetIndex: Int? { requestedPresetIndex ?? activePresetIndex }
    /// Last refused edit worth showing in the editor (cleared on the next edit).
    @Published var notice: String?
    @Published var preset: PrimePreset?
    /// Contents of every preset read so far, filled by a background scan
    /// after sync so the list can flag presets with unnamed models.
    @Published private(set) var presetCache: [Int: PrimePreset] = [:]
    @Published private(set) var isBusy = false

    enum Link: String {
        case usb = "USB"
        case bluetooth = "Bluetooth"
    }
    /// The connection the session currently runs over (nil = none).
    @Published private(set) var link: Link?

    /// Log sink only; frames go through the link writers below.
    weak var bluetooth: BluetoothManager?
    private var bluetoothWriter: ((Data) -> Void)?
    private var usbWriter: ((Data) -> Void)?

    private struct Request {
        var payload: [UInt8]
        /// Requests with the same key replace each other while still queued,
        /// so dragging a slider only ever leaves the latest value pending.
        var coalesceKey: String?
        var handler: ([UInt8]) -> Void
        var reply: UInt8 { payload[0] &+ 1 }
        /// Periodic polls don't count as "busy" (no spinner every second).
        var isBackground: Bool { coalesceKey == "state" || coalesceKey == "battery" }
        /// Read-only requests are re-sent once after a timeout.
        var isRetryableRead: Bool { [0x01, 0x11, 0x21, 0x23, 0x29, 0x33, 0x91, 0xa9].contains(payload[0]) }
        var retried = false
    }

    /// The 99-byte system-state payload from 0x12/0x15, opcode included.
    /// Preset select is "send it back as 0x13 with [1] changed".
    private var systemState: [UInt8]?
    private var rxBuffer: [UInt8] = []
    private var queue: [Request] = []
    private var inFlight: Request?
    private var timeoutWork: DispatchWorkItem?
    /// Over USB the pedal ignores every command for ~180–200 ms after a preset
    /// select (measured: 200 ms answered, 180 ms not), so nothing is sent
    /// before this time. See docs/RESEARCH-NOTES.md.
    private var quietUntil: Date?
    private var quietWork: DispatchWorkItem?
    private var batteryTimer: Timer?
    /// USB only: the pedal doesn't push preset/tempo changes over USB, so we
    /// ask for the system state whenever the queue is idle.
    private var stateTimer: Timer?
    /// Preset indices still to read in the background. Only sent when the
    /// request queue is otherwise idle, so user actions are never delayed
    /// behind the scan.
    private var scanQueue: [Int] = []
    private var scanLogged = false

    var scanRemaining: Int { scanQueue.count }

    // MARK: - Links (main queue)

    /// BLE: FFF2 notifications are on and FFF3 is writable.
    func bluetoothReady(write: @escaping (Data) -> Void) {
        bluetoothWriter = write
        chooseLink()
    }

    func bluetoothGone() {
        bluetoothWriter = nil
        chooseLink()
    }

    /// USB: the HID interface is open.
    func usbReady(write: @escaping (Data) -> Void) {
        usbWriter = write
        chooseLink()
    }

    func usbGone() {
        usbWriter = nil
        chooseLink()
    }

    /// Prefers USB. Switching links restarts the session (full resync), since
    /// replies in flight on the old link are lost.
    private func chooseLink() {
        let preferred: Link? = usbWriter != nil ? .usb : (bluetoothWriter != nil ? .bluetooth : nil)
        guard preferred != link else { return }
        link = preferred
        if let preferred {
            log("Prime M2 session over \(preferred.rawValue)")
            sessionDidStart()
        } else {
            sessionDidEnd()
        }
    }

    private var writer: ((Data) -> Void)? {
        switch link {
        case .usb: return usbWriter
        case .bluetooth: return bluetoothWriter
        case nil: return nil
        }
    }

    // MARK: - Session lifecycle

    /// Runs the same sync sequence the official app runs after connecting.
    private func sessionDidStart() {
        reset()
        log("Prime M2 session starting")

        send([0x01]) { [weak self] payload in
            // 02 41 01 "V1.5.0" 00 …
            self?.firmware = payload.count > 3 ? PrimeM2Names.decode(Array(payload[3...])) : nil
        }
        requestPresetNames(from: 1, to: 40)
        requestPresetNames(from: 41, to: 80)
        send([0xa9]) { [weak self] payload in
            // aa <len u16 LE> "iPedal…"
            self?.serial = payload.count > 3 ? PrimeM2Names.decode(Array(payload[3...])) : nil
        }
        send([0x11]) { [weak self] payload in
            guard let self else { return }
            self.applySystemState(payload)
            self.isReady = true
            self.log("Synced over \(self.link?.rawValue ?? "?"): firmware \(self.firmware ?? "?"), \(self.presetNames.count) preset names, active \(self.activePresetIndex.map(PrimeM2Names.slotLabel) ?? "?"), tempo \(self.tempo ?? 0)")
            if let index = self.activePresetIndex { self.loadPreset(index) }
            self.scanQueue = Array(1...80)
        }
        pollBattery()
        batteryTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.pollBattery()
        }
        if link == .usb {
            stateTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                self?.pollState()
            }
        }
    }

    private func sessionDidEnd() {
        reset()
    }

    private func reset() {
        batteryTimer?.invalidate()
        batteryTimer = nil
        stateTimer?.invalidate()
        stateTimer = nil
        timeoutWork?.cancel()
        timeoutWork = nil
        queue.removeAll()
        inFlight = nil
        quietUntil = nil
        requestedPresetIndex = nil
        quietWork?.cancel()
        quietWork = nil
        rxBuffer.removeAll()
        scanQueue.removeAll()
        scanLogged = false
        presetCache = [:]
        systemState = nil
        isReady = false
        isBusy = false
        firmware = nil
        serial = nil
        battery = nil
        tempo = nil
        presetNames = [:]
        activePresetIndex = nil
        preset = nil
    }

    // MARK: - Commands

    func selectPreset(_ index: Int) {
        guard var state = systemState, state.count > 1, (1...80).contains(index) else {
            log("Preset select \(index) skipped: no system state yet")
            return
        }
        log("Selecting preset \(PrimeM2Names.slotLabel(index))")
        requestedPresetIndex = index
        state[0] = 0x13
        state[1] = UInt8(index)
        send(state) { [weak self] payload in
            guard let self else { return }
            if self.requestedPresetIndex == index { self.requestedPresetIndex = nil }
            guard payload.count > 1, payload[1] == 0x01 else {
                self.log("Preset select \(index) rejected: \(PrimeFrame.hex(payload))")
                return
            }
            self.systemState?[1] = UInt8(index)
            self.activePresetIndex = index
            self.loadPreset(index)
        }
    }

    func loadPreset(_ index: Int) {
        send([0x21, UInt8(index)]) { [weak self] payload in
            guard let self else { return }
            guard let preset = PrimePreset(payload: payload) else {
                self.log("Could not parse preset \(index) (\(payload.count) bytes)")
                return
            }
            self.preset = preset
            self.cache(preset)
            let chain = preset.modules.filter { !$0.isEmpty }
                .map { "\($0.slot):\($0.typeName) \(ModuleCatalog.modelLabel(type: $0.type, model: $0.model ?? 0))" }
            self.log("Loaded preset \(PrimeM2Names.slotLabel(index)) “\(preset.name)”: " + (chain.isEmpty ? "empty" : chain.joined(separator: ", ")))
        }
        send([0x33, UInt8(index)]) { [weak self] payload in
            self?.applyTempo(payload)
        }
    }

    /// Live edit of one slot (`25 slot type n data`). Affects the pedal's
    /// edit buffer immediately; persists only after `savePreset`.
    func updateModule(_ module: PrimeModule) {
        if let index = preset?.modules.firstIndex(where: { $0.slot == module.slot }) {
            preset?.modules[index] = module
            if let preset { presetCache[preset.index] = preset }
        }
        send([0x25] + module.encoded, coalesceKey: "module-\(module.slot)") { [weak self] payload in
            if payload.count < 2 || payload[1] != 0x01 {
                self?.log("Module \(module.slot) update rejected: \(PrimeFrame.hex(payload))")
            }
        }
    }

    // MARK: - Adding, changing and removing modules

    /// Echoed back by `2e`; just a running number.
    private var moduleEditID: UInt8 = 0

    /// `2d id op slot type model` → `2e id ok`. op 1 puts an effect into an
    /// empty slot, op 2 changes the effect in a used one; either way the
    /// pedal loads that model's defaults (hidden words included). When its
    /// DSP is full it still answers ok but changes nothing, so the slot is
    /// read back and compared. See docs/RESEARCH-NOTES.md.
    func placeModule(slot: UInt8, type: UInt8, model: UInt16) {
        let occupied = preset?.modules.first { $0.slot == slot }.map { !$0.isEmpty } ?? false
        let wanted = "\(ModuleCatalog.typeName(type)) \(ModuleCatalog.modelLabel(type: type, model: model))"
        notice = nil
        moduleEditID &+= 1
        log("\(occupied ? "Changing" : "Adding") slot \(slot) → \(wanted)")
        send([0x2d, moduleEditID, occupied ? 2 : 1, slot, type, UInt8(truncatingIfNeeded: model)]) { [weak self] payload in
            guard let self else { return }
            guard payload.count > 2, payload[2] == 0x01 else {
                self.notice = "The pedal rejected \(wanted) in slot \(slot)."
                self.log(self.notice ?? "")
                return
            }
            self.readSlot(slot) { module in
                self.log("Slot \(slot) now: " + (module.map { "\($0.typeName) \(ModuleCatalog.modelLabel(type: $0.type, model: $0.model ?? 0)) | \($0.words.map(String.init).joined(separator: " "))" } ?? "unreadable"))
                if module?.type != type || module?.model != model {
                    self.notice = "The pedal didn't take \(wanted) in slot \(slot), probably because its DSP is full. "
                        + "Remove or change another effect first."
                    self.log(self.notice ?? "")
                }
            }
        }
    }

    /// `2d id 3 slot` deletes the slot; later slots move up, so the whole
    /// edit buffer is re-read.
    func deleteModule(slot: UInt8) {
        guard let index = preset?.index else { return }
        notice = nil
        moduleEditID &+= 1
        log("Removing slot \(slot)")
        send([0x2d, moduleEditID, 3, slot, 0, 0]) { [weak self] payload in
            guard let self else { return }
            guard payload.count > 2, payload[2] == 0x01 else {
                self.notice = "The pedal refused to remove slot \(slot)."
                self.log(self.notice ?? "")
                return
            }
            self.loadPreset(index)
        }
    }

    /// `23 slot` → `24 slot type n data` from the edit buffer.
    private func readSlot(_ slot: UInt8, then done: @escaping (PrimeModule?) -> Void) {
        send([0x23, slot]) { [weak self] payload in
            guard let self else { return }
            var offset = 1
            let module = PrimeModule.parse(payload, offset: &offset)
            if let module, let i = self.preset?.modules.firstIndex(where: { $0.slot == slot }) {
                self.preset?.modules[i] = module
                if let preset = self.preset { self.presetCache[preset.index] = preset }
            }
            done(module)
        }
    }

    /// Saves the pedal's current edit buffer into preset `index` under `name`
    /// (`2f pp name[20]`), then selects it, as the official app does.
    func savePreset(to index: Int, name: String) {
        send([0x2f, UInt8(index)] + PrimeM2Names.encode(name)) { [weak self] payload in
            guard let self else { return }
            guard payload.count > 1, payload[1] == 0x01 else {
                self.log("Save to \(index) rejected: \(PrimeFrame.hex(payload))")
                return
            }
            self.presetNames[index] = name
            self.selectPreset(index)
        }
    }

    private func requestPresetNames(from start: Int, to end: Int) {
        send([0x29, UInt8(start), UInt8(end)]) { [weak self] payload in
            // 2a start end name[20] × (end - start + 1)
            guard let self, payload.count >= 3 else { return }
            let first = Int(payload[1])
            var offset = 3
            var index = first
            while offset + PrimePreset.nameLength <= payload.count {
                self.presetNames[index] = PrimeM2Names.decode(Array(payload[offset..<(offset + PrimePreset.nameLength)]))
                offset += PrimePreset.nameLength
                index += 1
            }
        }
    }

    private func cache(_ preset: PrimePreset) {
        presetCache[preset.index] = preset
        presetNames[preset.index] = preset.name
        scanQueue.removeAll { $0 == preset.index }
        if !scanLogged, presetCache.count == 80, scanQueue.isEmpty {
            scanLogged = true
            log("All 80 presets read")
        }
    }

    /// Reads one not-yet-cached preset; called from `pump` when idle.
    /// Uses `21 pp` like `loadPreset`, but leaves the active preset alone.
    private func scanNext() -> Bool {
        while let index = scanQueue.first {
            scanQueue.removeFirst()
            if presetCache[index] != nil { continue }
            send([0x21, UInt8(index)]) { [weak self] payload in
                guard let self, let preset = PrimePreset(payload: payload) else { return }
                self.cache(preset)
            }
            return true
        }
        return false
    }

    /// Picks up preset and tempo changes made on the pedal itself (what the
    /// unsolicited 0x15 / 0x34 frames do over BLE). Skipped while anything
    /// else is queued, so edits and the preset scan go first.
    private func pollState() {
        guard isReady, inFlight == nil, queue.isEmpty else { return }
        send([0x11], coalesceKey: "state") { [weak self] payload in
            guard let self, payload.count > 42 else { return }
            let previous = self.activePresetIndex
            self.applySystemState(payload)
            if let index = self.activePresetIndex, index != previous {
                self.log("Pedal switched to preset \(PrimeM2Names.slotLabel(index))")
                self.loadPreset(index)
            }
        }
    }

    private func pollBattery() {
        send([0x91], coalesceKey: "battery") { [weak self] payload in
            if payload.count > 1 { self?.battery = Int(payload[1]) }
        }
    }

    // MARK: - Request queue

    private func send(_ payload: [UInt8], coalesceKey: String? = nil, handler: @escaping ([UInt8]) -> Void) {
        let request = Request(payload: payload, coalesceKey: coalesceKey, handler: handler)
        if let key = coalesceKey, let index = queue.firstIndex(where: { $0.coalesceKey == key }) {
            queue[index] = request
        } else {
            queue.append(request)
        }
        pump()
    }

    private func pump() {
        if inFlight == nil, queue.isEmpty, isReady, scanNext() { return }
        isBusy = (inFlight.map { !$0.isBackground } ?? false) || queue.contains { !$0.isBackground }
        guard inFlight == nil, !queue.isEmpty, let writer else { return }
        if let quietUntil, quietUntil > Date() {
            guard quietWork == nil else { return }
            let work = DispatchWorkItem { [weak self] in
                self?.quietWork = nil
                self?.pump()
            }
            quietWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + quietUntil.timeIntervalSinceNow, execute: work)
            return
        }
        let request = queue.removeFirst()
        inFlight = request
        writer(PrimeFrame.encode(payload: request.payload))

        let work = DispatchWorkItem { [weak self] in
            guard let self, var stuck = self.inFlight else { return }
            self.inFlight = nil
            if stuck.isRetryableRead, !stuck.retried {
                self.log(String(format: "No reply 0x%02x, retrying", stuck.reply))
                stuck.retried = true
                self.queue.insert(stuck, at: 0)
            } else {
                self.log(String(format: "Timeout waiting for reply 0x%02x", stuck.reply))
                if stuck.payload[0] == 0x13 { self.requestedPresetIndex = nil }
            }
            self.pump()
        }
        timeoutWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: work)
    }

    // MARK: - Receive path

    /// A whole frame's payload from USB.
    func didReceiveUSB(payload: [UInt8]) {
        guard link == .usb, !payload.isEmpty else { return }
        handle(payload)
    }

    /// Raw FFF2 notification bytes from BLE.
    func didReceive(_ data: Data, from characteristicUUID: String) {
        guard characteristicUUID == Self.notifyCharacteristic, link == .bluetooth else { return }
        rxBuffer.append(contentsOf: data)

        while true {
            // Resync on garbage: drop bytes until an aa 55 header.
            while rxBuffer.count >= 2, !(rxBuffer[0] == 0xaa && rxBuffer[1] == 0x55) {
                rxBuffer.removeFirst()
            }
            guard rxBuffer.count >= 4 else { return }
            let length = Int(rxBuffer[2]) | Int(rxBuffer[3]) << 8
            guard rxBuffer.count >= length + 6 else { return }
            let frame = Data(rxBuffer[0..<(length + 6)])
            rxBuffer.removeFirst(length + 6)

            guard let decoded = PrimeFrame.decode(frame), decoded.crcOK, !decoded.payload.isEmpty else {
                log("Dropped frame with bad CRC: \(PrimeFrame.hex(frame))")
                continue
            }
            handle(decoded.payload)
        }
    }

    private func handle(_ payload: [UInt8]) {
        let opcode = payload[0]
        if let request = inFlight, request.reply == opcode {
            timeoutWork?.cancel()
            inFlight = nil
            // A preset select (14) or save (30, followed by a select) leaves the
            // pedal deaf for a moment over USB; adding/removing a module (2e)
            // rebuilds its DSP chain, so give it the same pause.
            if link == .usb, opcode == 0x14 || opcode == 0x30 || opcode == 0x2e {
                quietUntil = Date().addingTimeInterval(0.3)
            }
            request.handler(payload)
            pump()
            return
        }
        switch opcode {
        case 0x15:
            // Preset changed on the pedal itself.
            applySystemState(payload)
            if let index = activePresetIndex { loadPreset(index) }
        case 0x34:
            applyTempo(payload)
        case 0x9e:
            break   // USB heartbeat, every ~6 s
        
        default:
            log(String(format: "Unsolicited 0x%02x: ", opcode) + PrimeFrame.hex(payload.dropFirst()))
        }
    }

    private func applySystemState(_ payload: [UInt8]) {
        guard payload.count > 42 else { return }
        systemState = payload
        activePresetIndex = Int(payload[1])
        tempo = Int(payload[41]) | Int(payload[42]) << 8
    }

    private func applyTempo(_ payload: [UInt8]) {
        guard payload.count > 2 else { return }
        tempo = Int(payload[1]) | Int(payload[2]) << 8
    }

    private func log(_ line: String) {
        bluetooth?.appendLog(line)
    }
}
