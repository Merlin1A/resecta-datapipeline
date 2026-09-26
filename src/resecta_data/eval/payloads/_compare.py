"""Both before/after comparators' clauses and verdicts."""

from __future__ import annotations

from typing import TypedDict

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


__all__ = [
    "AbsentFamilyVerdict",
    "Clause",
    "CompareDocumentsPayload",
    "ComparePayload",
    "DocumentRowVerdict",
    "DocumentsAggregateVerdict",
    "FamilyFprClause",
    "FamilyVerdict",
    "PrecisionClause",
    "RecallClause",
    "SliceClause",
    "SliceRecord",
    "Thresholds",
]
