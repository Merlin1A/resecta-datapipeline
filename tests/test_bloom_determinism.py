"""Determinism for the Phase 2 Bloom build path.

Rebuilds both filters twice at the canonical seed and asserts byte-identical
output. Any drift here is a determinism defect.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resecta_data.bloom import BloomFilter, optimal_bits
from resecta_data.bloom.corpus_ingest import (
    merge,
    parse_census_spanish,
    parse_census_surnames,
    parse_paranames,
    parse_popnames_by_type,
    parse_ssa_given_names,
)
from resecta_data.cli import _ingest_surnames
from resecta_data.common.determinism import CANONICAL_SEED

_SOURCES = Path(__file__).parent.parent / "src" / "resecta_data" / "gazetteers" / "sources"


def _surname_rows() -> list:  # type: ignore[type-arg]
    return [
        *parse_census_surnames(
            _SOURCES / "census_surnames/census_surnames_bootstrap_20260416.csv",
            "census_surnames",
        ),
        *parse_census_spanish(
            _SOURCES / "census_spanish/census_spanish_bootstrap_20260416.txt",
            "census_spanish",
        ),
        *parse_paranames(
            _SOURCES / "paranames/paranames_bootstrap_20260416.tsv",
            "paranames",
        ),
        *parse_popnames_by_type(
            _SOURCES / "popnames/popnames_bootstrap_20260416.csv",
            "popnames",
            "surname",
        ),
    ]


def _given_rows() -> list:  # type: ignore[type-arg]
    return [
        *parse_ssa_given_names(
            _SOURCES / "ssa_given_names/ssa_bootstrap_20260416.txt",
            "ssa_given_names",
        ),
        *parse_paranames(
            _SOURCES / "paranames/paranames_bootstrap_20260416.tsv",
            "paranames",
        ),
        *parse_popnames_by_type(
            _SOURCES / "popnames/popnames_bootstrap_20260416.csv",
            "popnames",
            "given",
        ),
    ]


@pytest.mark.determinism
@pytest.mark.parametrize(
    ("rows_fn"),
    [_surname_rows, _given_rows],
    ids=["surnames", "given-names"],
)
def test_build_is_deterministic(rows_fn: object) -> None:
    def build_bytes() -> bytes:
        rows = rows_fn()  # type: ignore[operator]
        ingest = merge(rows)
        m = optimal_bits(len(ingest.rows), 0.001)
        bf = BloomFilter.empty(m=m, k=10, seed=CANONICAL_SEED)
        for key in ingest.rows:
            bf.insert(key)
        return bf.serialize(ingest.source_hash)

    assert build_bytes() == build_bytes()


def test_ingest_source_hash_is_order_independent() -> None:
    """Swapping source order must not change the SHA-256 of the merged rows."""
    rows = _surname_rows()
    shuffled = sorted(rows, key=lambda r: r.source_id)[::-1]
    assert merge(rows).source_hash == merge(shuffled).source_hash


def _build_small_sources_dir(tmp_path: Path) -> Path:
    """Mirror only the bootstrap fixtures from the real sources tree.

    ``_surname_ingest_specs`` adds full-file specs only when those files
    exist; pointing it at this tmp tree keeps the parse fast and bounded
    while still exercising the multi-source ingest code path.
    """
    src_root = _SOURCES
    dst_root = tmp_path / "sources"
    dst_root.mkdir()
    bootstraps = (
        "census_surnames/census_surnames_bootstrap_20260416.csv",
        "census_spanish/census_spanish_bootstrap_20260416.txt",
        "paranames/paranames_bootstrap_20260416.tsv",
        "popnames/popnames_bootstrap_20260416.csv",
        "ssa_given_names/ssa_bootstrap_20260416.txt",
    )
    for rel in bootstraps:
        src_file = src_root / rel
        dst_file = dst_root / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
    return dst_root


@pytest.mark.determinism
@pytest.mark.parametrize("workers", [1, 2, 4], ids=lambda w: f"workers={w}")
def test_build_is_deterministic_with_parallel_ingest(
    workers: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Varying ingest worker count must not change the bloom artifact bytes.

    Uses ``_ingest_surnames`` over a bootstrap-only sources tree so the
    parallel ingest code path runs end-to-end under each worker count, and
    the resulting filter serialization is compared against the workers=1
    baseline for byte-equality.
    """
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    sources_dir = _build_small_sources_dir(tmp_path)

    def _build_artifact(w: int) -> bytes:
        ingest, _ = _ingest_surnames(sources_dir, workers=w)
        m = optimal_bits(len(ingest.rows), 0.001)
        bf = BloomFilter.empty(m=m, k=10, seed=CANONICAL_SEED)
        for key in ingest.rows:
            bf.insert(key)
        return bf.serialize(ingest.source_hash)

    baseline = _build_artifact(1)
    parametrized = _build_artifact(workers)
    assert parametrized == baseline
