"""Determinism: ``BloomFilter.build_parallel`` ≡ serial insert.

Parallel chunked hashing uses OR-merge on partial bit-arrays and integer-sum
on per-chunk counts. Both are commutative, so the resulting filter must be
byte-identical to the serial build regardless of worker completion order.
This file asserts that equivalence — at a large N where the parallel path
actually spawns workers, and at a tiny N where the serial fallback kicks in.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from resecta_data.bloom.filter import _MAX_BUILD_WORKERS, BloomFilter, optimal_bits
from resecta_data.common.determinism import CANONICAL_SEED

_FPR = 0.001
_K = 10
_SOURCE_HASH = b"\x00" * 32


def _build_serial(keys: tuple[str, ...], *, m: int, k: int, seed: int) -> BloomFilter:
    bf = BloomFilter.empty(m=m, k=k, seed=seed)
    for key in keys:
        bf.insert(key)
    return bf


@pytest.mark.parametrize(
    ("n", "workers"),
    [
        (5_000, 2),  # triggers parallel path: 5000 >= 2 * _MIN_KEYS_PER_CHUNK
        (10, 8),  # tiny input falls to serial path even with workers=8
        (1, 4),  # degenerate single-key input
    ],
    ids=["large-parallel", "tiny-serial-fallback", "degenerate-1"],
)
def test_build_parallel_matches_serial(n: int, workers: int) -> None:
    keys = tuple(f"name{i:07d}" for i in range(n))
    m = max(optimal_bits(n, _FPR), 8)
    seed = CANONICAL_SEED

    serial = _build_serial(keys, m=m, k=_K, seed=seed)
    parallel = BloomFilter.build_parallel(m=m, k=_K, seed=seed, keys=keys, workers=workers)

    assert parallel.bit_array == serial.bit_array
    assert parallel.row_count == serial.row_count == n
    assert parallel.serialize(_SOURCE_HASH) == serial.serialize(_SOURCE_HASH)


def test_build_parallel_is_stable_across_runs() -> None:
    """Two parallel builds over the same keys must produce identical bytes."""
    keys = tuple(f"k{i:06d}" for i in range(5_000))
    m = max(optimal_bits(len(keys), _FPR), 8)
    first = BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys, workers=2)
    second = BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys, workers=2)
    assert first.serialize(_SOURCE_HASH) == second.serialize(_SOURCE_HASH)


def test_build_parallel_key_order_irrelevant() -> None:
    """Key order into build_parallel must not affect the final bytes.

    OR-on-bitarrays is commutative, so shuffling the input should produce a
    byte-identical filter. A failure here would indicate order-dependent
    state crept into the hashing path (it shouldn't — ``mmh3.hash64`` is
    pure).
    """
    keys = tuple(f"name{i:06d}" for i in range(5_000))
    m = max(optimal_bits(len(keys), _FPR), 8)
    forward = BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys, workers=2)
    reversed_keys = tuple(reversed(keys))
    backward = BloomFilter.build_parallel(
        m=m, k=_K, seed=CANONICAL_SEED, keys=reversed_keys, workers=2
    )
    assert forward.bit_array == backward.bit_array
    assert forward.row_count == backward.row_count
    assert forward.serialize(_SOURCE_HASH) == backward.serialize(_SOURCE_HASH)


# ---- worker-count knobs (RESECTA_BUILD_WORKERS, PYTEST_XDIST_WORKER) -----


class _RecordingPool:
    """Test double for ProcessPoolExecutor that records ``max_workers``."""

    captured: ClassVar[dict[str, int]] = {}

    def __init__(self, max_workers: int) -> None:
        type(self).captured["max_workers"] = max_workers

    def __enter__(self) -> _RecordingPool:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def map(self, fn: Any, *iterables: Any, chunksize: int = 1) -> Any:
        return (fn(*args) for args in zip(*iterables, strict=True))

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        return None


def test_build_parallel_honors_build_workers_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RESECTA_BUILD_WORKERS`` caps the pool when ``workers`` is left default."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "2")
    _RecordingPool.captured = {}
    monkeypatch.setattr(
        "resecta_data.bloom.filter.ProcessPoolExecutor",
        _RecordingPool,
    )
    keys = tuple(f"k{i:06d}" for i in range(50_000))  # large enough to force pool path
    m = max(optimal_bits(len(keys), _FPR), 8)
    BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys)
    assert _RecordingPool.captured.get("max_workers") == 2


def test_build_parallel_build_workers_env_clamped_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized env value still clamps to ``_MAX_BUILD_WORKERS``."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "100")
    _RecordingPool.captured = {}
    monkeypatch.setattr(
        "resecta_data.bloom.filter.ProcessPoolExecutor",
        _RecordingPool,
    )
    keys = tuple(f"k{i:06d}" for i in range(50_000))
    m = max(optimal_bits(len(keys), _FPR), 8)
    BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys)
    assert _RecordingPool.captured.get("max_workers", 0) <= _MAX_BUILD_WORKERS


def test_build_parallel_byte_identical_with_env_capped_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capping workers via env must not change the output bytes (commutativity)."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    keys = tuple(f"name{i:07d}" for i in range(5_000))
    m = max(optimal_bits(len(keys), _FPR), 8)

    serial = BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys, workers=1)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "2")
    capped = BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys)

    assert capped.serialize(_SOURCE_HASH) == serial.serialize(_SOURCE_HASH)


def test_build_parallel_respects_pytest_xdist_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under xdist with workers=None, the parent must take the serial path.

    Mirrors test_parse_sources_parallel_respects_pytest_xdist_env from
    test_bloom_corpus_ingest.py — change #7 in the OOM-freeze fix plan.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "ProcessPoolExecutor must not be instantiated under PYTEST_XDIST_WORKER"
        )

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setattr(
        "resecta_data.bloom.filter.ProcessPoolExecutor",
        _boom,
    )
    keys = tuple(f"k{i:06d}" for i in range(50_000))  # large enough to *want* the pool
    m = max(optimal_bits(len(keys), _FPR), 8)
    bf = BloomFilter.build_parallel(m=m, k=_K, seed=CANONICAL_SEED, keys=keys)
    assert bf.row_count == len(keys)
