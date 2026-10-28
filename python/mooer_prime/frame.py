"""Wire framing used by the Prime M2 on BLE (FFF0 service) and USB HID:

    aa 55 | len (uint16 LE) | payload (len bytes) | CRC-16 (BE)

CRC is poly 0x1021, init 0x0000, unreflected, xorout 0xFFFF (CRC-16/GSM),
computed over the length field + payload. See docs/PROTOCOL.md in the
Swift project.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

SYNC = b"\xaa\x55"


def crc16(data: Iterable[int]) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc ^ 0xFFFF


def encode(payload: bytes) -> bytes:
    length = len(payload)
    body = bytes([length & 0xFF, length >> 8]) + bytes(payload)
    crc = crc16(body)
    return SYNC + body + bytes([crc >> 8, crc & 0xFF])


@dataclass
class Decoded:
    payload: bytes
    crc_ok: bool


def decode(data: bytes) -> Optional[Decoded]:
    """None if `data` isn't a single complete aa 55 frame."""
    if len(data) < 6 or data[0] != 0xAA or data[1] != 0x55:
        return None
    length = data[2] | data[3] << 8
    if len(data) != length + 6:
        return None
    expected = data[-2] << 8 | data[-1]
    return Decoded(payload=bytes(data[4:4 + length]), crc_ok=crc16(data[2:-2]) == expected)


def describe(decoded: Decoded) -> str:
    """One-line human summary for the log."""
    parts = [f"frame len={len(decoded.payload)}", "crc ok" if decoded.crc_ok else "CRC BAD"]
    if decoded.payload:
        kind = decoded.payload[0]
        parts.append(f"type=0x{kind:02x}")
        if kind == 0x15 and len(decoded.payload) > 41:
            slot = decoded.payload[1] - 1
            parts.append(f"preset {slot // 4}{'ABCD'[slot % 4]}")
            parts.append(f"[45]={decoded.payload[41]}")
    return " ".join(parts)


def parse_hex(text: str) -> Optional[bytes]:
    """Parses "aa 55 0a", "aa550a", "0xaa,0x55" etc. None on any invalid token."""
    cleaned = "".join(c for c in text.lower().replace("0x", "") if c not in " ,:-\n\t")
    if len(cleaned) % 2:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


def hex_bytes(data: Iterable[int]) -> str:
    return " ".join(f"{b:02x}" for b in data)


class FrameReassembler:
    """Collects raw bytes (BLE notifications or zero-padded HID reports) and
    yields whole frames. Drops bytes until an aa 55 header to resync."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def clear(self) -> None:
        self.buffer.clear()

    def feed(self, data: bytes):
        self.buffer += data
        while True:
            start = self.buffer.find(SYNC)
            if start < 0:
                # Keep a trailing 0xaa: it may be the first half of a header.
                del self.buffer[:max(0, len(self.buffer) - 1)]
                if self.buffer and self.buffer[0] != 0xAA:
                    self.buffer.clear()
                return
            del self.buffer[:start]
            if len(self.buffer) < 4:
                return
            length = self.buffer[2] | self.buffer[3] << 8
            if len(self.buffer) < length + 6:
                return
            frame = bytes(self.buffer[:length + 6])
            del self.buffer[:length + 6]
            yield frame
