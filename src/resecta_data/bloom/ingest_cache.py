"""The on-disk ingest cache under build/: a fingerprinted canonical dump of an IngestResult,
read back when the sources have not changed."""

from __future__ import annotations

import logging
from pathlib import Path

from resecta_data.bloom.corpus_ingest import (
    IngestResult,
    ParseSpec,
    compute_sources_fingerprint,
    ingest_result_from_canonical_dict,
    ingest_result_to_canonical_dict,
    ingest_sources_parallel,
)
from resecta_data.bloom.paranames_specs import _spec_inputs
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json

logger = logging.getLogger(__name__)


_INGEST_CACHE_SUBDIR = Path("gazetteers") / "_ingest_cache"


def _load_cached_ingest(
    cache_path: Path,
    fingerprint: str,
    sources: list[str],
) -> tuple[IngestResult, list[str]] | None:
    """Return the cached ingest iff the fingerprint matches, else ``None``.

    Any malformed cache payload is treated as a miss — the write path will
    overwrite with a fresh build. We deliberately do not raise on malformed
    caches because the file is ephemeral (git-ignored under ``build/``).
    """
    if not cache_path.is_file():
        return None
    try:
        data = load_json(cache_path)
    except PipelineError:
        return None
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint:
        return None
    try:
        ingest = ingest_result_from_canonical_dict(data["ingest"])
    except (PipelineError, KeyError):
        return None
    return ingest, sources


def _write_cached_ingest(
    cache_path: Path,
    fingerprint: str,
    ingest: IngestResult,
) -> None:
    payload = {
        "fingerprint": fingerprint,
        "ingest": ingest_result_to_canonical_dict(ingest),
    }
    dump_canonical_json(payload, cache_path)


def _ingest_with_cache(
    *,
    specs: list[ParseSpec],
    workers: int | None,
    cache_dir: Path | None,
    cache_name: str,
) -> tuple[IngestResult, list[str]]:
    """Parse, merge, and optionally cache an ingest.

    When ``cache_dir`` is provided, the fingerprint of the spec inputs is
    checked against ``cache_dir / cache_name``; on match, the cached result is
    returned and no parsing runs. On miss, the spec is parsed, merged, and
    the resulting payload is written atomically to the cache path before
    returning. The cache file is not a shipped artifact — it lives under an
    ``_ingest_cache/`` directory that ``iter_build_artifacts`` skips, so it
    is outside the hash-lock, schema-check, determinism-diff, and
    install-assets surfaces. The determinism rebuild derives its cache path
    from its own fresh build dir (empty cache ⇒ full re-parse), which is the
    parse-level determinism gate; see tests/test_determinism_cache_isolation.py.
    """
    inputs = _spec_inputs(specs)
    # Sources list is derived purely from the source_ids of wired specs, so
    # we can compute it without parsing — important for cache-hit paths.
    sources = sorted({source_id for _path, source_id in inputs})

    cache_path: Path | None = None
    fingerprint: str | None = None
    if cache_dir is not None and inputs:
        cache_path = cache_dir / cache_name
        fingerprint = compute_sources_fingerprint(inputs)
        hit = _load_cached_ingest(cache_path, fingerprint, sources)
        if hit is not None:
            logger.info("ingest cache hit: %s", cache_path)
            return hit

    # Workers aggregate (dedup) rows before they cross the process boundary;
    # byte-equivalent to merge(parse_sources_parallel(...)) but the live set
    # stays bounded — see ingest_sources_parallel.
    ingest, seen_source_ids = ingest_sources_parallel(specs, workers=workers)
    # Derive sources from actual rows (covers specs whose .args we couldn't
    # introspect); seen_source_ids is already sorted.
    sources = list(seen_source_ids)

    if cache_path is not None and fingerprint is not None:
        _write_cached_ingest(cache_path, fingerprint, ingest)

    return ingest, sources
