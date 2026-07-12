"""Tests for the 0.6 loud bootstrap-fallback guard in _paranames_full_specs.

The bloom build must never silently ingest only the ~3k bootstrap sample:
absent corpus → warning (or hard failure under RESECTA_REQUIRE_LFS=1);
pointer-sized corpus (unhydrated git-lfs checkout) → hard failure with a
hydration hint instead of a cryptic BadGzipFile mid-ingest.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from resecta_data.cli import _paranames_full_specs
from resecta_data.common.exceptions import PipelineError


def _sources_with_paranames(tmp_path: Path, size: int | None) -> Path:
    sources = tmp_path / "sources"
    paranames = sources / "paranames"
    paranames.mkdir(parents=True)
    if size is not None:
        (paranames / "paranames_full.tsv.gz").write_bytes(b"x" * size)
    return sources


def test_absent_corpus_warns_and_returns_empty(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESECTA_REQUIRE_LFS", raising=False)
    sources = _sources_with_paranames(tmp_path, size=None)
    with caplog.at_level(logging.WARNING, logger="resecta_data.cli"):
        specs = _paranames_full_specs(sources)
    assert specs == []
    assert any("bootstrap sample only" in rec.message for rec in caplog.records)


def test_absent_corpus_raises_under_require_lfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESECTA_REQUIRE_LFS", "1")
    sources = _sources_with_paranames(tmp_path, size=None)
    with pytest.raises(PipelineError, match="RESECTA_REQUIRE_LFS"):
        _paranames_full_specs(sources)


def test_pointer_sized_corpus_raises_with_hydration_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An LFS pointer file (~130 B) must fail loud, not BadGzipFile mid-ingest."""
    monkeypatch.delenv("RESECTA_REQUIRE_LFS", raising=False)
    sources = _sources_with_paranames(tmp_path, size=130)
    with pytest.raises(PipelineError, match="git lfs"):
        _paranames_full_specs(sources)


def test_hydrated_corpus_returns_monolithic_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESECTA_REQUIRE_LFS", raising=False)
    sources = _sources_with_paranames(tmp_path, size=1_000_001)
    specs = _paranames_full_specs(sources)
    assert len(specs) == 1


def test_shards_take_precedence_and_skip_size_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With shards present, the monolithic file (even a pointer) is unused."""
    monkeypatch.delenv("RESECTA_REQUIRE_LFS", raising=False)
    sources = _sources_with_paranames(tmp_path, size=130)
    shards = sources / "paranames" / "shards"
    shards.mkdir()
    (shards / "paranames_full_shard_000.tsv.gz").write_bytes(b"shard")
    specs = _paranames_full_specs(sources)
    assert len(specs) == 1
