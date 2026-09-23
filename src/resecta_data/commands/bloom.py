"""The name Bloom-filter build: build bloom, with the ParaNames sharding and the ingest cache it
reads through."""

from __future__ import annotations

from pathlib import Path

import click

from resecta_data.bloom import (
    NAME_FILTERS_CUTOVER,
    BloomFilter,
    FilterBuildResult,
    build_manifest,
    optimal_bits,
)
from resecta_data.bloom.corpus_ingest import IngestResult
from resecta_data.bloom.ingest_cache import _INGEST_CACHE_SUBDIR, _ingest_with_cache
from resecta_data.bloom.paranames_specs import _given_name_ingest_specs, _surname_ingest_specs
from resecta_data.bloom.spec import (
    FPR_TARGET,
    GIVEN_NAME_FILTER_FILE,
    K_HASHES,
    MANIFEST_FILE,
    SURNAME_FILTER_FILE,
)
from resecta_data.common.cutover import build_cutover_diff
from resecta_data.common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
from resecta_data.common.io import atomic_write_bytes, dump_canonical_json

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
