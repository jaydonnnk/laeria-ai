"""bitarray.util subset — pure Python (see package docstring)."""

from __future__ import annotations

from . import bitarray


def ba2int(a: bitarray, signed: bool = False) -> int:
    """Big-endian bits -> int."""
    n = 0
    for b in a:
        n = (n << 1) | (1 if b else 0)
    if signed and len(a) and a[0]:
        n -= 1 << len(a)
    return n


def int2ba(n: int, length: int | None = None, endian: str = "big", signed: bool = False) -> bitarray:
    """int -> big-endian bits of `length`."""
    if length is None:
        length = max(n.bit_length(), 1)
    if signed and n < 0:
        n += 1 << length
    bits = [(n >> i) & 1 for i in range(length - 1, -1, -1)]
    if endian == "little":
        bits.reverse()
    result = bitarray()
    result._bits = bits
    return result
