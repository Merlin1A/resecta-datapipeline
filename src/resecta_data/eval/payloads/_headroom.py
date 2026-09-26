"""The learned-term headroom probe (``g8_headroom.json``)."""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# The learned-term headroom probe (``g8_headroom.json``)
# ---------------------------------------------------------------------------


class ScoreSummary(TypedDict):
    count: int
    min: float | None
    mean: float | None
    max: float | None


class PosteriorSummary(TypedDict):
    floor: float
    cutoff_posterior: float | None
    positive: ScoreSummary
    false_positive: ScoreSummary


# Keyed by the stringified percentile ("5" .. "95").
Percentiles = dict[str, float | None]


class RawPercentiles(TypedDict):
    positive: Percentiles
    false_positive: Percentiles


class FamilyHeadroom(TypedDict):
    cutoff: float | None
    fp_above_cutoff: int
    fp_below_cutoff: int
    tp_above: int
    tp_below: int
    suppressible_fp_mass: int
    score_gap: float | None
    positive_count: int
    fp_count: int
    posterior_summary: PosteriorSummary
    raw_percentiles: RawPercentiles


class HeadroomPayload(TypedDict):
    schema_version: int
    generated_by: str
    absorbing_state_floor: float
    per_family: dict[str, FamilyHeadroom]


__all__ = [
    "FamilyHeadroom",
    "HeadroomPayload",
    "Percentiles",
    "PosteriorSummary",
    "RawPercentiles",
    "ScoreSummary",
]
