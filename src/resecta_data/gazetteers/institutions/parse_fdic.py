"""Parser for the FDIC institutions CSV export (D3 source).

Consumes the CSV produced by the FDIC institutions API at
``src/resecta_data/gazetteers/institutions/sources/fdic_institutions_YYYYMMDD.csv``
(U.S. federal government data, non-copyrightable under 17 U.S.C. § 105) and
emits ``InstitutionEntry`` rows scoped to ``financial_institution`` per
``schemas/institutions.schema.json``.

The FDIC API export uses these columns (at minimum):
  NAME, CERT, CITY, STNAME, ACTIVE

Only rows where ACTIVE==1 (currently operating institutions) are included.
Jurisdictions are derived from the STNAME (full state name) column using an
explicit mapping to USPS 2-letter codes. Unknown state names cause a loud
failure (fail-fast).

Alias derivation:
  - Short name = name up to the first comma.
  - Strip/normalize business suffixes via normalize.STRIP_SUFFIXES.
  - Keep both pre-strip and post-strip forms as aliases (normalized NFKC lowercase).
  - Never emit an alias equal to the canonical name (lowercased) or empty/whitespace.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError

from .normalize import derive_aliases, nfkc_lower
from .parse_gsa import InstitutionEntry

logger = logging.getLogger(__name__)

_SOURCE_ENCODING: Final[str] = "utf-8-sig"  # BOM-tolerant for CSV exports
_CATEGORY: Final[str] = "financial_institution"

# Minimum required columns in the CSV header.
_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset({"NAME", "CERT", "CITY", "STNAME", "ACTIVE"})

# Explicit STNAME (full state name) → USPS 2-letter code map.
# Covers all 50 states + DC + US territories that may appear in the FDIC export.
# Unknown state names are a hard fail (fail-loud principle).
_STNAME_TO_USPS: Final[dict[str, str]] = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    # US territories that may appear in FDIC data
    "American Samoa": "AS",
    "Guam": "GU",
    "Northern Mariana Islands": "MP",
    "Puerto Rico": "PR",
    "Virgin Islands": "VI",
}


def parse_fdic_institutions(path: Path) -> list[InstitutionEntry]:
    """Parse the FDIC institutions CSV into financial-institution entries.

    Only ACTIVE==1 rows are included. Entries are sorted by name for
    determinism.

    Args:
        path: CSV path. Must exist; source is immutable.

    Returns:
        Entries in deterministic order, sorted by name. Each entry has
        ``category="financial_institution"``.

    Raises:
        MissingSourceError: If ``path`` does not exist.
        PipelineError: If the CSV header is missing required columns, a
            STNAME value has no USPS mapping, or the file is otherwise
            malformed.
    """
    if not path.is_file():
        raise MissingSourceError(
            f"FDIC institutions CSV not found at {path}. "
            "Run the fetch_fdic_banks.sh script on Linux to fetch, then copy to sources/."
        )

    with path.open("r", encoding=_SOURCE_ENCODING, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = _REQUIRED_COLUMNS - set(header)
        if missing:
            raise PipelineError(
                f"{path}: missing required columns {sorted(missing)}; header was {header!r}"
            )
        rows = list(reader)

    entries: dict[str, InstitutionEntry] = {}
    skipped_inactive = 0

    for row in rows:
        # Filter to active institutions only.
        active_raw = (row.get("ACTIVE") or "").strip()
        try:
            active = int(active_raw)
        except ValueError:
            active = 0
        if active != 1:
            skipped_inactive += 1
            continue

        raw_name = (row.get("NAME") or "").strip()
        if not raw_name:
            continue

        stname = (row.get("STNAME") or "").strip()
        usps = _STNAME_TO_USPS.get(stname)
        if usps is None:
            raise PipelineError(
                f"{path}: unknown state name {stname!r} in row NAME={raw_name!r}. "
                "Add to _STNAME_TO_USPS in parse_fdic.py or check the source data."
            )

        canonical = raw_name
        canonical_lower = nfkc_lower(canonical)
        aliases_lower = derive_aliases(raw_name)

        # Remove any alias that equals the canonical name (lowercased).
        aliases_filtered = [a for a in aliases_lower if a != canonical_lower and a.strip()]

        # Deduplicate: first occurrence of a canonical name wins.
        if canonical in entries:
            continue

        entries[canonical] = InstitutionEntry(
            name=canonical,
            aliases=tuple(sorted(set(aliases_filtered))),
            category=_CATEGORY,
            jurisdictions=(usps,),
        )

    if skipped_inactive:
        logger.info("parse_fdic: skipped %d inactive institutions (ACTIVE != 1)", skipped_inactive)

    result = sorted(entries.values(), key=lambda e: e.name)
    logger.info(
        "parse_fdic: parsed %d active financial_institution entries from %s", len(result), path.name
    )
    return result
