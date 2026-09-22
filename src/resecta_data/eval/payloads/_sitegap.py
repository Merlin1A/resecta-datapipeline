"""The site gap (``g8_site_gap.json``)."""

from __future__ import annotations

from typing import TypedDict

from ._baseline import WatchTier
from ._common import Interval

# ---------------------------------------------------------------------------
# The site gap (``g8_site_gap.json``)
# ---------------------------------------------------------------------------


class SiteMustSummary(TypedDict):
    total: int
    covered: int
    recall: float
    precision_option_c: float
    f2_option_c: float


class SiteShouldSummary(TypedDict):
    total: int
    covered: int
    recall: float


class MustNotSummary(TypedDict):
    total: int
    fired: int
    fire_rate: float


class SiteTierSummary(TypedDict):
    must: SiteMustSummary
    should: SiteShouldSummary
    watch: WatchTier
    must_not: MustNotSummary


class SiteSummary(TypedDict):
    """One side of a site-gap row."""

    precision: float
    recall: float
    f1: float
    f2: float
    precision_with_decoys: float
    true_positives: int
    false_negatives: int
    false_positives: int
    adversarial_suppress_fired: int
    support_n: int
    detections_n: int
    precision_wilson95: Interval
    recall_wilson95: Interval
    low_confidence: bool
    per_tier: SiteTierSummary


class SiteDelta(TypedDict):
    """Site B minus detector; ``per_tier`` is keyed ``<tier>_<field>``."""

    precision: float
    recall: float
    f1: float
    f2: float
    precision_with_decoys: float
    true_positives: int
    false_negatives: int
    false_positives: int
    adversarial_suppress_fired: int
    per_tier: dict[str, float]


class SiteGapRow(TypedDict):
    """A family row: a side absent from one baseline is null, and so is the delta."""

    present_in: list[str]
    detector: SiteSummary | None
    siteb: SiteSummary | None
    delta_siteb_minus_detector: SiteDelta | None


class SiteGapTotals(TypedDict):
    """The grand-total row: both baselines always carry ``totals``."""

    present_in: list[str]
    detector: SiteSummary
    siteb: SiteSummary
    delta_siteb_minus_detector: SiteDelta


class SiteGapPayload(TypedDict):
    schema_version: int
    generated_by: str
    metric: str
    detector_sha256: str
    siteb_sha256: str
    families: list[str]
    families_with_gap: list[str]
    per_family: dict[str, SiteGapRow]
    totals: SiteGapTotals


__all__ = [
    "MustNotSummary",
    "SiteDelta",
    "SiteGapPayload",
    "SiteGapRow",
    "SiteGapTotals",
    "SiteMustSummary",
    "SiteShouldSummary",
    "SiteSummary",
    "SiteTierSummary",
]
