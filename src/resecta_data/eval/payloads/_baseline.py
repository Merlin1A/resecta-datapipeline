"""The derived detection baseline (``g8_detection_baseline.json``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict, cast

from ._common import Interval, _require

# ---------------------------------------------------------------------------
# The derived detection baseline (``g8_detection_baseline.json``)
# ---------------------------------------------------------------------------


class ScoredTier(TypedDict):
    """A tier carrying recall AND the Option-C precision (must, should)."""

    total: int
    covered: int
    recall: float
    recall_wilson95: Interval
    precision_option_c: float
    f1_option_c: float
    f2_option_c: float


class WatchTier(TypedDict):
    total: int
    covered: int
    covered_rate: float


class MustNotTier(TypedDict):
    total: int
    fired: int
    fire_rate: float
    fire_rate_wilson95: Interval


class PerTier(TypedDict):
    must: ScoredTier
    should: ScoredTier
    watch: WatchTier
    must_not: MustNotTier


class ClauseCell(TypedDict):
    """The four fields the before/after clauses read off an aggregate cell."""

    precision: float
    recall: float
    precision_with_decoys: float
    low_confidence: bool


class BaselineCell(ClauseCell):
    """One aggregate cell of the derived baseline (per slice, per cell, totals)."""

    true_positives: int
    false_positives: int
    false_negatives: int
    adversarial_suppress_total: int
    adversarial_suppress_fired: int
    suppressed_by_negative_context: int
    support_n: int
    detections_n: int
    precision_wilson95: Interval
    recall_wilson95: Interval
    f1: float
    f2: float
    adversarial_suppression_fp_rate: float
    family_false_positive_count: int
    per_tier: PerTier


class BaselinePayload(TypedDict):
    schema_version: int
    generated_by: str
    metric: str
    source_cells_sha256: str
    per_family: dict[str, BaselineCell]
    per_doctype: dict[str, BaselineCell]
    per_demographic: dict[str, BaselineCell]
    per_cell: dict[str, BaselineCell]
    totals: BaselineCell


def as_baseline_payload(raw: Mapping[str, object]) -> BaselinePayload:
    """A parsed derived baseline under its named shape (``per_family`` + ``totals``)."""
    _require(raw, ("per_family", "totals"))
    return cast("BaselinePayload", raw)


__all__ = [
    "BaselineCell",
    "BaselinePayload",
    "ClauseCell",
    "MustNotTier",
    "PerTier",
    "ScoredTier",
    "WatchTier",
    "as_baseline_payload",
]
