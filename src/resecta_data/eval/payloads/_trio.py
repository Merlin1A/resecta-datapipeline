"""The Swift G8 trio's inputs: raw join cells and raw match scores."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NotRequired, TypedDict, cast

from ._common import _require

# ---------------------------------------------------------------------------
# The Swift G8 trio: raw join cells (``_cells.json``, CONTRACT.md File 1)
# ---------------------------------------------------------------------------


class RawCell(TypedDict):
    """One ``(category, doctype, bucket)`` join cell's raw counters."""

    true_positives: int
    false_negatives: int
    false_positives: int
    adversarial_suppress_total: int
    adversarial_suppress_fired: int
    suppressed_by_negative_context: int
    # The packet-tier counters (1.2 P1.10); absent from a pre-extension file.
    tier_must_total: NotRequired[int]
    tier_must_covered: NotRequired[int]
    tier_should_total: NotRequired[int]
    tier_should_covered: NotRequired[int]
    tier_watch_total: NotRequired[int]
    tier_watch_covered: NotRequired[int]
    tier_must_not_total: NotRequired[int]
    tier_must_not_fired: NotRequired[int]


class CellsPayload(TypedDict):
    """``_cells.json``: the cells map keyed ``<category>_<doctype>_<bucket>``."""

    cells: dict[str, RawCell]
    site: NotRequired[str]


def as_cells_payload(raw: Mapping[str, object]) -> CellsPayload:
    """The parsed ``_cells.json`` under its named shape (``cells`` required)."""
    _require(raw, ("cells",))
    return cast("CellsPayload", raw)


# ---------------------------------------------------------------------------
# The raw match scores (``_raw_scores.json``, CONTRACT.md File 2)
# ---------------------------------------------------------------------------


class RawScoreRow(TypedDict):
    category: str
    raw: float
    gt_class: str
    doctype: NotRequired[str]
    bucket: NotRequired[str]


class RawScoresPayload(TypedDict):
    rows: list[RawScoreRow]
    absorbing_state_floor: float
    balanced_cutoffs: NotRequired[dict[str, float | None]]


def as_raw_scores_payload(raw: Mapping[str, object]) -> RawScoresPayload:
    """The parsed ``_raw_scores.json`` (``rows`` + ``absorbing_state_floor`` required)."""
    _require(raw, ("rows", "absorbing_state_floor"))
    return cast("RawScoresPayload", raw)


__all__ = [
    "CellsPayload",
    "RawCell",
    "RawScoreRow",
    "RawScoresPayload",
    "as_cells_payload",
    "as_raw_scores_payload",
]
