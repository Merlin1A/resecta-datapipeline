"""RSBF pack/unpack round-trip and membership lock-in tests."""

from __future__ import annotations

import hashlib
import random

import pytest

from resecta_data.bloom import BloomFilter, nfkc_lower, optimal_bits, pack, unpack
from resecta_data.bloom.spec import HEADER_SIZE, RSBF_MAGIC, RSBF_VERSION
from resecta_data.common.exceptions import PipelineError


def test_pack_unpack_round_trip_bit_array() -> None:
    rng = random.Random(20260416)  # noqa: S311 — determinism, not security
    m = 2048
    bit_array = bytes(rng.randint(0, 255) for _ in range(m // 8))
    source_hash = hashlib.sha256(b"sample").digest()

    blob = pack(k=10, m=m, seed=42, row_count=17, source_hash=source_hash, bit_array=bit_array)
    header, body = unpack(blob)

    assert header.version == RSBF_VERSION
    assert header.k == 10
    assert header.m == m
    assert header.seed == 42
    assert header.row_count == 17
    assert header.source_hash == source_hash
    assert body == bit_array

    blob2 = pack(
        k=header.k,
        m=header.m,
        seed=header.seed,
        row_count=header.row_count,
        source_hash=header.source_hash,
        bit_array=body,
    )
    assert blob2 == blob


def test_all_inserted_keys_query_true() -> None:
    originals = ["José", "Garcia", "Smith", "Nuñez", "\ufb01sher"]
    bf = BloomFilter.empty(m=optimal_bits(len(originals), 0.001), k=10, seed=20260416)
    for raw in originals:
        bf.insert(nfkc_lower(raw))
    # Queries that should all hit: original casing, ALL-CAPS, and the canonical
    # normalized form all resolve to the same key after nfkc_lower.
    for candidate in ["José", "JOSÉ", "josé", "garcia", "SMITH", "Nuñez", "fisher", "\ufb01sher"]:
        assert bf.contains(candidate) is True, candidate


def test_rejects_unsupported_magic() -> None:
    with pytest.raises(PipelineError, match="magic"):
        unpack(b"XXXX" + bytes(HEADER_SIZE - 4 + 8))


def test_rejects_unsupported_version() -> None:
    blob = bytearray(HEADER_SIZE + 8)
    blob[0:4] = RSBF_MAGIC
    blob[4:6] = (42).to_bytes(2, "little")
    blob[7:15] = (64).to_bytes(8, "little")
    with pytest.raises(PipelineError, match="version"):
        unpack(bytes(blob))


def test_rejects_short_input() -> None:
    with pytest.raises(PipelineError, match="too short"):
        unpack(b"RSBF")
