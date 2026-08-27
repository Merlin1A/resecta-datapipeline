"""Parser for the HUD USPS ZIP-to-state crosswalk CSV.

The HUD crosswalk ships with a stable header row. We read the CSV by column
name rather than by position so a column reorder in a future release does not
silently mis-parse. Only the columns we need are retained; every other
column is dropped.

Columns we rely on:

    ZIP      — 5-digit ZIP string, leading zeros preserved
    STATE    — two-letter USPS state code
    RES_RATIO — fraction of residential deliveries in this ZIP that fall in
                the named state (used as the tie-breaker for split ZIPs)

Rows that do not match these contracts are rejected loudly — a silent skip
would let a malformed source degrade coverage without surfacing the problem.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError

_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset({"ZIP", "STATE", "RES_RATIO"})
_ZIP_LENGTH: Final[int] = 5
_STATE_LENGTH: Final[int] = 2


@dataclass(frozen=True, slots=True)
class CrosswalkRow:
    """One parsed row of the HUD crosswalk.

    Attributes:
        zip5: Five-character ZIP string.
        state: Two-letter uppercase state code.
        res_ratio: Residential-delivery ratio in [0, 1].
    """

    zip5: str
    state: str
    res_ratio: float


def parse(csv_path: Path) -> list[CrosswalkRow]:
    """Parse the HUD crosswalk at ``csv_path``.

    Args:
        csv_path: File to read. Must exist.

    Returns:
        A list of rows in source order. Order is preserved so downstream
        sort steps can produce deterministic output.

    Raises:
        MissingSourceError: If ``csv_path`` does not exist.
        PipelineError: If the header is wrong or any row fails validation.
    """
    if not csv_path.is_file():
        raise MissingSourceError(
            f"HUD ZIP crosswalk not found at {csv_path}. Run `make sources` to fetch."
        )

    rows: list[CrosswalkRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = _REQUIRED_COLUMNS - set(header)
        if missing:
            raise PipelineError(
                f"{csv_path}: missing required columns {sorted(missing)}; header was {header!r}"
            )

        for lineno, raw in enumerate(reader, start=2):
            rows.append(_parse_row(raw, csv_path, lineno))

    if not rows:
        raise PipelineError(f"{csv_path}: parsed zero rows")

    return rows


def _parse_row(raw: dict[str, str], csv_path: Path, lineno: int) -> CrosswalkRow:
    """Validate and coerce one CSV row. Raises ``PipelineError`` on bad data."""
    zip5 = (raw.get("ZIP") or "").strip()
    state = (raw.get("STATE") or "").strip().upper()
    ratio_str = (raw.get("RES_RATIO") or "").strip()

    if len(zip5) != _ZIP_LENGTH or not zip5.isdigit():
        raise PipelineError(f"{csv_path}:{lineno}: expected 5-digit ZIP, got {zip5!r}")
    if len(state) != _STATE_LENGTH or not state.isalpha():
        raise PipelineError(f"{csv_path}:{lineno}: expected 2-letter state, got {state!r}")
    try:
        ratio = float(ratio_str)
    except ValueError as exc:
        raise PipelineError(f"{csv_path}:{lineno}: RES_RATIO {ratio_str!r} is not a float") from exc
    if not 0.0 <= ratio <= 1.0:
        raise PipelineError(f"{csv_path}:{lineno}: RES_RATIO {ratio} outside [0, 1]")

    return CrosswalkRow(zip5=zip5, state=state, res_ratio=ratio)
