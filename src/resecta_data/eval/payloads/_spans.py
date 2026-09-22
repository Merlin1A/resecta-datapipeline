"""The G8 corpus, the per-span sidecar rows and the span-outcome tables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict, cast

from ._common import Interval

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


__all__ = [
    "CellsCrosscheck",
    "ContextClassDescriptor",
    "CorpusDocument",
    "CorpusSpan",
    "FurnitureDescriptor",
    "FurnitureKindEntry",
    "FurnitureRegion",
    "G8Corpus",
    "SidecarRow",
    "SpanCellView",
    "SpanOutcomesPayload",
    "SpanRowCounts",
    "TallyView",
    "TierTally",
    "as_corpus",
    "as_sidecar_row",
    "as_sidecar_rows",
]
