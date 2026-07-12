"""Quantify per-family learned-term headroom (M9) from raw match scores.

Input is the Swift harness's ``_raw_scores.json`` (CONTRACT.md File 2): every
match the detector returned pre-cutoff, tagged ``gt_class`` ("positive" /
"suppress" / "none") by offset overlap, plus the per-family balanced cutoff map
and the absorbing-state prior floor.

M9 asks, per family: is there *headroom* for a learned scoring term -- i.e. do
the false-positive-class scores (``none`` + ``suppress``) separate from the
true-positive (``positive``) scores enough that a learned weight could push FP
mass below the cutoff without dragging TP mass with it? We answer it by
counting how much FP vs TP mass sits above / below the balanced cutoff (raw),
and above / below the posterior of that cutoff, and by reporting the raw score
gap between the FP and TP masses.

The posterior mirrors the engine seam ``CalibratedScorer.posterior(raw,
priorMean = max(prior, floor))`` with EMPTY priors -- the gate path is
pre-posterior, so we apply the same arithmetic here:
``posterior = sigmoid(logit(raw) + logit(max(prior, floor)))`` and, with no
per-category prior, ``prior = 0`` so ``priorMean = floor``. Raw scores are
clamped off the open-interval endpoints before ``logit`` so 0.0 / 1.0 inputs
stay finite (the engine clamps identically at the seam).

Deterministic: sorted families, sorted percentile keys, no wall-clock.

See CONTRACT.md File 2; this module follows the pipeline's determinism
(``common/determinism.py``) and mechanism-language
(``common/mechanism_language.py``) rules.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Final

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.eval.headroom"
_SCHEMA_VERSION: Final[int] = 1

# The three overlap classes the harness tags each raw score with. "positive" is
# true-positive mass; "none" + "suppress" are the FP classes M9 wants a learned
# term to push down (the GT-keyed decoy "suppress" plus generic spurious
# "none").
_GT_POSITIVE: Final[str] = "positive"
_GT_SUPPRESS: Final[str] = "suppress"
_GT_NONE: Final[str] = "none"
_FP_CLASSES: Final[frozenset[str]] = frozenset({_GT_NONE, _GT_SUPPRESS})

# Clamp epsilon: raw scores are nudged off {0, 1} before logit so the transform
# stays finite. 1e-6 matches the magnitude the engine uses at the seam and is
# far below any cutoff, so it does not move a score across a decision boundary.
_CLAMP_EPS: Final[float] = 1e-6

# Percentiles reported for each class's raw-score distribution. Sorted tuple so
# the emitted key order is stable; the keys are stringified for JSON.
_PERCENTILES: Final[tuple[int, ...]] = (5, 25, 50, 75, 95)


def _logit(p: float) -> float:
    """Return the log-odds of ``p``, clamped off the open-interval endpoints.

    Args:
        p: A probability-like value, typically in ``[0, 1]``. Values at or
            beyond ``{0, 1}`` are clamped to ``[_CLAMP_EPS, 1 - _CLAMP_EPS]``
            so ``log`` stays finite -- the same clamp the engine applies before
            its posterior transform.

    Returns:
        ``log(p / (1 - p))`` on the clamped value.
    """
    clamped = min(max(p, _CLAMP_EPS), 1.0 - _CLAMP_EPS)
    return math.log(clamped / (1.0 - clamped))


def _sigmoid(x: float) -> float:
    """Return the logistic sigmoid of ``x``, numerically stable for either sign."""
    if x >= 0.0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _posterior(raw: float, floor: float) -> float:
    """Return the engine-seam posterior of ``raw`` under an empty-prior floor.

    Mirrors ``CalibratedScorer.posterior(raw, priorMean = max(prior, floor))``
    with no per-category prior, so ``priorMean = max(0, floor) = floor``:

    ``posterior = sigmoid(logit(raw) + logit(floor))``.

    Args:
        raw: The pre-posterior match confidence.
        floor: The absorbing-state prior floor (``priorMean`` with empty priors).

    Returns:
        The posterior probability in ``(0, 1)``.
    """
    return _sigmoid(_logit(raw) + _logit(floor))


def _percentiles(values: list[float]) -> dict[str, float | None]:
    """Return the ``_PERCENTILES`` of a sorted copy of ``values``.

    Uses the nearest-rank method on the sorted sample (deterministic, no
    interpolation library). An empty input yields every percentile as
    ``None`` so the emitted shape is stable regardless of support.

    Args:
        values: Raw scores for one class within one family.

    Returns:
        A dict keyed by stringified percentile (``"5"`` .. ``"95"``).
    """
    if not values:
        return {str(p): None for p in _PERCENTILES}
    ordered = sorted(values)
    n = len(ordered)
    out: dict[str, float | None] = {}
    for p in _PERCENTILES:
        # Nearest-rank: rank = ceil(p/100 * n), 1-based, clamped into range.
        rank = math.ceil((p / 100.0) * n)
        idx = min(max(rank, 1), n) - 1
        out[str(p)] = ordered[idx]
    return out


def _summ(values: list[float]) -> dict[str, float | int | None]:
    """Return count / min / mean / max of ``values`` (None extrema when empty)."""
    if not values:
        return {"count": 0, "min": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "mean": math.fsum(values) / len(values),
        "max": max(values),
    }


def _family_headroom(
    *,
    cutoff: float | None,
    floor: float,
    positives: list[float],
    fp_scores: list[float],
) -> dict[str, Any]:
    """Compute the M9 headroom block for one family.

    Args:
        cutoff: The family's balanced cutoff, or ``None`` if the harness omitted
            it (no cutoff defined for this kind). When ``None``, the
            above/below split is reported as zero and ``cutoff`` is emitted as
            ``null`` -- the family is still summarized so the shape is stable.
        floor: The absorbing-state prior floor.
        positives: Raw scores of ``gt_class == "positive"`` rows.
        fp_scores: Raw scores of ``gt_class in {"none", "suppress"}`` rows.

    Returns:
        A JSON-serializable dict: cutoff, the four above/below counts, the
        suppressible FP mass, the FP-vs-TP score gap, and posterior / raw-
        percentile summaries.
    """
    if cutoff is None:
        fp_above = fp_below = tp_above = tp_below = 0
    else:
        fp_above = sum(1 for s in fp_scores if s >= cutoff)
        fp_below = len(fp_scores) - fp_above
        tp_above = sum(1 for s in positives if s >= cutoff)
        tp_below = len(positives) - tp_above

    # Suppressible FP mass = FP that currently clear the cutoff (i.e. would
    # surface today). A learned term has headroom to remove these iff they sit
    # below the TP mass -- captured by the score gap below.
    suppressible_fp_mass = fp_above

    # Score gap: lowest TP score minus highest FP score. Positive => the
    # classes are linearly separable on the raw axis (clean headroom);
    # negative => they overlap (a learned term must trade off). None when
    # either class is empty.
    min_tp = min(positives) if positives else None
    max_fp = max(fp_scores) if fp_scores else None
    score_gap = (min_tp - max_fp) if (min_tp is not None and max_fp is not None) else None

    posterior_summary = {
        "floor": floor,
        "cutoff_posterior": (None if cutoff is None else _posterior(cutoff, floor)),
        "positive": _summ([_posterior(s, floor) for s in positives]),
        "false_positive": _summ([_posterior(s, floor) for s in fp_scores]),
    }
    raw_percentiles = {
        "positive": _percentiles(positives),
        "false_positive": _percentiles(fp_scores),
    }

    return {
        "cutoff": cutoff,
        "fp_above_cutoff": fp_above,
        "fp_below_cutoff": fp_below,
        "tp_above": tp_above,
        "tp_below": tp_below,
        "suppressible_fp_mass": suppressible_fp_mass,
        "score_gap": score_gap,
        "positive_count": len(positives),
        "fp_count": len(fp_scores),
        "posterior_summary": posterior_summary,
        "raw_percentiles": raw_percentiles,
    }


def build_headroom(raw_scores_payload: dict[str, Any]) -> dict[str, Any]:
    """Derive the per-family M9 learned-term headroom probe from raw scores.

    Args:
        raw_scores_payload: The parsed ``_raw_scores.json`` payload
            (CONTRACT.md File 2): ``rows`` (each ``{category, doctype, bucket,
            raw, gt_class}``), ``balanced_cutoffs`` (family -> cutoff; families
            absent from the map have no cutoff), and ``absorbing_state_floor``.

    Returns:
        A JSON-serializable dict matching ``schemas/g8_headroom.schema.json``:
        ``schema_version``, ``generated_by``, ``absorbing_state_floor``, and
        ``per_family`` keyed by category, each carrying the headroom block from
        :func:`_family_headroom`.

    Raises:
        KeyError: If ``raw_scores_payload`` lacks ``rows`` or
            ``absorbing_state_floor``, or a row is missing a required field
            (fail loud).
    """
    rows: list[dict[str, Any]] = raw_scores_payload["rows"]
    floor = float(raw_scores_payload["absorbing_state_floor"])
    cutoffs: dict[str, Any] = raw_scores_payload.get("balanced_cutoffs", {})

    positives_by_family: dict[str, list[float]] = {}
    fp_by_family: dict[str, list[float]] = {}
    families: set[str] = set(cutoffs)

    for row in rows:
        family = str(row["category"])
        families.add(family)
        raw = float(row["raw"])
        gt_class = str(row["gt_class"])
        if gt_class == _GT_POSITIVE:
            positives_by_family.setdefault(family, []).append(raw)
        elif gt_class in _FP_CLASSES:
            fp_by_family.setdefault(family, []).append(raw)
        # Any other gt_class label is out of contract; ignore rather than
        # miscount it into either mass. (The contract enumerates exactly three.)
        elif gt_class not in {_GT_POSITIVE, _GT_NONE, _GT_SUPPRESS}:
            logger.warning("ignoring row with out-of-contract gt_class %r", gt_class)

    per_family = {
        family: _family_headroom(
            cutoff=(None if cutoffs.get(family) is None else float(cutoffs[family])),
            floor=floor,
            positives=positives_by_family.get(family, []),
            fp_scores=fp_by_family.get(family, []),
        )
        for family in sorted(families)
    }

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "absorbing_state_floor": floor,
        "per_family": per_family,
    }


__all__ = ["build_headroom"]
