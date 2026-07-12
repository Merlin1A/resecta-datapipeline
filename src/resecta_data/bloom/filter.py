"""Build-side Bloom filter.

=== SHARED WITH SWIFT ===
The ``contains`` method below mirrors the Swift reader at
  ../Packages/RedactionEngine/Sources/RedactionEngine/Detection/Gazetteer/BloomFilter.swift
Membership uses Kirsch-Mitzenmacher double-hashing:

    pos_i = ((h1 + i * h2) mod 2^64) mod m    for i in 0..<k

where ``(h1, h2) = MurmurHash3_x64_128(bytes, seed & 0xFFFF_FFFF)``.

``mmh3`` is used on the build path for speed; the pure-Python port in
``hasher.py`` is validated against it in tests.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Final

import mmh3

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.process import effective_workers, request_parent_death_signal

from .normalize import nfkc_lower
from .packer import pack

_UINT64_MASK: Final[int] = (1 << 64) - 1
_MAX_K: Final[int] = 0xFF

# Maximum number of parallel chunks used by ``BloomFilter.build_parallel``.
# Pool startup overhead dwarfs hashing work past ~8 workers at our corpus size.
_MAX_BUILD_WORKERS: Final[int] = 8

# Minimum keys-per-chunk below which parallel dispatch is pointless; the pool
# spin-up (fork + pickle + IPC) costs more than the work itself.
_MIN_KEYS_PER_CHUNK: Final[int] = 2_000


def optimal_bits(n: int, fpr: float) -> int:
    """Minimum bit-array size for ``n`` items at false-positive rate ``fpr``.

    Uses the standard formula ``m = -n * ln(p) / (ln 2)^2`` rounded up to the
    nearest multiple of 8 so the bit-array size is byte-aligned.
    """
    if n <= 0:
        raise PipelineError(f"n must be positive, got {n}")
    if not 0 < fpr < 1:
        raise PipelineError(f"fpr must be in (0, 1), got {fpr}")
    raw = -n * math.log(fpr) / (math.log(2) ** 2)
    # Round up to the next multiple of 8 bits.
    return int(math.ceil(raw / 8) * 8)


@dataclass(slots=True)
class BloomFilter:
    """Build-side Bloom filter.

    Attributes:
        m: Bit-array size in bits.
        k: Number of hash functions.
        seed: Hash seed (lower 32 bits used).
        bit_array: Mutable bytearray of length ``ceil(m / 8)``.
        row_count: Number of inserts that have been applied.
    """

    m: int
    k: int
    seed: int
    bit_array: bytearray
    row_count: int = 0

    @classmethod
    def empty(cls, *, m: int, k: int, seed: int) -> BloomFilter:
        """Create an empty filter with the given parameters."""
        if m <= 0:
            raise PipelineError(f"m must be positive, got {m}")
        if not 1 <= k <= _MAX_K:
            raise PipelineError(f"k must be in [1, {_MAX_K}], got {k}")
        return cls(m=m, k=k, seed=seed, bit_array=bytearray(math.ceil(m / 8)))

    def insert(self, normalized_key: str) -> None:
        """Insert a pre-normalized key. Caller is responsible for normalization."""
        for pos in self._positions(normalized_key):
            self.bit_array[pos // 8] |= 1 << (pos % 8)
        self.row_count += 1

    def contains(self, key: str) -> bool:
        """Check membership. Normalizes ``key`` before hashing."""
        normalized = nfkc_lower(key)
        return all(
            (self.bit_array[pos // 8] >> (pos % 8)) & 1 for pos in self._positions(normalized)
        )

    def _positions(self, normalized_key: str) -> list[int]:
        key_bytes = normalized_key.encode("utf-8")
        h1, h2 = mmh3.hash64(key_bytes, seed=self.seed & 0xFFFFFFFF, signed=False)
        return [((h1 + i * h2) & _UINT64_MASK) % self.m for i in range(self.k)]

    def serialize(self, source_hash: bytes) -> bytes:
        """Pack the filter into RSBF bytes. ``source_hash`` is the 32-byte SHA-256."""
        return pack(
            k=self.k,
            m=self.m,
            seed=self.seed,
            row_count=self.row_count,
            source_hash=source_hash,
            bit_array=bytes(self.bit_array),
        )

    @classmethod
    def build_parallel(
        cls,
        *,
        m: int,
        k: int,
        seed: int,
        keys: Sequence[str],
        workers: int | None = None,
    ) -> BloomFilter:
        """Build a filter by hashing ``keys`` in parallel chunks.

        Functionally equivalent to ``BloomFilter.empty(...)`` followed by
        sequential ``insert`` on each key — but each chunk's bit-array is
        produced in a worker process and OR-merged into the final filter.

        OR on bit-arrays is associative + commutative, and integer addition
        is too, so the resulting bit-array, ``row_count``, and serialized
        bytes are independent of worker completion order. This is the
        invariant the parallel/serial equivalence test in
        ``tests/test_bloom_filter.py`` asserts.

        Args:
            m: Bit-array size in bits (see :func:`optimal_bits`).
            k: Number of hash functions.
            seed: Hash seed; only the lower 32 bits are used.
            keys: Pre-normalized keys. Order does not affect output.
            workers: Pool size. ``None`` (default) uses ``os.cpu_count()``,
                clamped to :data:`_MAX_BUILD_WORKERS`. A value of ``1`` forces
                serial execution (no pool overhead).
        """
        if m <= 0:
            raise PipelineError(f"m must be positive, got {m}")
        if not 1 <= k <= _MAX_K:
            raise PipelineError(f"k must be in [1, {_MAX_K}], got {k}")

        if workers is None:
            # Honour the same RESECTA_BUILD_WORKERS lever as
            # parse_sources_parallel + corpus.generate.build, so a single
            # env var caps all three pools. See common.process.effective_workers.
            effective = effective_workers(default=os.cpu_count() or 1, hard_cap=_MAX_BUILD_WORKERS)
        else:
            effective = workers
        # Mirror parse_sources_parallel: under pytest-xdist with workers=None,
        # fall to the serial path so N outer xdist workers * M inner pool
        # workers do not stack. Tests that want to exercise the parallel
        # branch pass workers= explicitly.
        if workers is None and os.environ.get("PYTEST_XDIST_WORKER"):
            effective = 1
        chunk_count = max(1, min(effective, _MAX_BUILD_WORKERS))
        # Tiny corpora (e.g. the ~30k given-names aren't tiny, but the test
        # fixtures are): pool spin-up dwarfs the hashing.
        if chunk_count <= 1 or len(keys) < _MIN_KEYS_PER_CHUNK * chunk_count:
            bf = cls.empty(m=m, k=k, seed=seed)
            for key in keys:
                bf.insert(key)
            return bf

        # Deterministic integer-split chunking. Input order is preserved but
        # doesn't matter for correctness; keeping the chunk boundaries stable
        # just makes failures easier to reproduce.
        n = len(keys)
        base, rem = divmod(n, chunk_count)
        chunks: list[list[str]] = []
        start = 0
        for i in range(chunk_count):
            size = base + (1 if i < rem else 0)
            chunks.append(list(keys[start : start + size]))
            start += size

        # Explicit shutdown(cancel_futures=True) on every exit path; see
        # corpus_ingest.parse_sources_parallel for rationale.
        pool = ProcessPoolExecutor(max_workers=chunk_count)
        try:
            results = list(
                pool.map(
                    _hash_keys_into_bitset,
                    chunks,
                    [m] * chunk_count,
                    [k] * chunk_count,
                    [seed] * chunk_count,
                    chunksize=1,
                )
            )
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        # OR-merge partial bit-arrays via one bigint reduction — C-level and
        # faster than a Python byte-by-byte loop at m≈2M.
        m_bytes = math.ceil(m / 8)
        acc = 0
        total_count = 0
        for partial_bits, partial_count in results:
            acc |= int.from_bytes(partial_bits, "big")
            total_count += partial_count
        merged = bytearray(acc.to_bytes(m_bytes, "big"))

        return cls(m=m, k=k, seed=seed, bit_array=merged, row_count=total_count)


def _hash_keys_into_bitset(
    keys: list[str],
    m: int,
    k: int,
    seed: int,
) -> tuple[bytes, int]:
    """Parallel-insert worker for :meth:`BloomFilter.build_parallel`.

    Allocates a fresh ``bytearray(ceil(m/8))``, hashes each key via the same
    mmh3 / Kirsch-Mitzenmacher scheme as :meth:`BloomFilter.insert`, and
    returns ``(bit_array_bytes, len(keys))``. The caller OR-merges the bytes
    and sums the counts — both operations are commutative, so the merged
    result does not depend on worker completion order.

    Module-level so it is picklable for ``ProcessPoolExecutor``.
    """
    # First action: register parent-death signal so this worker self-
    # terminates if the builder parent dies (see common/process.py).
    request_parent_death_signal()
    bit_array = bytearray(math.ceil(m / 8))
    effective_seed = seed & 0xFFFFFFFF
    for normalized_key in keys:
        key_bytes = normalized_key.encode("utf-8")
        h1, h2 = mmh3.hash64(key_bytes, seed=effective_seed, signed=False)
        for i in range(k):
            pos = ((h1 + i * h2) & _UINT64_MASK) % m
            bit_array[pos // 8] |= 1 << (pos % 8)
    return bytes(bit_array), len(keys)
