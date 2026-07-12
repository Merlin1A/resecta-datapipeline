"""RSBF binary packer / unpacker.

See ``spec.py`` for the format. This module is pure bytes-in / bytes-out — no
hashing, no I/O, no insertion logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from resecta_data.common.exceptions import PipelineError

from .spec import HEADER_SIZE, RSBF_MAGIC, RSBF_VERSION, SOURCE_HASH_SIZE

_MAX_K = 0xFF
_UINT64_MASK = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class RsbfHeader:
    """Parsed RSBF header fields.

    Attributes:
        version: File format version; currently 1.
        k: Number of hash functions used during insertion.
        m: Bit-array size in bits.
        seed: Hash seed; only the lower 32 bits are used by MurmurHash3.
        row_count: Number of unique keys inserted.
        source_hash: SHA-256 of the sorted normalized source rows.
    """

    version: int
    k: int
    m: int
    seed: int
    row_count: int
    source_hash: bytes


def pack(
    *,
    k: int,
    m: int,
    seed: int,
    row_count: int,
    source_hash: bytes,
    bit_array: bytes,
) -> bytes:
    """Serialize a Bloom filter into RSBF bytes.

    Args:
        k: Number of hash functions. Must fit in a u8.
        m: Bit-array size in bits. Must be > 0.
        seed: Hash seed. Full u64 stored; only the lower 32 bits are consumed.
        row_count: Number of unique keys inserted.
        source_hash: Exactly 32 bytes (SHA-256 of the sorted source rows).
        bit_array: Exactly ``ceil(m / 8)`` bytes.

    Returns:
        The complete RSBF byte string (header + bit array).

    Raises:
        PipelineError: If any size invariant is violated.
    """
    if not 1 <= k <= _MAX_K:
        raise PipelineError(f"k={k} does not fit in a u8")
    if m <= 0:
        raise PipelineError(f"m={m} must be positive")
    if len(source_hash) != SOURCE_HASH_SIZE:
        raise PipelineError(f"source_hash must be {SOURCE_HASH_SIZE} bytes, got {len(source_hash)}")
    expected_bytes = math.ceil(m / 8)
    if len(bit_array) != expected_bytes:
        raise PipelineError(
            f"bit_array length {len(bit_array)} does not match m={m} (expected {expected_bytes})"
        )

    header = bytearray(HEADER_SIZE)
    header[0:4] = RSBF_MAGIC
    header[4:6] = RSBF_VERSION.to_bytes(2, "little")
    header[6] = k
    header[7:15] = m.to_bytes(8, "little")
    header[15:23] = (seed & _UINT64_MASK).to_bytes(8, "little")
    header[23:31] = row_count.to_bytes(8, "little")
    header[31:63] = source_hash

    return bytes(header) + bit_array


def unpack(data: bytes) -> tuple[RsbfHeader, bytes]:
    """Parse RSBF bytes. Returns (header, bit_array).

    Raises:
        PipelineError: On any format violation (magic, version, length).
    """
    if len(data) < HEADER_SIZE:
        raise PipelineError(f"RSBF data too short: {len(data)} < {HEADER_SIZE}")
    if data[0:4] != RSBF_MAGIC:
        raise PipelineError(f"RSBF magic mismatch: {data[0:4]!r}")
    version = int.from_bytes(data[4:6], "little")
    if version != RSBF_VERSION:
        raise PipelineError(f"Unsupported RSBF version: {version}")

    k = data[6]
    m = int.from_bytes(data[7:15], "little")
    seed = int.from_bytes(data[15:23], "little")
    row_count = int.from_bytes(data[23:31], "little")
    source_hash = bytes(data[31:63])
    bit_array = bytes(data[HEADER_SIZE:])

    expected_bytes = math.ceil(m / 8)
    if len(bit_array) != expected_bytes:
        raise PipelineError(
            f"RSBF bit-array length {len(bit_array)} does not match m={m} "
            f"(expected {expected_bytes})"
        )

    return (
        RsbfHeader(
            version=version,
            k=k,
            m=m,
            seed=seed,
            row_count=row_count,
            source_hash=source_hash,
        ),
        bit_array,
    )
