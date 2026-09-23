"""The name Bloom-filter build: build bloom, with the ParaNames sharding and the ingest cache it
reads through."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from resecta_data.bloom import (
    NAME_FILTERS_CUTOVER,
    BloomFilter,
    FilterBuildResult,
    build_manifest,
    optimal_bits,
)
from resecta_data.bloom.corpus_ingest import (
    IngestResult,
    ParseSpec,
    compute_sources_fingerprint,
    ingest_result_from_canonical_dict,
    ingest_result_to_canonical_dict,
    ingest_sources_parallel,
)
from resecta_data.bloom.paranames_specs import (
    _given_name_ingest_specs,
    _spec_inputs,
    _surname_ingest_specs,
)
from resecta_data.bloom.spec import (
    FPR_TARGET,
    GIVEN_NAME_FILTER_FILE,
    K_HASHES,
    MANIFEST_FILE,
    SURNAME_FILTER_FILE,
)
from resecta_data.common.cutover import build_cutover_diff
from resecta_data.common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import atomic_write_bytes, dump_canonical_json, load_json

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Build subcommands (Phase 2)
# -----------------------------------------------------------------------------
#
# Deterministic build timestamp. Wall-clock content is banned from
# artifact payloads; the manifest builtAt field is included in the hash lock,
# so we pin it to the canonical build date rather than call datetime.now().
_BUILD_YEAR = CANONICAL_SEED // 10000
_BUILD_MONTH = (CANONICAL_SEED // 100) % 100
_BUILD_DAY = CANONICAL_SEED % 100
_DEFAULT_BUILD_DATE = f"{_BUILD_YEAR:04d}-{_BUILD_MONTH:02d}-{_BUILD_DAY:02d}T00:00:00Z"

_DEFAULT_SOURCES_DIR = Path("src/resecta_data/gazetteers/sources")


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


def _ingest_surnames(
    sources_dir: Path,
    *,
    workers: int | None = None,
    cache_dir: Path | None = None,
) -> tuple[IngestResult, list[str]]:
    return _ingest_with_cache(
        specs=_surname_ingest_specs(sources_dir),
        workers=workers,
        cache_dir=cache_dir,
        cache_name="surnames.json",
    )


def _ingest_given_names(
    sources_dir: Path,
    *,
    workers: int | None = None,
    cache_dir: Path | None = None,
) -> tuple[IngestResult, list[str]]:
    return _ingest_with_cache(
        specs=_given_name_ingest_specs(sources_dir),
        workers=workers,
        cache_dir=cache_dir,
        cache_name="given-names.json",
    )


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


def _build_one_filter(ingest: IngestResult, seed: int) -> tuple[BloomFilter, int]:
    m = max(optimal_bits(len(ingest.rows), FPR_TARGET), 8)
    bf = BloomFilter.build_parallel(m=m, k=K_HASHES, seed=seed, keys=ingest.rows)
    return bf, m


@click.command("bloom")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--sources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=_DEFAULT_SOURCES_DIR,
    show_default=True,
    help="Directory containing the name-corpus source subdirectories.",
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
@click.option(
    "--build-date",
    default=_DEFAULT_BUILD_DATE,
    show_default=True,
    help="ISO-8601 UTC timestamp recorded in the manifest. Pinned for determinism.",
)
def build_bloom_cmd(build_dir: Path, sources_dir: Path, seed: int, build_date: str) -> None:
    """Build surnames.bloom + given-names.bloom + gazetteer_manifest.json."""
    assert_hash_seed_pinned()

    cache_dir = build_dir / _INGEST_CACHE_SUBDIR
    surname_ingest, surname_sources = _ingest_surnames(sources_dir, cache_dir=cache_dir)
    given_ingest, given_sources = _ingest_given_names(sources_dir, cache_dir=cache_dir)

    surname_bf, surname_m = _build_one_filter(surname_ingest, seed)
    given_bf, given_m = _build_one_filter(given_ingest, seed)

    surname_bytes = surname_bf.serialize(surname_ingest.source_hash)
    given_bytes = given_bf.serialize(given_ingest.source_hash)

    atomic_write_bytes(build_dir / "gazetteers" / SURNAME_FILTER_FILE, surname_bytes)
    atomic_write_bytes(build_dir / "gazetteers" / GIVEN_NAME_FILTER_FILE, given_bytes)

    filters = [
        FilterBuildResult(
            name="surnames",
            type_="surname",
            n=len(surname_ingest.rows),
            m=surname_m,
            k=K_HASHES,
            fpr_target=FPR_TARGET,
            sources=tuple(surname_sources),
        ),
        FilterBuildResult(
            name="given-names",
            type_="givenName",
            n=len(given_ingest.rows),
            m=given_m,
            k=K_HASHES,
            fpr_target=FPR_TARGET,
            sources=tuple(given_sources),
        ),
    ]
    manifest = build_manifest(filters, seed=seed, built_at=build_date)
    dump_canonical_json(manifest, build_dir / "gazetteers" / MANIFEST_FILE)

    cutover_diff = build_cutover_diff((), (), spec=NAME_FILTERS_CUTOVER)
    cutover_dest = build_dir / "gazetteers" / "name_filters.cutover-diff.json"
    dump_canonical_json(cutover_diff, cutover_dest)
    summary = cutover_diff["summary"]

    click.echo(
        f"Wrote {SURNAME_FILTER_FILE} ({len(surname_ingest.rows)} keys, m={surname_m}), "
        f"{GIVEN_NAME_FILTER_FILE} ({len(given_ingest.rows)} keys, m={given_m}), {MANIFEST_FILE}; "
        f"{cutover_dest.name} (legacy_only={summary['legacy_only_count']}, "
        f"rebuild_only={summary['rebuild_only_count']}, "
        f"keyed_diff={summary['keyed_diff_count']})"
    )


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_bloom_cmd)
