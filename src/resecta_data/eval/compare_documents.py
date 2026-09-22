"""The four-clause before/after predicate over DOCUMENT rows (1.2 P0.5).

:mod:`resecta_data.eval.compare` decides the §3 predicate from two derived G8
baselines, per scorer family. This module applies the SAME four clauses to
two ``documents_eval.json`` dicts (the H1.3 document-level Site-B eval,
:mod:`resecta_data.eval.documents`): one row per ``(document, leg kind)``
present in BOTH evals, plus a grand-total aggregate pooled over the rows.

A row's cell is read from the leg's median-run headline, which is already
Option-C shaped (``documents._metric_view``): ``precision`` = strict precision
(must-fire strict hits over the hits plus the must-not-fire spans that fired
as the category), ``recall`` = strict recall, and ``precision_with_decoys``
= that same strict precision -- its denominator IS the decoy construction, so
C2 (family-FPR) reads the must-not-fire rate exactly as the G8 comparator
reads ``1 - precision_with_decoys``. C1 / C2 / C3 then run unchanged (the
clause functions are imported from :mod:`resecta_data.eval.compare`). C4, the
slice non-regression guard, walks the document's per-category strict
precision (the axis a document carries) on each row, and the per-leg-kind
pooled precision on the aggregate.

Pooled counts for the aggregate are recovered from the headline integers
(``support``, ``must_not_fire.fired_as_category``) and the rounded strict
recall (``round(recall * support)`` is exact for any document-sized support),
so the aggregate is pure arithmetic over the frozen eval, like everything
else here: no re-join, no re-scoring. A ``(document, leg)`` present on one
side only is listed in ``rows_skipped`` rather than evaluated.

Same units and defaults as ``eval-compare``: ``delta_p`` / ``delta_slice``
are precision fractions, ``eps`` / ``delta_f_rel`` fractions throughout.
Dev/eval artifact; no install route; deterministic (input digests, sorted
iteration).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from resecta_data.common.io import canonical_bytes, sha256_bytes

from .compare import _C4, _family_verdict
from .payloads import (
    LEG_KINDS,
    ClauseCell,
    CompareDocumentsPayload,
    DocumentBlock,
    DocumentRowVerdict,
    DocumentsAggregateVerdict,
    DocumentsEvalPayload,
    FamilyVerdict,
    LegKind,
    MedianBlock,
    MetricView,
    SliceClause,
    SliceRecord,
    as_documents_eval,
)

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.eval.compare_documents"
_SCHEMA_VERSION: Final[int] = 1
_METRIC: Final[str] = "g8_compare_documents_verdict"
_AGGREGATE_NAME: Final[str] = "aggregate"

# The leg kinds documents.py emits, in reporting order.
_LEG_ORDER: Final[dict[str, int]] = {"text": 0, "ocr": 1, "ocr-forced": 2, "mixed": 3}

# A pooled must-fire support below this is advisory low_confidence (the
# document eval's own ``low_support`` floor, ``documents._LOW_SUPPORT``).
_LOW_SUPPORT: Final[int] = 30


def _row_name(document: str, leg: str) -> str:
    return f"{document}/{leg}"


def _leg_rank(leg: str) -> int:
    return _LEG_ORDER.get(leg, len(_LEG_ORDER))


def _headline(eval_payload: DocumentsEvalPayload, document: str, leg: LegKind) -> MedianBlock:
    return eval_payload["per_document"][document][leg]["median"]


def _cell_from_headline(headline: MetricView) -> ClauseCell:
    """The Option-C strict headline as the cell shape the G8 clauses read."""
    strict = headline["strict"]
    return {
        "precision": float(strict["precision"]),
        "recall": float(strict["recall"]),
        # Strict precision is must-not-fire constructed already (Option C).
        "precision_with_decoys": float(strict["precision"]),
        "low_confidence": bool(headline["low_support"]),
    }


def _pooled_counts(headline: MetricView) -> tuple[int, int, int]:
    """(must-fire support, strict hits, must-not-fire fired as category)."""
    support = int(headline["support"])
    hits = round(float(headline["strict"]["recall"]) * support)
    fired = int(headline["must_not_fire"]["fired_as_category"])
    return support, hits, fired


def _cell_from_counts(support: int, hits: int, fired: int, low_confidence: bool) -> ClauseCell:
    denominator = hits + fired
    precision = hits / denominator if denominator else 1.0
    recall = hits / support if support else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "precision_with_decoys": precision,
        "low_confidence": low_confidence,
    }


def _slice_clause(
    before: dict[str, float], after: dict[str, float], axis: str, delta_slice: float
) -> SliceClause:
    """C4 over one axis of named slices (strict precision before/after)."""
    records: list[SliceRecord] = []
    for name in sorted(set(before) & set(after)):
        before_p, after_p = before[name], after[name]
        delta = after_p - before_p
        records.append(
            {
                "axis": axis,
                "slice": name,
                "before": before_p,
                "after": after_p,
                "delta": delta,
                "regressed": delta < -delta_slice,
            }
        )
    regressed = [r for r in records if r["regressed"]]
    return {
        "clause": _C4,
        "win": not regressed,
        "regressed": bool(regressed),
        "win_threshold": delta_slice,
        "slices": records,
        "regressed_slices": [f"{r['axis']}:{r['slice']}" for r in regressed],
    }


def _category_precisions(headline_block: MedianBlock) -> dict[str, float]:
    per_category = headline_block["per_category"]
    return {cat: float(view["strict"]["precision"]) for cat, view in per_category.items()}


def _attach_c4(verdict: FamilyVerdict, c4: SliceClause) -> None:
    """Fold a C4 clause into a verdict built by ``_family_verdict``."""
    verdict["clauses"].append(c4)
    if c4["regressed"]:
        verdict["regressed_clauses"].append(c4["clause"])
        verdict["regression"] = True
    verdict["win"] = bool(verdict["win"]) and bool(c4["win"])


def build_compare_documents(
    before: Mapping[str, object],
    after: Mapping[str, object],
    thresholds: dict[str, float],
) -> CompareDocumentsPayload:
    """Decide the four-clause predicate over document rows of two evals.

    Args:
        before: The parsed BEFORE ``documents_eval.json`` dict.
        after: The parsed AFTER ``documents_eval.json`` dict.
        thresholds: ``delta_p`` / ``delta_f_rel`` / ``eps`` / ``delta_slice``
            (``delta_p`` and ``delta_slice`` as precision FRACTIONS).

    Returns:
        A JSON-serializable verdict dict matching
        ``schemas/g8_compare_documents.schema.json``: one row verdict per
        ``(document, leg)`` present on both sides, the rows present on one
        side only, the pooled aggregate, both input digests, and the overall
        ``regression`` flag (any row OR the aggregate).

    Raises:
        KeyError: If an eval lacks a required block or field (fail loud).
    """
    delta_p = float(thresholds["delta_p"])
    delta_f_rel = float(thresholds["delta_f_rel"])
    eps = float(thresholds["eps"])
    delta_slice = float(thresholds["delta_slice"])

    before_eval = as_documents_eval(before)
    after_eval = as_documents_eval(after)
    before_docs = before_eval["per_document"]
    after_docs = after_eval["per_document"]

    def legs(block: DocumentBlock) -> set[LegKind]:
        # Every key but ``variant`` is a leg kind (the schema admits no other).
        return {leg for leg in LEG_KINDS if leg in block}

    before_rows = {(doc, leg) for doc, block in before_docs.items() for leg in legs(block)}
    after_rows = {(doc, leg) for doc, block in after_docs.items() for leg in legs(block)}
    shared = sorted(before_rows & after_rows, key=lambda r: (r[0], _leg_rank(r[1]), r[1]))
    skipped = sorted(before_rows ^ after_rows, key=lambda r: (r[0], _leg_rank(r[1]), r[1]))

    row_verdicts: list[DocumentRowVerdict] = []
    pooled_before: dict[str, list[int]] = {}
    pooled_after: dict[str, list[int]] = {}
    for document, leg in shared:
        head_before = _headline(before_eval, document, leg)
        head_after = _headline(after_eval, document, leg)
        verdict = _family_verdict(
            _row_name(document, leg),
            _cell_from_headline(head_before["headline"]),
            _cell_from_headline(head_after["headline"]),
            delta_p=delta_p,
            delta_f_rel=delta_f_rel,
            eps=eps,
            delta_slice=delta_slice,
        )
        row: DocumentRowVerdict = {**verdict, "document": document, "leg": leg}
        _attach_c4(
            row,
            _slice_clause(
                _category_precisions(head_before),
                _category_precisions(head_after),
                "category",
                delta_slice,
            ),
        )
        row_verdicts.append(row)
        for pool, head in ((pooled_before, head_before), (pooled_after, head_after)):
            counts = _pooled_counts(head["headline"])
            for key in ("__all__", leg):
                acc = pool.setdefault(key, [0, 0, 0])
                acc[0] += counts[0]
                acc[1] += counts[1]
                acc[2] += counts[2]

    def pooled_cell(pool: dict[str, list[int]], key: str) -> ClauseCell:
        support, hits, fired = pool.get(key, [0, 0, 0])
        return _cell_from_counts(support, hits, fired, low_confidence=support < _LOW_SUPPORT)

    aggregate = _family_verdict(
        _AGGREGATE_NAME,
        pooled_cell(pooled_before, "__all__"),
        pooled_cell(pooled_after, "__all__"),
        delta_p=delta_p,
        delta_f_rel=delta_f_rel,
        eps=eps,
        delta_slice=delta_slice,
    )
    leg_kinds = sorted(
        (set(pooled_before) | set(pooled_after)) - {"__all__"}, key=lambda k: (_leg_rank(k), k)
    )
    _attach_c4(
        aggregate,
        _slice_clause(
            {leg: pooled_cell(pooled_before, leg)["precision"] for leg in leg_kinds},
            {leg: pooled_cell(pooled_after, leg)["precision"] for leg in leg_kinds},
            "leg",
            delta_slice,
        ),
    )
    aggregate_out: DocumentsAggregateVerdict = {**aggregate, "rows_pooled": len(shared)}

    overall = any(row["regression"] for row in row_verdicts) or bool(aggregate_out["regression"])
    payload: CompareDocumentsPayload = {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "metric": _METRIC,
        "before_sha256": sha256_bytes(canonical_bytes(before)),
        "after_sha256": sha256_bytes(canonical_bytes(after)),
        "before_site": str(before.get("site", "unknown")),
        "after_site": str(after.get("site", "unknown")),
        "thresholds": {
            "delta_p": delta_p,
            "delta_f_rel": delta_f_rel,
            "eps": eps,
            "delta_slice": delta_slice,
        },
        "regression": overall,
        "rows": row_verdicts,
        "rows_skipped": [_row_name(doc, leg) for doc, leg in skipped],
        "aggregate": aggregate_out,
    }
    logger.info(
        "eval compare-documents: %s (rows=%d, skipped=%d, aggregate regression=%s)",
        "regression" if overall else "no-regression",
        len(row_verdicts),
        len(skipped),
        aggregate_out["regression"],
    )
    return payload


__all__ = ["build_compare_documents"]
