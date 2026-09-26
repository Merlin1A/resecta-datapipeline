"""The ingest specs for the name corpora: the ParaNames shard fan-out and the per-source parse
specs the surname and given-name Bloom filters are built from."""

from __future__ import annotations

import logging
import os
from functools import partial
from pathlib import Path
from typing import Any

from resecta_data.bloom.corpus_ingest import (
    ParseSpec,
    parse_census_spanish,
    parse_census_spanish_full,
    parse_census_surnames,
    parse_paranames,
    parse_paranames_full,
    parse_popnames_by_type,
    parse_popnames_fullfile,
    parse_ssa_given_names,
)
from resecta_data.common.exceptions import PipelineError

logger = logging.getLogger(__name__)


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
