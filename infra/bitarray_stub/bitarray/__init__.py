"""Pure-Python bitarray — Windows Smart App Control workaround.

SAC blocks the unsigned C extension in the real `bitarray` wheel, which
eth_account imports (via its HD-wallet / BIP39 mnemonic module) even when the
app only ever uses raw private keys. This drop-in provides the small surface
eth_account needs — the `bitarray` class plus util.ba2int / util.int2ba — in
pure Python, so no native DLL exists for SAC to block.

It is functional (BIP39 would work), just slower. Only the laptop venv uses
it; Linux/VPS keeps the real C bitarray. Reinstall after a venv rebuild:
    cp -r infra/bitarray_stub/bitarray  <venv>/Lib/site-packages/
Bits are stored MSB-first, matching bitarray's default big-endian semantics.
"""

from __future__ import annotations

from collections.abc import Iterable


class bitarray:
    __slots__ = ("_bits",)

    def __init__(self, initial: Iterable[int] | str | None = None) -> None:
        if initial is None:
            self._bits: list[int] = []
        elif isinstance(initial, str):
            self._bits = [1 if c == "1" else 0 for c in initial]
        elif isinstance(initial, bitarray):
            self._bits = list(initial._bits)
        else:
            self._bits = [1 if b else 0 for b in initial]

    def frombytes(self, data: bytes) -> None:
        for byte in data:
            for i in range(7, -1, -1):
                self._bits.append((byte >> i) & 1)

    def tobytes(self) -> bytes:
        bits = self._bits
        pad = (-len(bits)) % 8
        padded = bits + [0] * pad
        out = bytearray()
        for i in range(0, len(padded), 8):
            byte = 0
            for b in padded[i : i + 8]:
                byte = (byte << 1) | b
            out.append(byte)
        return bytes(out)

    def extend(self, other: Iterable[int] | bitarray) -> None:
        if isinstance(other, bitarray):
            self._bits.extend(other._bits)
        else:
            self._bits.extend(1 if b else 0 for b in other)

    def to01(self) -> str:
        return "".join(str(b) for b in self._bits)

    def tolist(self) -> list[int]:
        return list(self._bits)

    def __getitem__(self, item):
        if isinstance(item, slice):
            result = bitarray()
            result._bits = self._bits[item]
            return result
        return self._bits[item]

    def __len__(self) -> int:
        return len(self._bits)

    def __iter__(self):
        return iter(self._bits)

    def __eq__(self, other) -> bool:
        return isinstance(other, bitarray) and self._bits == other._bits

    def __repr__(self) -> str:
        return f"bitarray('{self.to01()}')"
