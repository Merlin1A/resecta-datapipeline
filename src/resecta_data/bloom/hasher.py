"""Pure-Python port of MurmurHash3_x64_128 (Austin Appleby, public domain).

=== SHARED WITH SWIFT ===
Output must match byte-for-byte the Swift port at
  ../Packages/RedactionEngine/Sources/RedactionEngine/Detection/Gazetteer/BloomFilter.swift
and the reference implementation in the ``mmh3`` Python package
(``mmh3.hash64(data, seed=seed, signed=False)``).

Both alternate implementations exist in the tree so the build pipeline can
validate equivalence via ``tests/test_murmurhash3_parity.py``. The build hot
path uses ``mmh3`` for speed; this pure-Python version is the reference and the
fallback if ``mmh3`` is ever unavailable.

The tail-block literals (1..15, shift counts 8..56) are part of the algorithm
definition; extracting them to named constants would obscure the translation
from Appleby's reference source without adding safety.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from typing import Final

_UINT64_MASK: Final[int] = (1 << 64) - 1
_C1: Final[int] = 0x87C37B91114253D5
_C2: Final[int] = 0x4CF5AD432745937F
_BLOCK_SIZE: Final[int] = 16


def _rotl64(x: int, r: int) -> int:
    x &= _UINT64_MASK
    return ((x << r) | (x >> (64 - r))) & _UINT64_MASK


def _fmix64(k: int) -> int:
    k &= _UINT64_MASK
    k ^= k >> 33
    k = (k * 0xFF51AFD7ED558CCD) & _UINT64_MASK
    k ^= k >> 33
    k = (k * 0xC4CEB9FE1A85EC53) & _UINT64_MASK
    k ^= k >> 33
    return k


def murmurhash3_x64_128(data: bytes, seed: int) -> tuple[int, int]:  # noqa: PLR0912, PLR0915
    """Compute the 128-bit MurmurHash3 of ``data`` as two u64 halves.

    The tail block uses a direct translation of Appleby's reference switch
    statement; the numeric offsets and shift counts (1..15, 8..56) are part
    of the algorithm definition and intentionally not extracted to named
    constants (would degrade readability vs. the reference source).

    Args:
        data: Raw bytes to hash.
        seed: 32-bit unsigned seed. Only the lower 32 bits are used (matching
            the Swift port and ``mmh3.hash64(..., seed=seed)``).

    Returns:
        ``(h1, h2)`` — the low 64 bits and the high 64 bits respectively.
    """
    seed32 = seed & 0xFFFFFFFF
    length = len(data)
    nblocks = length // _BLOCK_SIZE

    h1 = seed32
    h2 = seed32

    for i in range(nblocks):
        off = i * _BLOCK_SIZE
        k1 = int.from_bytes(data[off : off + 8], "little")
        k2 = int.from_bytes(data[off + 8 : off + 16], "little")

        k1 = (k1 * _C1) & _UINT64_MASK
        k1 = _rotl64(k1, 31)
        k1 = (k1 * _C2) & _UINT64_MASK
        h1 ^= k1
        h1 = _rotl64(h1, 27)
        h1 = (h1 + h2) & _UINT64_MASK
        h1 = (h1 * 5 + 0x52DCE729) & _UINT64_MASK

        k2 = (k2 * _C2) & _UINT64_MASK
        k2 = _rotl64(k2, 33)
        k2 = (k2 * _C1) & _UINT64_MASK
        h2 ^= k2
        h2 = _rotl64(h2, 31)
        h2 = (h2 + h1) & _UINT64_MASK
        h2 = (h2 * 5 + 0x38495AB5) & _UINT64_MASK

    tail_offset = nblocks * _BLOCK_SIZE
    k1 = 0
    k2 = 0
    remainder = length & 15

    if remainder >= 15:
        k2 ^= data[tail_offset + 14] << 48
    if remainder >= 14:
        k2 ^= data[tail_offset + 13] << 40
    if remainder >= 13:
        k2 ^= data[tail_offset + 12] << 32
    if remainder >= 12:
        k2 ^= data[tail_offset + 11] << 24
    if remainder >= 11:
        k2 ^= data[tail_offset + 10] << 16
    if remainder >= 10:
        k2 ^= data[tail_offset + 9] << 8
    if remainder >= 9:
        k2 ^= data[tail_offset + 8]
        k2 = (k2 * _C2) & _UINT64_MASK
        k2 = _rotl64(k2, 33)
        k2 = (k2 * _C1) & _UINT64_MASK
        h2 ^= k2
    if remainder >= 8:
        k1 ^= data[tail_offset + 7] << 56
    if remainder >= 7:
        k1 ^= data[tail_offset + 6] << 48
    if remainder >= 6:
        k1 ^= data[tail_offset + 5] << 40
    if remainder >= 5:
        k1 ^= data[tail_offset + 4] << 32
    if remainder >= 4:
        k1 ^= data[tail_offset + 3] << 24
    if remainder >= 3:
        k1 ^= data[tail_offset + 2] << 16
    if remainder >= 2:
        k1 ^= data[tail_offset + 1] << 8
    if remainder >= 1:
        k1 ^= data[tail_offset + 0]
        k1 = (k1 * _C1) & _UINT64_MASK
        k1 = _rotl64(k1, 31)
        k1 = (k1 * _C2) & _UINT64_MASK
        h1 ^= k1

    h1 ^= length
    h2 ^= length

    h1 = (h1 + h2) & _UINT64_MASK
    h2 = (h2 + h1) & _UINT64_MASK
    h1 = _fmix64(h1)
    h2 = _fmix64(h2)
    h1 = (h1 + h2) & _UINT64_MASK
    h2 = (h2 + h1) & _UINT64_MASK

    return h1, h2
