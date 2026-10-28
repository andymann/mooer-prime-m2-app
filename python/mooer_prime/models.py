"""Preset and module structs, wire-exact (see docs/PROTOCOL.md)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

NAME_LENGTH = 20
SLOT_COUNT = 20


@dataclass
class PrimeModule:
    """One of the 20 effect slots in a preset, as it travels on the wire:

        slot# | type | n | data[n]      data = n/2 x uint16 LE

    words[0] is enabled (0/1), words[1] the model within the type, the rest
    are parameters. An empty slot is `slot# 00 00`.
    """

    slot: int
    type: int
    words: List[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.type == 0 and not self.words

    @property
    def is_enabled(self) -> bool:
        return bool(self.words) and self.words[0] != 0

    def with_enabled(self, enabled: bool) -> "PrimeModule":
        words = list(self.words)
        if words:
            words[0] = 1 if enabled else 0
        return replace(self, words=words)

    def with_word(self, index: int, value: int) -> "PrimeModule":
        words = list(self.words)
        words[index] = value
        return replace(self, words=words)

    @property
    def model(self) -> Optional[int]:
        return self.words[1] if len(self.words) > 1 else None

    @property
    def parameter_indices(self) -> range:
        """Indices into `words` that are parameters (after enabled/model)."""
        return range(min(2, len(self.words)), len(self.words))

    @property
    def type_name(self) -> str:
        from .catalog import type_name
        return type_name(self.type)

    def encoded(self) -> bytes:
        out = bytearray([self.slot, self.type, len(self.words) * 2])
        for word in self.words:
            out += bytes([word & 0xFF, (word >> 8) & 0xFF])
        return bytes(out)

    @staticmethod
    def parse(data: bytes, offset: int) -> Optional[Tuple["PrimeModule", int]]:
        """Parses one slot record at `offset`; returns (module, next offset)."""
        if offset + 3 > len(data):
            return None
        slot, kind, length = data[offset], data[offset + 1], data[offset + 2]
        end = offset + 3 + length
        if end > len(data):
            return None
        words = [data[i] | data[i + 1] << 8 for i in range(offset + 3, end - 1, 2)]
        return PrimeModule(slot, kind, words), end


@dataclass
class PrimePreset:
    """Full preset as returned by `21 pp` -> `22`."""

    index: int
    name: str
    modules: List[PrimeModule]
    trailer: bytes

    @staticmethod
    def from_payload(payload: bytes) -> Optional["PrimePreset"]:
        """`payload` is the whole 0x22 frame payload, opcode included."""
        if len(payload) < 2 + NAME_LENGTH or payload[0] != 0x22:
            return None
        offset = 2 + NAME_LENGTH
        modules = []
        for _ in range(SLOT_COUNT):
            parsed = PrimeModule.parse(payload, offset)
            if parsed is None:
                return None
            module, offset = parsed
            modules.append(module)
        return PrimePreset(payload[1], decode_name(payload[2:2 + NAME_LENGTH]), modules, bytes(payload[offset:]))


def decode_name(data: bytes) -> str:
    end = data.find(0)
    return bytes(data[:end if end >= 0 else len(data)]).decode("utf-8", errors="replace")


def encode_name(name: str, length: int = NAME_LENGTH) -> bytes:
    """NUL-padded/truncated to `length` bytes, printable ASCII only."""
    ascii_bytes = bytes(ord(c) for c in name if 0x20 <= ord(c) < 0x80)[:length]
    return ascii_bytes + bytes(length - len(ascii_bytes))


def slot_label(index: int) -> str:
    """1...80 -> "0A"..."19D" (4 slots per bank, banks from 0)."""
    if index < 1:
        return "?"
    zero_based = index - 1
    return f"{zero_based // 4}{'ABCD'[zero_based % 4]}"
