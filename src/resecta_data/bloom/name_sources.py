"""The name-corpus ingest the bloom, demographics and corpus commands share.

Where the committed name corpora live, where a build directory keeps its ingest
cache, and the two cached ingests (surnames, given names) that the Bloom filters
and the demographic report are built from.
"""

from __future__ import annotations

from pathlib import Path

from resecta_data.bloom.corpus_ingest import IngestResult
from resecta_data.bloom.ingest_cache import _INGEST_CACHE_SUBDIR, _ingest_with_cache
from resecta_data.bloom.paranames_specs import _given_name_ingest_specs, _surname_ingest_specs

__all__ = ["DEFAULT_SOURCES_DIR", "ingest_cache_dir", "ingest_given_names", "ingest_surnames"]

DEFAULT_SOURCES_DIR = Path("src/resecta_data/gazetteers/sources")
"""The committed name-corpus source subdirectories (relative to the repository root)."""


def ingest_cache_dir(build_dir: Path) -> Path:
    """The ingest cache of ``build_dir`` (build-relative, so two build dirs never share one)."""
    return build_dir / _INGEST_CACHE_SUBDIR


def ingest_surnames(
    sources_dir: Path,
    *,
    workers: int | None = None,
    cache_dir: Path | None = None,
) -> tuple[IngestResult, list[str]]:
    """The merged surname corpus and the source ids it drew from, through the ingest cache."""
    return _ingest_with_cache(
        specs=_surname_ingest_specs(sources_dir),
        workers=workers,
        cache_dir=cache_dir,
        cache_name="surnames.json",
    )


def ingest_given_names(
    sources_dir: Path,
    *,
    workers: int | None = None,
    cache_dir: Path | None = None,
) -> tuple[IngestResult, list[str]]:
    """The merged given-name corpus and the source ids it drew from, through the ingest cache."""
    return _ingest_with_cache(
        specs=_given_name_ingest_specs(sources_dir),
        workers=workers,
        cache_dir=cache_dir,
        cache_name="given-names.json",
    )
