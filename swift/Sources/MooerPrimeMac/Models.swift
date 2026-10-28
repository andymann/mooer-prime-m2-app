import Foundation

/// One of the 20 effect slots in a Prime M2 preset, exactly as it travels on
/// the wire (see docs/RESEARCH-NOTES.md, "Preset struct"):
///
///     slot# | type | n | data[n]      data = n/2 × uint16 LE
///
/// data[0] is enabled (0/1), data[1] the model within the type, the rest are
/// parameters (knobs are 0…100; some words carry Hz/ms values above that).
/// An empty slot is `slot# 00 00`.
struct PrimeModule: Identifiable, Hashable {
    var slot: UInt8
    var type: UInt8
    var words: [UInt16]

    var id: UInt8 { slot }
    var isEmpty: Bool { type == 0 && words.isEmpty }

    var isEnabled: Bool {
        get { words.first.map { $0 != 0 } ?? false }
        set { if !words.isEmpty { words[0] = newValue ? 1 : 0 } }
    }

    var model: UInt16? { words.count > 1 ? words[1] : nil }

    /// Indices into `words` that are parameters (everything after enabled/model).
    var parameterIndices: Range<Int> { min(2, words.count)..<words.count }

    var typeName: String { ModuleCatalog.typeName(type) }

    var encoded: [UInt8] {
        var bytes: [UInt8] = [slot, type, UInt8(words.count * 2)]
        for word in words {
            bytes.append(UInt8(word & 0xff))
            bytes.append(UInt8(word >> 8))
        }
        return bytes
    }

    /// Parses one slot record at `offset`, advancing it. Nil if truncated.
    static func parse(_ bytes: [UInt8], offset: inout Int) -> PrimeModule? {
        guard offset + 3 <= bytes.count else { return nil }
        let slot = bytes[offset], type = bytes[offset + 1], length = Int(bytes[offset + 2])
        guard offset + 3 + length <= bytes.count else { return nil }
        var words: [UInt16] = []
        var index = offset + 3
        while index + 1 < offset + 3 + length {
            words.append(UInt16(bytes[index]) | UInt16(bytes[index + 1]) << 8)
            index += 2
        }
        offset += 3 + length
        return PrimeModule(slot: slot, type: type, words: words)
    }
}

/// Full preset as returned by `21 pp` → `22`.
struct PrimePreset: Hashable {
    static let nameLength = 20
    static let slotCount = 20

    var index: Int
    var name: String
    var modules: [PrimeModule]
    var trailer: [UInt8]

    /// `payload` is the whole 0x22 frame payload, opcode included.
    init?(payload: [UInt8]) {
        guard payload.count >= 2 + Self.nameLength, payload[0] == 0x22 else { return nil }
        index = Int(payload[1])
        name = PrimeM2Names.decode(Array(payload[2..<(2 + Self.nameLength)]))
        var offset = 2 + Self.nameLength
        var modules: [PrimeModule] = []
        for _ in 0..<Self.slotCount {
            guard let module = PrimeModule.parse(payload, offset: &offset) else { return nil }
            modules.append(module)
        }
        self.modules = modules
        trailer = Array(payload[offset...])
    }
}

enum PrimeM2Names {
    static func decode(_ bytes: [UInt8]) -> String {
        let trimmed = bytes.prefix { $0 != 0 }
        return String(decoding: trimmed, as: UTF8.self)
    }

    /// NUL-padded/truncated to `length` bytes, ASCII only (the device field
    /// is a fixed byte array; non-ASCII would be split mid-character).
    static func encode(_ name: String, length: Int = PrimePreset.nameLength) -> [UInt8] {
        let ascii = name.unicodeScalars.filter { $0.isASCII && $0.value >= 0x20 }.map { UInt8($0.value) }
        return Array(ascii.prefix(length)) + Array(repeating: 0, count: max(0, length - ascii.count))
    }

    /// 1…80 → "0A"…"19D" (4 slots per bank, banks from 0).
    static func slotLabel(_ index: Int) -> String {
        guard index >= 1 else { return "?" }
        let zeroBased = index - 1
        return "\(zeroBased / 4)\(["A", "B", "C", "D"][zeroBased % 4])"
    }
}
