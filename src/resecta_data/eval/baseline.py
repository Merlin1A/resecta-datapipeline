"""Derive the G8 detection baseline from the Swift harness's raw join cells.

The Swift ``G8BaselineHarness.sweepG8Corpus`` already performed the
offset-overlap join and emitted per-``(category, doctype, bucket)`` counts
(``_cells.json``, CONTRACT.md File 1). This module turns those raw counts into
the committed-ready detection baseline: precision / recall / F1 / adversarial-
suppression FP-rate, computed per cell and aggregated four ways (per-family,
per-doctype, per-demographic, grand-total).

No re-join, no IoU, no device score-dump: every metric here is pure arithmetic
over the counts the Swift side supplied. The derivation is deterministic
(sorted iteration, no wall-clock; ``source_cells_sha256`` is a hash of the
input bytes, not a clock) so the emitted baseline is byte-reproducible.

A fairness guardrail flags -- but never drops -- any slice whose positive-
support N (TP + FN) is below ``_LOW_CONFIDENCE_SUPPORT``: a low-support
demographic slice is reported with ``low_confidence: true`` rather than
silently presented as a reliable number.

See CONTRACT.md File 1; this module follows the pipeline's determinism
(``common/determinism.py``) and mechanism-language
(``common/mechanism_language.py``) rules.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from resecta_data.common.io import sha256_bytes
from resecta_data.common.mechanism_language import assert_safe

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.eval.baseline"
_SCHEMA_VERSION: Final[int] = 1
_METRIC: Final[str] = "g8_detection_baseline"

# The five doctypes and five demographic buckets the harness stratifies by.
# Every aggregate is emitted for all members of these axes even when a slice
# has zero support, so a downstream consumer can rely on a stable shape and
# the fairness guardrail can flag the empty slices (CONTRACT.md File 1).
_DOCTYPES: Final[tuple[str, ...]] = ("court", "medical", "financial", "foia", "generic")
_BUCKETS: Final[tuple[str, ...]] = ("white", "black", "hispanic", "asian", "ai_an")

# A slice whose positive-support N (TP + FN) is below this threshold is flagged
# low_confidence (reported, never dropped). 30 is the conventional small-sample
# floor; this is the demographic-fairness guardrail, not a hard gate.
_LOW_CONFIDENCE_SUPPORT: Final[int] = 30


def _safe_ratio(numerator: int, denominator: int) -> float:
    """Return ``numerator / denominator`` as a float, or ``0.0`` when denom is 0.

    Args:
        numerator: Dividend.
        denominator: Divisor; a zero divisor yields ``0.0`` (the convention the
            contract specifies for empty P/R/F1/FPR denominators).

    Returns:
        The ratio, or ``0.0`` if ``denominator`` is zero.
    """
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _f1(precision: float, recall: float) -> float:
    """Return the harmonic mean of ``precision`` and ``recall``.

    Returns ``0.0`` when either input is zero (the harmonic mean is undefined
    when the sum is zero, and zero in every other degenerate case), matching
    the contract's "0.0 when denom 0" rule.
    """
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _parse_cell_key(key: str) -> tuple[str, str, str]:
    """Split a ``"<category>_<doctype>_<bucket>"`` cell key into its parts.

    The bucket ``ai_an`` itself contains an underscore, so a naive right-split
    on a fixed token count is wrong. We therefore match the bucket and doctype
    against the known vocabularies from the longest candidate inward, and treat
    whatever remains as the (possibly underscore-bearing) family.

    Args:
        key: A cell key emitted by the Swift harness.

    Returns:
        A ``(category, doctype, bucket)`` tuple.

    Raises:
        ValueError: If the key does not end in a known ``doctype_bucket`` pair
            or leaves an empty family -- a malformed key is a contract
            violation and must fail loud rather than miscount.
    """
    for bucket in _BUCKETS:
        suffix = f"_{bucket}"
        if not key.endswith(suffix):
            continue
        head = key[: -len(suffix)]
        for doctype in _DOCTYPES:
            doc_suffix = f"_{doctype}"
            if not head.endswith(doc_suffix):
                continue
            family = head[: -len(doc_suffix)]
            if not family:
                raise ValueError(f"cell key {key!r} has an empty category component")
            return family, doctype, bucket
    raise ValueError(
        f"cell key {key!r} does not end in a known <doctype>_<bucket> pair; "
        f"doctypes={_DOCTYPES}, buckets={_BUCKETS}"
    )


class _Counts:
    """Mutable accumulator of the six raw counts carried on every cell.

    Aggregation is a sum over these six integers; every emitted metric is
    derived from a summed instance, so the per-cell and aggregate code paths
    share one metric formula and cannot drift.
    """

    __slots__ = (
        "adversarial_suppress_fired",
        "adversarial_suppress_total",
        "false_negatives",
        "false_positives",
        "suppressed_by_negative_context",
        "true_positives",
    )

    def __init__(self) -> None:
        self.true_positives = 0
        self.false_negatives = 0
        self.false_positives = 0
        self.adversarial_suppress_total = 0
        self.adversarial_suppress_fired = 0
        self.suppressed_by_negative_context = 0

    def add(self, cell: dict[str, Any]) -> None:
        """Fold one raw cell's six counts into this accumulator."""
        self.true_positives += int(cell["true_positives"])
        self.false_negatives += int(cell["false_negatives"])
        self.false_positives += int(cell["false_positives"])
        self.adversarial_suppress_total += int(cell["adversarial_suppress_total"])
        self.adversarial_suppress_fired += int(cell["adversarial_suppress_fired"])
        self.suppressed_by_negative_context += int(cell["suppressed_by_negative_context"])


def _metrics_from_counts(counts: _Counts) -> dict[str, Any]:
    """Compute the derived metric block for one cell or aggregate.

    All metrics follow the contract:

    - ``precision = TP / (TP + FP)``
    - ``recall = TP / (TP + FN)``
    - ``f1`` = harmonic mean of precision and recall
    - ``adversarial_suppression_fp_rate = fired / total`` (GT-label-keyed decoy
      fire rate)
    - ``family_false_positive_count = FP + adversarial_suppress_fired`` (generic
      spurious fires plus decoy fires)
    - ``precision_with_decoys = TP / (TP + FP + adversarial_suppress_fired)``

    Each denominator yields ``0.0`` when zero. ``support_n`` (TP + FN, the
    positive GT support) and ``detections_n`` (TP + FP, the surfaced
    detections of this kind) are carried so low-support slices can be flagged
    downstream.

    Args:
        counts: A summed (or single-cell) count accumulator.

    Returns:
        A JSON-serializable metric dict. Caller adds ``low_confidence``.
    """
    tp = counts.true_positives
    fp = counts.false_positives
    fn = counts.false_negatives
    fired = counts.adversarial_suppress_fired
    total = counts.adversarial_suppress_total

    support_n = tp + fn
    detections_n = tp + fp
    decoy_fp_count = fp + fired

    precision = _safe_ratio(tp, detections_n)
    recall = _safe_ratio(tp, support_n)
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "adversarial_suppress_total": total,
        "adversarial_suppress_fired": fired,
        "suppressed_by_negative_context": counts.suppressed_by_negative_context,
        "support_n": support_n,
        "detections_n": detections_n,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "adversarial_suppression_fp_rate": _safe_ratio(fired, total),
        "family_false_positive_count": decoy_fp_count,
        "precision_with_decoys": _safe_ratio(tp, tp + decoy_fp_count),
    }


def _aggregate_cell(counts: _Counts) -> dict[str, Any]:
    """Build an aggregate cell: metrics plus the fairness low-confidence flag.

    The flag fires when positive support (``support_n``) is below
    ``_LOW_CONFIDENCE_SUPPORT``. It is advisory -- the slice is always emitted.
    """
    metrics = _metrics_from_counts(counts)
    metrics["low_confidence"] = metrics["support_n"] < _LOW_CONFIDENCE_SUPPORT
    return metrics


def build_baseline(cells_payload: dict[str, Any]) -> dict[str, Any]:
    """Derive the committed-ready G8 detection baseline from raw join cells.

    Args:
        cells_payload: The parsed ``_cells.json`` payload (CONTRACT.md File 1).
            Must carry a ``cells`` mapping keyed
            ``"<category>_<doctype>_<bucket>"``, each value carrying the six raw
            counts. Other top-level fields (``doc_count`` etc.) are ignored by
            the derivation; the input bytes are hashed into the output for
            provenance.

    Returns:
        A JSON-serializable dict matching ``schemas/g8_detection_baseline.schema.json``:
        ``schema_version``, ``generated_by``, ``metric``, ``source_cells_sha256``,
        and the ``per_family`` / ``per_doctype`` / ``per_demographic`` /
        ``per_cell`` / ``totals`` aggregate blocks. Every doctype in
        ``_DOCTYPES`` and bucket in ``_BUCKETS`` appears in its block even with
        zero support.

    Raises:
        KeyError: If ``cells_payload`` lacks a ``cells`` mapping or a cell is
            missing a required count (fail loud).
        ValueError: If a cell key is malformed (see :func:`_parse_cell_key`).
    """
    cells: dict[str, Any] = cells_payload["cells"]

    # Hash the canonical-encoded INPUT for provenance. Re-encoding canonically
    # (rather than hashing whatever bytes happened to arrive) makes the digest
    # invariant to upstream whitespace/key-order churn, so an unchanged join
    # produces an unchanged baseline hash.
    source_sha = sha256_bytes(_canonical_bytes(cells_payload))

    per_cell_counts: dict[str, _Counts] = {}
    per_family_counts: dict[str, _Counts] = {}
    per_doctype_counts: dict[str, _Counts] = {d: _Counts() for d in _DOCTYPES}
    per_demographic_counts: dict[str, _Counts] = {b: _Counts() for b in _BUCKETS}
    totals = _Counts()

    # Iterate cells in sorted key order so accumulation is deterministic
    # (integer addition is associative, but sorted iteration keeps the code
    # honest and the per-source attribution stable for any future extension).
    for key in sorted(cells):
        cell = cells[key]
        family, doctype, bucket = _parse_cell_key(key)

        per_cell_counts.setdefault(key, _Counts()).add(cell)
        per_family_counts.setdefault(family, _Counts()).add(cell)
        # doctype/bucket buckets are pre-seeded for the full axis; an
        # out-of-axis label would have raised in _parse_cell_key already.
        per_doctype_counts[doctype].add(cell)
        per_demographic_counts[bucket].add(cell)
        totals.add(cell)

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "metric": _METRIC,
        "source_cells_sha256": source_sha,
        "per_family": {
            fam: _aggregate_cell(per_family_counts[fam]) for fam in sorted(per_family_counts)
        },
        "per_doctype": {doc: _aggregate_cell(per_doctype_counts[doc]) for doc in _DOCTYPES},
        "per_demographic": {bkt: _aggregate_cell(per_demographic_counts[bkt]) for bkt in _BUCKETS},
        "per_cell": {key: _aggregate_cell(per_cell_counts[key]) for key in sorted(per_cell_counts)},
        "totals": _aggregate_cell(totals),
    }

    low_conf = sorted(
        f"{axis}:{name}"
        for axis, block in (
            ("family", payload["per_family"]),
            ("doctype", payload["per_doctype"]),
            ("demographic", payload["per_demographic"]),
        )
        for name, cell in block.items()
        if cell["low_confidence"]
    )
    if low_conf:
        # Mechanism-language guard on the only free-form string this builder
        # would surface, then a measurement-only operator note (no payload
        # mutation -- determinism-safe).
        note = (
            "low-support slices flagged (support_n below "
            f"{_LOW_CONFIDENCE_SUPPORT}): {', '.join(low_conf)}"
        )
        assert_safe(note, context="eval.baseline low_confidence note")
        logger.info("%s", note)

    return payload


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Return the canonical-JSON byte encoding of ``payload`` for hashing.

    Mirrors the serialization parameters of
    ``common.io.dump_canonical_json`` (sorted keys, indent 2, the canonical
    separators, ``ensure_ascii=False``, trailing newline) so the provenance
    digest is invariant to upstream whitespace and key-order churn: an
    unchanged join yields an unchanged ``source_cells_sha256``.
    """
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=False,
    )
    return encoded.encode("utf-8") + b"\n"


__all__ = ["build_baseline"]
