"""The named JSON shapes the eval stages pass between each other.

Every payload the eval package reads (the Swift harness emissions, the
corpus, the ground-truth sidecars, a derived artifact read back from disk)
or writes (the derived baseline, headroom probe, span outcomes, document
eval and the three comparators) is a plain JSON object; the classes here
name those objects' keys and value types for the type checker. They are
``TypedDict`` classes, so at runtime every payload is still the plain
``dict`` the builders construct -- key order, values and bytes are exactly
what the builders insert.

Inputs cross into the typed world at ONE place per shape: the ``as_*``
boundary functions below, which check the top-level keys a stage relies on
(raising ``KeyError`` like the dict access they stand in for) and return the
same object under its named shape. Nothing is copied or re-encoded, so the
provenance digests the builders take of their inputs are unchanged.

Optional keys a stage reads with ``.get`` are ``NotRequired``; a key a
builder adds only on some rows (``value_text``, ``note``) is ``NotRequired``
on the output shape too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Literal, NotRequired, TypedDict, cast

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

# A Wilson / BCa interval: [low, high], or null when the denominator is zero.
Interval = list[float] | None

# The leg kinds a document eval reports (``documents._leg_kind``), in order.
LegKind = Literal["text", "ocr", "ocr-forced", "mixed"]
LEG_KINDS: Final[tuple[LegKind, ...]] = ("text", "ocr", "ocr-forced", "mixed")


def _require(raw: Mapping[str, object], keys: tuple[str, ...]) -> None:
    """Fail like the dict access a stage makes: ``KeyError`` on the first absent key."""
    for key in keys:
        if key not in raw:
            raise KeyError(key)


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


# ---------------------------------------------------------------------------
# The raw match scores (``_raw_scores.json``, CONTRACT.md File 2) and headroom
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


# ---------------------------------------------------------------------------
# The before/after comparators (``g8_compare_verdict`` / ``..._documents``)
# ---------------------------------------------------------------------------


class Thresholds(TypedDict):
    delta_p: float
    delta_f_rel: float
    eps: float
    delta_slice: float


class PrecisionClause(TypedDict):
    clause: str
    win: bool
    regressed: bool
    before: float
    after: float
    delta: float
    win_threshold: float


class FamilyFprClause(TypedDict):
    clause: str
    win: bool
    regressed: bool
    before: float
    after: float
    win_threshold: float
    delta_f_rel: float


class RecallClause(TypedDict):
    clause: str
    win: bool
    regressed: bool
    before: float
    after: float
    win_threshold: float
    eps: float


class SliceRecord(TypedDict):
    axis: str
    slice: str
    before: float
    after: float
    delta: float
    regressed: bool


class SliceClause(TypedDict):
    clause: str
    win: bool
    regressed: bool
    win_threshold: float
    slices: list[SliceRecord]
    regressed_slices: list[str]


Clause = PrecisionClause | FamilyFprClause | RecallClause | SliceClause


class FamilyVerdict(TypedDict):
    name: str
    non_regression_only: bool
    regression: bool
    win: bool
    regressed_clauses: list[str]
    clauses: list[Clause]
    low_confidence: bool


class AbsentFamilyVerdict(TypedDict):
    """A scorer family with no ``per_family`` entry (off the G8 panel)."""

    name: str
    absent: bool
    note: str
    regression: bool


class ComparePayload(TypedDict):
    schema_version: int
    generated_by: str
    metric: str
    before_sha256: str
    after_sha256: str
    thresholds: Thresholds
    regression: bool
    families: list[FamilyVerdict | AbsentFamilyVerdict]
    aggregate: FamilyVerdict


class DocumentRowVerdict(FamilyVerdict):
    document: str
    leg: str


class DocumentsAggregateVerdict(FamilyVerdict):
    rows_pooled: int


class CompareDocumentsPayload(TypedDict):
    schema_version: int
    generated_by: str
    metric: str
    before_sha256: str
    after_sha256: str
    before_site: str
    after_site: str
    thresholds: Thresholds
    regression: bool
    rows: list[DocumentRowVerdict]
    rows_skipped: list[str]
    aggregate: DocumentsAggregateVerdict


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


# ---------------------------------------------------------------------------
# The G8 corpus and the per-span sidecar (``g8_span_outcomes.json``)
# ---------------------------------------------------------------------------


class CorpusSpan(TypedDict):
    category: str
    start: int
    end: int
    tier: NotRequired[str | None]
    expected_outcome: NotRequired[str]
    context_class: NotRequired[str | None]
    value: NotRequired[str]


class FurnitureRegion(TypedDict):
    """A planted furniture region; validated field by field at the join."""

    start: NotRequired[int]
    end: NotRequired[int]
    kind: NotRequired[str]


class CorpusDocument(TypedDict):
    id: str
    doctype: str
    demographic_bucket: str
    text: str
    pii_spans: list[CorpusSpan]
    furniture: NotRequired[list[FurnitureRegion] | None]


class G8Corpus(TypedDict):
    documents: NotRequired[list[CorpusDocument]]


def as_corpus(raw: Mapping[str, object]) -> G8Corpus:
    """The parsed G8 corpus under its named shape (every key read defensively)."""
    return cast("G8Corpus", raw)


class SidecarRow(TypedDict):
    """One JSONL sidecar row: offsets only, never document text."""

    doc_id: str
    family: str
    start: int
    end: int
    tier: str | None
    outcome: str
    det_start: NotRequired[int]
    det_end: NotRequired[int]
    det_spans: NotRequired[list[list[int]]]


def as_sidecar_row(row: Mapping[str, object]) -> SidecarRow:
    """A sidecar row the reader has validated key by key, under its named shape."""
    return cast("SidecarRow", row)


def as_sidecar_rows(rows: Sequence[Mapping[str, object]]) -> Sequence[SidecarRow]:
    """Sidecar rows as ``read_sidecar`` validated them, under their named shape."""
    return cast("Sequence[SidecarRow]", rows)


class TierTally(TypedDict):
    total: int
    covered: int


class TallyView(TypedDict):
    tp: int
    fn: int
    fp: int
    must_not_total: int
    must_not_fired: int
    one_token_tp: int
    recall: float
    recall_wilson95: Interval
    by_tier: dict[str, TierTally]
    token_coverage: dict[str, int]
    detections_per_tp: dict[str, int]


class SpanCellView(TallyView):
    family: str
    doctype: str
    bucket: str
    context_class: str | None


class ContextClassDescriptor(TypedDict):
    source: str
    vocabulary: list[str]
    present: list[str]
    classed_ground_truth_rows: int


class FurnitureDescriptor(TypedDict):
    source: str
    join: str
    kinds_present: list[str]
    regions: int
    documents_with_furniture: int


class FurnitureKindEntry(TypedDict):
    fp: int
    by_doctype: dict[str, int]


class CellsCrosscheck(TypedDict):
    status: str
    cells_compared: int


class SpanRowCounts(TypedDict):
    total: int
    ground_truth: int
    corpus_spans: int
    tp: int
    fn: int
    fp: int
    tn: int
    must_not_fired: int
    one_token_tp: int


class SpanOutcomesPayload(TypedDict):
    schema_version: int
    generated_by: str
    metric: str
    site: str
    source_spans_sha256: str
    source_corpus_sha256: str
    join_rule: str
    context_class: ContextClassDescriptor | None
    furniture: FurnitureDescriptor | None
    row_counts: SpanRowCounts
    one_token_rule: str
    cells_crosscheck: CellsCrosscheck
    per_family: dict[str, TallyView]
    cells: dict[str, SpanCellView]
    by_context_class: dict[str, dict[str, TallyView]]
    cells_by_context_class: dict[str, SpanCellView]
    by_furniture_kind: dict[str, dict[str, FurnitureKindEntry]]
    totals: TallyView


# ---------------------------------------------------------------------------
# The document-level eval: its inputs (manifest, ground truth, harness runs,
# OCR line dumps) and ``documents_eval.json``
# ---------------------------------------------------------------------------


class ManifestRow(TypedDict):
    id: str
    path: NotRequired[str]
    gt: NotRequired[str | None]


def as_manifest(raw: Sequence[Mapping[str, object]]) -> Sequence[ManifestRow]:
    """The parsed ``documents.manifest.json`` rows under their named shape."""
    return cast("Sequence[ManifestRow]", raw)


class OccurrenceSpan(TypedDict):
    page: NotRequired[int]
    bbox: NotRequired[list[float] | None]


class OccurrenceRender(TypedDict):
    multiline: NotRequired[bool]


class Occurrence(TypedDict):
    """One ground-truth occurrence; boxes are corner form [x0, y0, x1, y1]."""

    id: str
    category: str
    expectation: str
    page: NotRequired[int | None]
    bbox: NotRequired[list[float] | None]
    spans: NotRequired[list[OccurrenceSpan] | None]
    value: NotRequired[str]
    leg_applicability: NotRequired[list[str]]
    context_class: NotRequired[str | None]
    caption_clearance_pt: NotRequired[float | None]
    caption_text: NotRequired[str | None]
    render: NotRequired[OccurrenceRender | None]


class GroundTruthVariant(TypedDict):
    kind: NotRequired[str | None]


class GroundTruth(TypedDict):
    occurrences: list[Occurrence]
    carried_stmt: NotRequired[list[Occurrence] | None]
    variant: NotRequired[GroundTruthVariant | None]


def as_ground_truth(raw: Mapping[str, object]) -> GroundTruth:
    """A parsed ground-truth sidecar under its named shape (``occurrences`` required)."""
    _require(raw, ("occurrences",))
    return cast("GroundTruth", raw)


class Hit(TypedDict):
    """One harness hit; ``rect`` is [x, y, w, h], normalized, bottom-left origin."""

    page: int
    category: str
    rect: list[float]
    text: NotRequired[str | None]
    # Carried by the harness, never read by the join.
    confidence: NotRequired[float]
    source: NotRequired[str]
    ocr_confidence: NotRequired[float | None]


class HarnessRun(TypedDict):
    """One ``DocumentHarnessTests`` hits JSON (one document x leg x run)."""

    leg: str
    run_index: int
    hits: list[Hit]
    site: NotRequired[str]
    page_count: NotRequired[int]
    text_layer_status: NotRequired[list[str] | None]
    # Opaque harness diagnostics, carried through to the run block untouched.
    diagnostics: NotRequired[dict[str, object]]


def as_harness_run(raw: Mapping[str, object]) -> HarnessRun:
    """A parsed harness hits JSON under its named shape."""
    _require(raw, ("leg", "run_index", "hits"))
    return cast("HarnessRun", raw)


class OcrLine(TypedDict):
    text: str
    normalized: str


class OcrPage(TypedDict):
    page: int
    lines: list[OcrLine]


class OcrLineDump(TypedDict):
    pages: NotRequired[list[OcrPage]]


def as_ocr_line_dump(raw: Mapping[str, object]) -> OcrLineDump:
    """A parsed ``ocr-lines-run-<n>.json`` dump under its named shape."""
    return cast("OcrLineDump", raw)


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
