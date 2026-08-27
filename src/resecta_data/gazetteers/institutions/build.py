"""Aggregate institution gazetteer entries into the shipped artifact.

Phase 2 emits a single category — ``federal_agency`` — sourced from the
Federal Register agencies API feed, following a source-parser rebuild. The
category enum in the schema
leaves room for ``federal_court``, ``state_official``, and ``local_clerk``
in later phases once the NAAG / USCOURTS / CDC / SSA license questions clear.

Two OPTIONAL sources supplement the mandatory feed:
- FDIC institutions CSV (fdic_institutions_YYYYMMDD.csv) → ``financial_institution``
- SEC EDGAR company_tickers JSON (edgar_company_tickers_YYYYMMDD.json) →
  ``financial_institution`` or ``employer``

Both new sources are OPTIONAL per design Risk 4 (partial-source clause): if the
dated source file is absent, the build proceeds with only the available sources.
More than one dated match for a source family raises (immutable provenance means
re-fetches must not silently shadow). The retrieval_date for each present source
is derived from the YYYYMMDD suffix in the dated filename.

Merge priority: FedReg entries win on name collisions (FedReg first, then FDIC,
then EDGAR). Entries are sorted by name for determinism.

The legacy GSA Federal Hierarchy Crosswalk reader (``parse_gsa``) is retained
and read at build time only to compute ``institutions.cutover-diff.json``,
the advisory diff artifact emitted alongside the rebuild artifact. The
crosswalk is not folded into the rebuild's ``sources`` provenance — the
diff is informational and the wire format is stable.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import sha256_file

from .parse_edgar import parse_edgar_companies
from .parse_fdic import parse_fdic_institutions
from .parse_federalregister import parse_federalregister_agencies
from .parse_gsa import InstitutionEntry, parse_gsa_agencies

logger = logging.getLogger(__name__)

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/institutions"
_SCHEMA_VERSION: Final[int] = 1
_CUTOVER_DIFF_VERSION: Final[int] = 1
_CUTOVER_ARTIFACT: Final[str] = "gazetteers/institutions.json"

_SOURCE_DIR: Final[Path] = Path(__file__).resolve().parent / "sources"
_FR_SOURCE: Final[Path] = _SOURCE_DIR / "federalregister_agencies.json"
_FR_RETRIEVAL_DATE: Final[str] = "2026-04-27"
_FR_SOURCE_ID: Final[str] = "federalregister_agencies"
_GSA_LEGACY_SOURCE: Final[Path] = _SOURCE_DIR / "gsa_federal_agencies_20260419.csv"

# Glob patterns for optional dated source files.
_FDIC_GLOB: Final[str] = "fdic_institutions_*.csv"
_EDGAR_GLOB: Final[str] = "edgar_company_tickers_*.json"

# Regex to extract YYYYMMDD from dated filenames like fdic_institutions_20260611.csv.
_DATED_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(r"_(\d{8})\.[^.]+$")


def _find_optional_source(source_dir: Path, glob: str, source_label: str) -> Path | None:
    """Locate a single optional dated source file.

    Args:
        source_dir: Directory to search.
        glob: Glob pattern (e.g. "fdic_institutions_*.csv").
        source_label: Human-readable label for error messages.

    Returns:
        Path to the found file, or None if absent.

    Raises:
        PipelineError: If more than one match is found (immutable provenance
            means re-fetches must not silently shadow).
    """
    matches = sorted(source_dir.glob(glob))
    if not matches:
        return None
    if len(matches) > 1:
        raise PipelineError(
            f"Multiple {source_label} source files found in {source_dir}: "
            f"{[m.name for m in matches]}. "
            "Remove all but the intended version — re-fetches must not silently shadow."
        )
    return matches[0]


def _retrieval_date_from_path(path: Path) -> str:
    """Extract the retrieval date from a dated filename's YYYYMMDD suffix.

    Args:
        path: Path whose stem ends with _YYYYMMDD.

    Returns:
        ISO date string "YYYY-MM-DD".

    Raises:
        PipelineError: If no YYYYMMDD suffix is found in the filename.
    """
    m = _DATED_SUFFIX_RE.search(path.name)
    if m is None:
        raise PipelineError(
            f"Cannot extract retrieval date from {path.name}. "
            "Expected filename suffix _YYYYMMDD (e.g., fdic_institutions_20260611.csv)."
        )
    raw = m.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def build(
    seed: int,
    fdic_source: Path | None = None,
    edgar_source: Path | None = None,
) -> dict[str, Any]:
    """Return the institutions payload built from all available sources.

    FedReg entries are always included. FDIC and EDGAR sources are optional
    (absent → omitted from output ``sources`` provenance). On name collisions
    FedReg entries win, then FDIC, then EDGAR.

    Args:
        seed: PRNG seed. Recorded for reproducibility; aggregation is
            deterministic without randomness.
        fdic_source: Explicit path to the FDIC CSV. When None (default), the
            builder searches ``_SOURCE_DIR`` using the glob ``fdic_institutions_*.csv``.
        edgar_source: Explicit path to the EDGAR JSON. When None (default), the
            builder searches ``_SOURCE_DIR`` using the glob
            ``edgar_company_tickers_*.json``.

    Returns:
        A payload dict conforming to ``schemas/institutions.schema.json``.

    Raises:
        PipelineError: If more than one dated file is found for a given source
            family, or if a located source file cannot be parsed.
    """
    # --- Federal Register (mandatory) ---
    fr_entries = parse_federalregister_agencies(_FR_SOURCE)
    sources: list[dict[str, str]] = [
        {
            "id": _FR_SOURCE_ID,
            "sha256": sha256_file(_FR_SOURCE),
            "retrieval_date": _FR_RETRIEVAL_DATE,
        }
    ]

    # Accumulated entries dict — FedReg has priority, so seed it first.
    # Dedup key is the canonical name.
    all_entries: dict[str, InstitutionEntry] = {e.name: e for e in fr_entries}

    # --- FDIC (optional) ---
    resolved_fdic = (
        fdic_source
        if fdic_source is not None
        else _find_optional_source(_SOURCE_DIR, _FDIC_GLOB, "FDIC institutions")
    )
    if resolved_fdic is not None:
        retrieval_date = _retrieval_date_from_path(resolved_fdic)
        fdic_entries = parse_fdic_institutions(resolved_fdic)
        sources.append(
            {
                "id": "fdic_institutions",
                "sha256": sha256_file(resolved_fdic),
                "retrieval_date": retrieval_date,
            }
        )
        added = 0
        for entry in fdic_entries:
            if entry.name not in all_entries:
                all_entries[entry.name] = entry
                added += 1
        logger.info("build: FDIC source added %d new entries (collisions skipped)", added)
    else:
        logger.info("build: no FDIC source found in %s — omitting from output", _SOURCE_DIR)

    # --- EDGAR (optional) ---
    resolved_edgar = (
        edgar_source
        if edgar_source is not None
        else _find_optional_source(_SOURCE_DIR, _EDGAR_GLOB, "EDGAR company tickers")
    )
    if resolved_edgar is not None:
        retrieval_date = _retrieval_date_from_path(resolved_edgar)
        edgar_entries = parse_edgar_companies(resolved_edgar)
        sources.append(
            {
                "id": "edgar_company_tickers",
                "sha256": sha256_file(resolved_edgar),
                "retrieval_date": retrieval_date,
            }
        )
        added = 0
        for entry in edgar_entries:
            if entry.name not in all_entries:
                all_entries[entry.name] = entry
                added += 1
        logger.info("build: EDGAR source added %d new entries (collisions skipped)", added)
    else:
        logger.info("build: no EDGAR source found in %s — omitting from output", _SOURCE_DIR)

    # Sort entries by name for determinism.
    sorted_entries = sorted(all_entries.values(), key=lambda e: e.name)

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "sources": sources,
        "entries": [entry.to_dict() for entry in sorted_entries],
    }


def build_cutover_diff() -> dict[str, Any]:
    """Return the legacy→rebuild cutover diff for ``institutions.json``.

    Reads both the legacy GSA Federal Hierarchy Crosswalk and the new
    Federal Register feed, runs each through its parser, and emits a
    schema-validated diff payload covering keys present only in the legacy
    output, keys present only in the rebuild output, and keys present in
    both with field-level differences.

    The diff is advisory — used for
    PR-review context, not as a release gate.
    """
    legacy_entries = {e.name: e.to_dict() for e in parse_gsa_agencies(_GSA_LEGACY_SOURCE)}
    rebuild_entries = {e.name: e.to_dict() for e in parse_federalregister_agencies(_FR_SOURCE)}

    legacy_only = sorted(legacy_entries.keys() - rebuild_entries.keys())
    rebuild_only = sorted(rebuild_entries.keys() - legacy_entries.keys())
    keyed_diff: list[dict[str, Any]] = []
    for name in sorted(legacy_entries.keys() & rebuild_entries.keys()):
        legacy = legacy_entries[name]
        rebuild = rebuild_entries[name]
        if legacy != rebuild:
            keyed_diff.append({"key": name, "legacy": legacy, "rebuild": rebuild})

    return {
        "version": _CUTOVER_DIFF_VERSION,
        "generated_by": _GENERATED_BY,
        "artifact": _CUTOVER_ARTIFACT,
        "summary": {
            "legacy_only_count": len(legacy_only),
            "rebuild_only_count": len(rebuild_only),
            "keyed_diff_count": len(keyed_diff),
        },
        "legacy_only": legacy_only,
        "rebuild_only": rebuild_only,
        "keyed_diff": keyed_diff,
    }
