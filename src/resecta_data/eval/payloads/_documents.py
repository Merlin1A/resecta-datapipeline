"""The document-level eval's output blocks (``documents_eval.json``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NotRequired, TypedDict, cast

from ._common import Interval, _require

# ---------------------------------------------------------------------------
# The document-level eval: the run, leg and document blocks of ``documents_eval.json``
# ---------------------------------------------------------------------------


class TierCounts(TypedDict):
    """The Option-C denominators of one run (per category and in total)."""

    mf_total: int
    mf_region: int
    mf_strict: int
    mnf_total: int
    mnf_fire_region: int
    mnf_fire_strict: int
    sf_total: int
    sf_region: int
    iou_denom: int
    iou_head: int
    iou_tight: int
    text_strict: int
    text_relaxed: int
    text_denom: int


class StratumCounts(TypedDict):
    mf_total: int
    mf_region: int
    mf_strict: int


# kind ("digit_ambiguity" / "context_class") -> stratum -> counts.
StrataCounts = dict[str, dict[str, StratumCounts]]


class StratumView(TypedDict):
    support: int
    region_recall: float
    strict_recall: float
    strict_recall_ci95: Interval


StrataViews = dict[str, dict[str, StratumView]]


class RateBlock(TypedDict):
    recall: float
    precision: float
    f1: float
    f2: float
    recall_ci95: Interval


class MustNotFireBlock(TypedDict):
    total: int
    fired_as_category: int
    region_covered: int


class ShouldFireBlock(TypedDict):
    total: int
    region_covered: int


class IouBlock(TypedDict):
    headline_rate: float
    tight_rate: float


class ValueTextBlock(TypedDict):
    denom: int
    strict_rate: float
    relaxed_rate: float


class MetricView(TypedDict):
    support: int
    low_support: bool
    region: RateBlock
    strict: RateBlock
    must_not_fire: MustNotFireBlock
    should_fire: ShouldFireBlock
    iou: IouBlock
    value_text: NotRequired[ValueTextBlock]


class OccurrenceJoin(TypedDict):
    """The Option-C verdict of one occurrence against one page's hits."""

    covered: bool
    iou_matched: bool
    iou_tight: bool
    cover_category: str | None
    cover_text: str | None
    cover_fraction: float
    cover_detections: int


class Verdict(TypedDict):
    leg: str
    expectation: str
    covered: bool
    strict: bool
    iou_matched: bool
    cover_category: str | None
    cover_text: str | None
    cover_fraction: float
    cover_detections: int
    digit_stratum: str
    ambiguous_token_count: int
    digit_token_count: int
    context_class: str


class RunBlock(TypedDict):
    """One harness run joined against the occurrence list."""

    run_index: int
    leg: str
    counts: TierCounts
    per_category: dict[str, MetricView]
    headline: MetricView
    confusion: dict[str, int]
    surplus_fires: int
    surplus_fires_per_page: float
    surplus_fires_by_page: dict[str, int]
    strata: StrataViews
    strata_counts: StrataCounts
    verdicts: dict[str, Verdict]
    diagnostics: dict[str, object]


class MedianBlock(TypedDict):
    headline: MetricView
    per_category: dict[str, MetricView]
    confusion: dict[str, int]
    surplus_fires: int
    surplus_fires_per_page: float
    surplus_fires_by_page: dict[str, int]
    strata: StrataViews
    verdicts: dict[str, Verdict]


class LegBlock(TypedDict):
    n_runs: int
    median_run_index: int
    median: MedianBlock
    strict_recall_min_median_max: list[float]
    region_recall_min_median_max: list[float]


# A document's block: its variant kind plus one entry per leg kind it ran
# (the leg kinds are not identifiers, hence the functional form).
DocumentBlock = TypedDict(
    "DocumentBlock",
    {
        "variant": str | None,
        "text": NotRequired[LegBlock],
        "ocr": NotRequired[LegBlock],
        "ocr-forced": NotRequired[LegBlock],
        "mixed": NotRequired[LegBlock],
    },
)


class Pool(TypedDict):
    documents: list[str]
    micro_f1: float
    micro_f2: float
    micro_f1_bca_ci95: Interval
    micro_f2_bca_ci95: Interval
    strata: StrataViews


class MatchRule(TypedDict):
    join_rule: str
    region: str
    iou: str
    strict: str
    value_text: str
    precision_denominator: str


class LegAttribution(TypedDict):
    clean_twins: dict[str, int]
    ocr_induced: list[str]
    normalizer_destroyed: list[str] | None
    detector_induced: list[str]
    unattributed: list[str]
    ocr_induced_count: int
    normalizer_destroyed_count: int | None
    detector_induced_count: int
    unattributed_count: int
    strict_miss_total: int
    normalizer_check: str


class MissAttribution(TypedDict):
    clean_twin_rule: str
    text_legs_present: list[str]
    classes: list[str]
    documents: dict[str, dict[str, LegAttribution]]
    note: NotRequired[str]


class OccurrenceMeta(TypedDict):
    """The caption-merge view of one occurrence (``value`` always, the rest when drawn)."""

    value: str
    page: NotRequired[int | None]
    caption_clearance_pt: NotRequired[float | None]
    caption_text: NotRequired[str | None]
    multiline: NotRequired[bool]


class MergeRow(TypedDict):
    clearance_pt: float
    bucket: str
    merged: bool | None
    strict: bool


class MergeCounts(TypedDict):
    rows: int
    decided: int
    merged: int


class MergeBucket(MergeCounts):
    merged_rate: float | None


class MergeLeg(TypedDict):
    rows: dict[str, MergeRow]
    buckets: dict[str, MergeBucket]
    line_check: str


class CaptionMerge(TypedDict):
    rule: str
    buckets: list[str]
    documents: dict[str, dict[str, MergeLeg]]
    pools: dict[str, dict[str, MergeBucket]]


class DocumentsEvalPayload(TypedDict):
    schema_version: int
    site: str
    generated_by: str
    match_rule: MatchRule
    per_document: dict[str, DocumentBlock]
    pools: dict[str, Pool]
    miss_attribution: MissAttribution
    caption_merge: CaptionMerge


def as_documents_eval(raw: Mapping[str, object]) -> DocumentsEvalPayload:
    """A parsed ``documents_eval.json`` under its named shape (``per_document`` required)."""
    _require(raw, ("per_document",))
    return cast("DocumentsEvalPayload", raw)


__all__ = [
    "CaptionMerge",
    "DocumentBlock",
    "DocumentsEvalPayload",
    "IouBlock",
    "LegAttribution",
    "LegBlock",
    "MatchRule",
    "MedianBlock",
    "MergeBucket",
    "MergeCounts",
    "MergeLeg",
    "MergeRow",
    "MetricView",
    "MissAttribution",
    "MustNotFireBlock",
    "OccurrenceJoin",
    "OccurrenceMeta",
    "Pool",
    "RateBlock",
    "RunBlock",
    "ShouldFireBlock",
    "StrataCounts",
    "StrataViews",
    "StratumCounts",
    "StratumView",
    "TierCounts",
    "ValueTextBlock",
    "Verdict",
    "as_documents_eval",
]
