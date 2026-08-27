"""Parser for the Census 2020 ZCTA-to-Tract Relationship File.

This file is the substitute data source for the HUD USPS ZIP crosswalk.
The HUD portal
requires an account and session-cookie dance that an offline build cannot
perform; the Census relationship file is a direct-download federal work
(17 U.S.C. §105) that provides equivalent ZIP→state coverage via the ZCTA
and tract geographies.

File format: 17-column pipe-delimited UTF-8-with-BOM. Relevant columns:

    GEOID_ZCTA5_20   5-digit ZCTA (ZIP Code Tabulation Area) as a string
    GEOID_TRACT_20   11-digit census tract GEOID; first 2 digits are the
                     state FIPS code
    AREALAND_PART    Land area (square meters) of the intersection between
                     this ZCTA and this tract — used as a residential-density
                     proxy when a ZCTA spans more than one state

Tract-only rows (with empty ZCTA columns 1-8) also appear in the file and
describe tracts that have no associated ZCTA. Those rows are skipped.

Emission: one ``CrosswalkRow`` per (zcta5, state) pair with ``res_ratio`` set
to the land-area share of that state for the ZCTA. Downstream
``build.py::_select_winning_state`` already uses ``res_ratio`` as the
tie-break signal, so the shape remains identical to the HUD path.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError

from .parser import CrosswalkRow

_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {"GEOID_ZCTA5_20", "GEOID_TRACT_20", "AREALAND_PART"}
)
_ZCTA_LENGTH: Final[int] = 5
_TRACT_GEOID_LENGTH: Final[int] = 11
_STATE_FIPS_LENGTH: Final[int] = 2

# FIPS state/territory numeric codes to USPS two-letter codes. Covers the 50
# states, DC, and the five populated Island Areas that appear in the Census
# relationship file (AS, GU, MP, PR, VI). Outlying uninhabited territories
# are not emitted in ZCTA-tract geography and intentionally omitted.
_FIPS_TO_USPS: Final[dict[str, str]] = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
    "60": "AS",
    "66": "GU",
    "69": "MP",
    "72": "PR",
    "78": "VI",
}


def _parse_zcta_tract_row(
    raw: dict[str, str],
    path: Path,
    lineno: int,
) -> tuple[str, str, int] | None:
    """Validate and decode one ZCTA-tract row.

    Returns ``(zcta5, state, land_area)`` for ZCTA-bearing rows, or ``None``
    for tract-only rows (columns 1-8 empty) which are skipped upstream.
    """
    zcta5 = (raw.get("GEOID_ZCTA5_20") or "").strip()
    if not zcta5:
        return None
    tract_geoid = (raw.get("GEOID_TRACT_20") or "").strip()
    area_str = (raw.get("AREALAND_PART") or "").strip()

    if len(zcta5) != _ZCTA_LENGTH or not zcta5.isdigit():
        raise PipelineError(f"{path}:{lineno}: expected 5-digit ZCTA, got {zcta5!r}")
    if len(tract_geoid) != _TRACT_GEOID_LENGTH or not tract_geoid.isdigit():
        raise PipelineError(f"{path}:{lineno}: expected 11-digit tract GEOID, got {tract_geoid!r}")

    state_fips = tract_geoid[:_STATE_FIPS_LENGTH]
    state = _FIPS_TO_USPS.get(state_fips)
    if state is None:
        raise PipelineError(
            f"{path}:{lineno}: unknown state FIPS code {state_fips!r} "
            f"in tract GEOID {tract_geoid!r}"
        )

    try:
        area = int(area_str) if area_str else 0
    except ValueError as exc:
        raise PipelineError(
            f"{path}:{lineno}: AREALAND_PART {area_str!r} is not an integer"
        ) from exc
    if area < 0:
        raise PipelineError(f"{path}:{lineno}: AREALAND_PART {area} is negative")

    return zcta5, state, area


def parse_census_zcta_tract(path: Path) -> list[CrosswalkRow]:
    """Parse the Census 2020 ZCTA-to-Tract Relationship File.

    Args:
        path: File path. Must exist. UTF-8 with BOM, pipe-delimited, 17 cols.

    Returns:
        Rows keyed by (zcta5, state) with ``res_ratio`` equal to the fraction
        of this ZCTA's total land area that falls inside the given state. A
        ZCTA fully contained in one state emits a single row with ratio 1.0;
        a ZCTA spanning two states emits two rows whose ratios sum to 1.0.

        Order is deterministic: sorted by (zcta5, state). Downstream
        ``build.py`` takes the highest-ratio state per ZIP (with alphabetic
        tie-break) which matches the HUD ``RES_RATIO`` semantics.

    Raises:
        MissingSourceError: If ``path`` does not exist.
        PipelineError: If the header is wrong or the file parses empty.
    """
    if not path.is_file():
        raise MissingSourceError(
            f"Census ZCTA-to-Tract relationship file not found at {path}. "
            "Run `make sources` to fetch."
        )

    # Land-area accumulator: {zcta5 -> {state -> total_area}}.
    area_by_zcta_state: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="|")
        header = reader.fieldnames or []
        missing = _REQUIRED_COLUMNS - set(header)
        if missing:
            raise PipelineError(
                f"{path}: missing required columns {sorted(missing)}; header was {header!r}"
            )

        for lineno, raw in enumerate(reader, start=2):
            parsed = _parse_zcta_tract_row(raw, path, lineno)
            if parsed is None:
                continue
            zcta5, state, area = parsed
            area_by_zcta_state[zcta5][state] += area

    rows: list[CrosswalkRow] = []
    for zcta5 in sorted(area_by_zcta_state):
        states = area_by_zcta_state[zcta5]
        total_area = sum(states.values())
        for state in sorted(states):
            # Zero-land ZCTAs (water-only fragments) still need a state; fall
            # back to equal weight across states so alphabetic tie-break in
            # build.py keeps the mapping deterministic.
            ratio = states[state] / total_area if total_area > 0 else 1.0 / len(states)
            rows.append(CrosswalkRow(zip5=zcta5, state=state, res_ratio=ratio))

    if not rows:
        raise PipelineError(f"{path}: parsed zero rows")

    return rows
