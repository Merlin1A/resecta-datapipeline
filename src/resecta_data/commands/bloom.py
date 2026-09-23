"""The name Bloom-filter build: build bloom, with the ParaNames sharding and the ingest cache it
reads through."""

from __future__ import annotations

import logging
import os
from functools import partial
from pathlib import Path
from typing import Any

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
    parse_census_spanish,
    parse_census_spanish_full,
    parse_census_surnames,
    parse_paranames,
    parse_paranames_full,
    parse_popnames_by_type,
    parse_popnames_fullfile,
    parse_ssa_given_names,
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

# Subdirectory under paranames/ where ``scripts/shard_paranames.py`` lands
# pre-split PER-block shards. When present, we fan the ingest across shards
# to break the single-worker gzip-decompression ceiling inside
# ``parse_paranames_full``. All shards carry the canonical source_id
# ``"paranames_full"`` so ``per_source_counts`` is identical to the
# single-file build.
_PARANAMES_SHARD_SUBDIR = "shards"
_PARANAMES_SHARD_GLOB = "paranames_full_shard_*.tsv.gz"


# Mirrors the Makefile's hydration probe: an LFS pointer file is ~130 B
# text; the real ParaNames corpus is ~954 MB. Size threshold (>1 MB)
# cleanly separates the two.
_PARANAMES_MIN_HYDRATED_BYTES = 1_000_000


def _paranames_full_specs(sources_dir: Path) -> list[ParseSpec]:
    """Return shard specs if a shards dir exists, else the single-file spec.

    Shard files are discovered under
    ``<sources_dir>/paranames/shards/paranames_full_shard_*.tsv.gz``. Order is
    deterministic (lexicographic by filename) so ``parse_sources_parallel``
    produces rows in a stable order across runs and machines.

    Shards are produced offline by ``scripts/shard_paranames.py``; when the
    shards/ directory is absent, the monolithic ``paranames_full.tsv.gz`` is
    used exactly as before. Returns an empty list if neither is present —
    LOUDLY (0.6 guard): the bloom build then ingests only the ~3k bootstrap
    sample, which must never happen silently. Set ``RESECTA_REQUIRE_LFS=1``
    to turn the warning into a hard failure (matches the Makefile guard).

    Raises:
        PipelineError: If the monolithic file is present but pointer-sized
            (unhydrated LFS checkout), or if the full corpus is absent and
            ``RESECTA_REQUIRE_LFS=1``.
    """
    shards_dir = sources_dir / "paranames" / _PARANAMES_SHARD_SUBDIR
    if shards_dir.is_dir():
        shard_paths = sorted(shards_dir.glob(_PARANAMES_SHARD_GLOB))
        if shard_paths:
            return [
                partial(
                    parse_paranames_full,
                    shard_path,
                    "paranames_full",
                    min_languages=5,
                )
                for shard_path in shard_paths
            ]
    full_paranames = sources_dir / "paranames/paranames_full.tsv.gz"
    if full_paranames.is_file():
        if full_paranames.stat().st_size < _PARANAMES_MIN_HYDRATED_BYTES:
            raise PipelineError(
                f"{full_paranames} is pointer-sized "
                f"({full_paranames.stat().st_size} bytes) — an unhydrated "
                "git-lfs checkout. gzip would fail on it mid-ingest. "
                "Hydrate with: git lfs install && git lfs pull"
            )
        return [
            partial(
                parse_paranames_full,
                full_paranames,
                "paranames_full",
                min_languages=5,
            )
        ]
    if os.environ.get("RESECTA_REQUIRE_LFS") == "1":
        raise PipelineError(
            f"ParaNames full corpus not found under {sources_dir / 'paranames'} "
            "and RESECTA_REQUIRE_LFS=1. Hydrate with: git lfs install && "
            "git lfs pull (or scripts/fetch_paranames.sh)."
        )
    logger.warning(
        "ParaNames full corpus not found under %s — the bloom filters will "
        "be built from the ~3k bootstrap sample only. Hydrate with "
        "`git lfs pull` or scripts/fetch_paranames.sh; set "
        "RESECTA_REQUIRE_LFS=1 to make this a hard failure.",
        sources_dir / "paranames",
    )
    return []


def _spec_inputs(specs: list[ParseSpec]) -> list[tuple[Path, str]]:
    """Extract (path, source_id) from each spec for cache fingerprinting.

    All specs in this module are built via ``functools.partial(parser, path,
    source_id, ...)``; we peek at ``.args`` to recover them. A spec that does
    not follow this shape is skipped (the fingerprint is a best-effort key,
    not a proof).
    """
    inputs: list[tuple[Path, str]] = []
    min_spec_positional_args = 2
    for spec in specs:
        args: tuple[Any, ...] = getattr(spec, "args", ())
        if (
            len(args) >= min_spec_positional_args
            and isinstance(args[0], Path)
            and isinstance(args[1], str)
        ):
            inputs.append((args[0], args[1]))
    return inputs


def _surname_ingest_specs(sources_dir: Path) -> list[ParseSpec]:
    """Canonical-order parse specs for the surname filter.

    Order matters: ``merge()`` attributes duplicate keys to the first-seen
    source, so reordering this list changes per-source counts even though
    the final row set is identical. Do not reorder without a compelling
    reason — and update ``tests/test_bloom_determinism.py`` if you do.
    """
    specs: list[ParseSpec] = []
    full_census = sources_dir / "census_surnames/Names_2010Census.csv"
    if full_census.is_file():
        specs.append(partial(parse_census_surnames, full_census, "census_surnames"))
    specs.append(
        partial(
            parse_census_surnames,
            sources_dir / "census_surnames/census_surnames_bootstrap_20260416.csv",
            "census_surnames",
        )
    )
    full_census_spanish = sources_dir / "census_spanish/census_spanish_full_20260419.zip"
    if full_census_spanish.is_file():
        specs.append(partial(parse_census_spanish_full, full_census_spanish, "census_spanish_full"))
    specs.append(
        partial(
            parse_census_spanish,
            sources_dir / "census_spanish/census_spanish_bootstrap_20260416.txt",
            "census_spanish",
        )
    )
    specs.append(
        partial(
            parse_paranames,
            sources_dir / "paranames/paranames_bootstrap_20260416.tsv",
            "paranames",
        )
    )
    specs.extend(_paranames_full_specs(sources_dir))
    full_popnames_surnames = sources_dir / "popnames/common-surnames-by-country.csv"
    if full_popnames_surnames.is_file():
        specs.append(
            partial(
                parse_popnames_fullfile,
                full_popnames_surnames,
                "popnames_common_surnames",
                "surname",
            )
        )
    specs.append(
        partial(
            parse_popnames_by_type,
            sources_dir / "popnames/popnames_bootstrap_20260416.csv",
            "popnames",
            "surname",
        )
    )
    return specs


def _given_name_ingest_specs(sources_dir: Path) -> list[ParseSpec]:
    """Canonical-order parse specs for the given-name filter. See _surname_ingest_specs."""
    specs: list[ParseSpec] = []
    full_ssa = sources_dir / "ssa_given_names/yob2024.txt"
    if full_ssa.is_file():
        specs.append(partial(parse_ssa_given_names, full_ssa, "ssa_given_names"))
    specs.append(
        partial(
            parse_ssa_given_names,
            sources_dir / "ssa_given_names/ssa_bootstrap_20260416.txt",
            "ssa_given_names",
        )
    )
    specs.append(
        partial(
            parse_paranames,
            sources_dir / "paranames/paranames_bootstrap_20260416.tsv",
            "paranames",
        )
    )
    specs.extend(_paranames_full_specs(sources_dir))
    full_popnames_forenames = sources_dir / "popnames/common-forenames-by-country.csv"
    if full_popnames_forenames.is_file():
        specs.append(
            partial(
                parse_popnames_fullfile,
                full_popnames_forenames,
                "popnames_common_forenames",
                "given",
            )
        )
    specs.append(
        partial(
            parse_popnames_by_type,
            sources_dir / "popnames/popnames_bootstrap_20260416.csv",
            "popnames",
            "given",
        )
    )
    return specs


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
