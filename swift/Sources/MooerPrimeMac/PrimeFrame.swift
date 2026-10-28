import Foundation

/// Wire framing used by the Prime M2 on its FFF0 service, as decoded from
/// unsolicited preset-change notifications on FFF2 (see docs/RESEARCH-NOTES.md):
///
///     aa 55 | len (uint16 LE) | payload (len bytes) | CRC-16 (BE)
///
/// CRC is poly 0x1021, init 0x0000, unreflected, xorout 0xFFFF
/// (CRC-16/GSM), computed over the length field + payload. Confirmed on
/// device→host frames only; host→device framing is assumed to match.
enum PrimeFrame {
    static let sync: [UInt8] = [0xaa, 0x55]

    static func crc16(_ bytes: some Sequence<UInt8>) -> UInt16 {
        var crc: UInt16 = 0x0000
        for byte in bytes {
            crc ^= UInt16(byte) << 8
            for _ in 0..<8 {
                crc = (crc & 0x8000) != 0 ? (crc << 1) ^ 0x1021 : crc << 1
            }
        }
        return crc ^ 0xFFFF
    }

    static func encode(payload: [UInt8]) -> Data {
        let length = UInt16(payload.count)
        let body: [UInt8] = [UInt8(length & 0xff), UInt8(length >> 8)] + payload
        let crc = crc16(body)
        return Data(sync + body + [UInt8(crc >> 8), UInt8(crc & 0xff)])
    }

    struct Decoded {
        let payload: [UInt8]
        let crcOK: Bool
    }

    /// Returns nil if `data` isn't a single complete aa 55 frame.
    static func decode(_ data: Data) -> Decoded? {
        let bytes = [UInt8](data)
        guard bytes.count >= 6, bytes[0] == 0xaa, bytes[1] == 0x55 else { return nil }
        let length = Int(bytes[2]) | Int(bytes[3]) << 8
        guard bytes.count == length + 6 else { return nil }
        let expected = UInt16(bytes[bytes.count - 2]) << 8 | UInt16(bytes[bytes.count - 1])
        return Decoded(payload: Array(bytes[4..<(4 + length)]),
                       crcOK: crc16(bytes[2..<(bytes.count - 2)]) == expected)
    }

    /// One-line human summary for the log, using what's been decoded so far.
    static func describe(_ decoded: Decoded) -> String {
        var parts = ["frame len=\(decoded.payload.count)", decoded.crcOK ? "crc ok" : "CRC BAD"]
        if let type = decoded.payload.first {
            parts.append(String(format: "type=0x%02x", type))
            if type == 0x15, decoded.payload.count > 41 {
                let slot = Int(decoded.payload[1]) - 1
                parts.append("preset \(slot / 4)\(["A", "B", "C", "D"][slot % 4])")
                parts.append("[45]=\(decoded.payload[41])")
            }
        }
        return parts.joined(separator: " ")
    }

    /// Parses "aa 55 0a", "aa550a", "0xaa,0x55" etc. Nil on any invalid token.
    static func parseHex(_ text: String) -> [UInt8]? {
        let cleaned = text.lowercased()
            .replacingOccurrences(of: "0x", with: "")
            .filter { !" ,:-\n\t".contains($0) }
        guard cleaned.count % 2 == 0 else { return nil }
        var bytes: [UInt8] = []
        var index = cleaned.startIndex
        while index < cleaned.endIndex {
            let next = cleaned.index(index, offsetBy: 2)
            guard let byte = UInt8(cleaned[index..<next], radix: 16) else { return nil }
            bytes.append(byte)
            index = next
        }
        return bytes
    }

    static func hex(_ bytes: some Sequence<UInt8>) -> String {
        bytes.map { String(format: "%02x", $0) }.joined(separator: " ")
    }
}
