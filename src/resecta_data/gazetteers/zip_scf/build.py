"""Build the ZIP → SCF → state table from a parsed crosswalk.

Two-phase derivation:

  1. For each 5-digit ZIP, select the "winning" row by highest RES_RATIO —
     this is the state that the majority of residential addresses in that
     ZIP fall under. Ties (rare) break alphabetically by state code for
     determinism.
  2. Group winners by 3-digit SCF prefix (first three characters of the
     ZIP). If every ZIP under a prefix resolves to the same state, emit
     only the scf_table row. Otherwise, emit the dominant state in
     scf_table and record the minority ZIPs in overrides.

Two source formats are accepted and auto-detected by header sniff:

  * The HUD USPS ZIP-to-state crosswalk (comma-delimited CSV with
    ``ZIP,STATE,RES_RATIO``). Historical bootstrap path.
  * The Census 2020 ZCTA-to-Tract Relationship File (pipe-delimited
    UTF-8-with-BOM with ``GEOID_ZCTA5_20|GEOID_TRACT_20|…``). This is the
    substitute for the HUD crosswalk; ZIP→state derives from the
    tract GEOID's FIPS prefix, with AREALAND_PART as the tie-break weight.

Both parsers return ``list[CrosswalkRow]``, so the Phase 1/2 derivation
below is agnostic to the input format.

The payload is rendered via ``dump_canonical_json`` — sorted keys, LF, no
insertion-order dependency.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import sha256_file

from .parse_census_zcta import parse_census_zcta_tract
from .parser import CrosswalkRow
from .parser import parse as parse_hud

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/zip_scf"
_SCHEMA_VERSION: Final[int] = 1

# First-line signatures for the two accepted source formats. The Census file
# ships UTF-8-with-BOM, so we strip the BOM before comparing.
_CENSUS_ZCTA_HEADER_TOKEN: Final[str] = "GEOID_ZCTA5_20"  # noqa: S105
_HUD_HEADER_TOKEN: Final[str] = "ZIP"  # noqa: S105
_UTF8_BOM: Final[str] = "\ufeff"


def _select_winning_state(rows: list[CrosswalkRow]) -> str:
    """Return the state with the highest RES_RATIO among rows for a single ZIP.

    Tie-break by state code alphabetically for determinism.
    """
    # Sort by (-res_ratio, state) so the first row is the winner.
    ranked = sorted(rows, key=lambda r: (-r.res_ratio, r.state))
    return ranked[0].state


def _parse_source(source: Path) -> list[CrosswalkRow]:
    """Dispatch to the Census ZCTA or HUD parser by header sniff.

    Auto-detection keeps the CLI a single ``--source`` flag while letting
    the full Census ZCTA file supersede the bootstrap HUD CSV when present.
    """
    with source.open("r", encoding="utf-8-sig", newline="") as fh:
        first_line = fh.readline()
    first_line = first_line.lstrip(_UTF8_BOM).rstrip("\r\n")
    if _CENSUS_ZCTA_HEADER_TOKEN in first_line and "|" in first_line:
        return parse_census_zcta_tract(source)
    if first_line.startswith(_HUD_HEADER_TOKEN) and "," in first_line:
        return parse_hud(source)
    raise PipelineError(
        f"{source}: unrecognized ZIP crosswalk format; expected a HUD CSV "
        f"header starting with {_HUD_HEADER_TOKEN!r} or a Census "
        f"ZCTA-to-Tract header containing {_CENSUS_ZCTA_HEADER_TOKEN!r}, "
        f"got {first_line!r}"
    )


def build(
    seed: int,
    *,
    source: Path,
    retrieval_date: str,
) -> dict[str, Any]:
    """Return the ZIP-SCF-state payload.

    Args:
        seed: PRNG seed. Recorded for reproducibility; derivation is
            deterministic without randomness.
        source: Path to either the HUD crosswalk CSV or the Census
            ZCTA-to-Tract Relationship File. Must exist.
        retrieval_date: ISO date (``YYYY-MM-DD``) the source was fetched.

    Returns:
        A payload dict conforming to ``schemas/zip_scf_states.schema.json``.
    """
    rows = _parse_source(source)

    # Phase 1: collapse duplicates per ZIP by highest residential ratio.
    zip_rows: dict[str, list[CrosswalkRow]] = defaultdict(list)
    for row in rows:
        zip_rows[row.zip5].append(row)

    zip_to_state: dict[str, str] = {
        zip5: _select_winning_state(group) for zip5, group in zip_rows.items()
    }

    # Phase 2: group by SCF prefix, choose dominant state, record minorities.
    scf_members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for zip5, state in zip_to_state.items():
        scf_members[zip5[:3]].append((zip5, state))

    scf_table: dict[str, str] = {}
    overrides: dict[str, str] = {}

    for scf_prefix, members in scf_members.items():
        state_counts = Counter(state for _, state in members)
        # Dominant state: highest count, ties broken alphabetically.
        dominant_state = sorted(state_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        scf_table[scf_prefix] = dominant_state
        for zip5, state in members:
            if state != dominant_state:
                overrides[zip5] = state

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "source_sha256": sha256_file(source),
        "source_retrieval_date": retrieval_date,
        "zip_key_type": "string",
        "scf_table": dict(sorted(scf_table.items())),
        "overrides": dict(sorted(overrides.items())),
    }
