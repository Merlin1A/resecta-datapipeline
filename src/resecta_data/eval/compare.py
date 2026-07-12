"""Compare two derived G8 detection baselines and decide the §3 predicate.

The S4 measurement design (``04-implementation-plan.md`` §5.2, restated in
``05-final-plan.md`` §3) decides whether a context-scorer AFTER run improves on
the S3 BEFORE purely from the two derived ``g8_detection_baseline.json`` dicts
the harness + :mod:`resecta_data.eval.baseline` already produce. This module is
that decision: **pure arithmetic over the already-frozen ``BaselineCell``
fields** -- it does not re-run detection, re-join offsets, read any device
score-dump, or re-derive any metric. It reads the fields
:mod:`resecta_data.eval.baseline` emits (``precision``, ``recall``,
``precision_with_decoys``, ``family_false_positive_count``, ``support_n``,
``low_confidence``) and applies the four-clause predicate per scorer family and
over the grand-total aggregate.

Each clause carries TWO senses so one comparator decides both questions: a
``win`` sense (the §3 improvement bar is met) and a ``regressed`` sense (the
metric got worse beyond float noise). Failing to *improve* is NOT a regression
-- so an identical BEFORE/AFTER is ``regression=False`` (a clean non-regression)
even though no uplift was achieved. The four clauses (AFTER vs BEFORE), per
family ``F`` and over ``totals``:

- **C1 precision** -- win: ``after.precision >= before.precision + delta_p``;
  regressed: precision dropped.
- **C2 family-FPR** -- win: ``after_FPR <= before_FPR * (1 - delta_f_rel)``;
  regressed: ``after_FPR > before_FPR`` -- where ``family_FPR =
  1 - precision_with_decoys`` (NOT ``1 - precision``: the two coincide on G8
  today only because zero decoys fire for the five families, but the code reads
  ``precision_with_decoys`` so it stays correct once a corpus adds decoys).
- **C3 recall floor** -- win and regressed both key on
  ``after.recall >= before.recall - eps`` (the over-suppression guard).
- **C4 slice non-regression** -- regressed: any ``per_doctype`` (5) or
  ``per_demographic`` (5) precision drops by more than ``delta_slice``.

A REGRESSION is **any** gating clause regressing. An aggregate clean verdict
never excuses a per-family regression. The five scorer families map to
``per_family`` keys by the
D-5 rule (the per_family block is keyed by ``PIICategory`` rawValue):
``account -> "account"``, ``phone -> "phone"``, ``mrn -> "medicalrecord"``,
``ein -> "ein"``, ``itin -> "itin"``. MRN/EIN are clean on G8 (zero FP) -> their
bar is **non-regression only** (no C1 uplift is demanded). ITIN is absent from
the G8 panel -> tolerated with no ``KeyError`` and marked off-panel.

Units: ``delta_p`` and ``delta_slice`` are precision **fractions** here (e.g.
``0.05`` for 5 points); the CLI accepts them in points and converts. ``eps`` and
``delta_f_rel`` are fractions throughout.

This is a dev/eval comparator: it has no install route, no lock entry, and is
not produced by ``make build``. It follows the pipeline's determinism
(``common/determinism.py``) and mechanism-language
(``common/mechanism_language.py``) rules; ARCH §12.2 (every emitted record
carries category / aggregate / mechanism only -- no document text, no PII
values, no coordinates).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from resecta_data.common.io import sha256_bytes
from resecta_data.common.mechanism_language import assert_safe

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.eval.compare"
_SCHEMA_VERSION: Final[int] = 1
_METRIC: Final[str] = "g8_compare_verdict"

# The five scorer families and the per_family key each resolves to. The
# per_family block is keyed by PIICategory rawValue (the D-5 trap): MRN's
# rawValue is "medicalrecord", NOT "mrn", so reading per_family["mrn"] would
# silently look absent and skip MRN's non-regression check -- a bug. Keyed by
# the scorer/wireName family name -> the baseline per_family key.
_FAMILY_KEY: Final[dict[str, str]] = {
    "account": "account",
    "phone": "phone",
    "mrn": "medicalrecord",
    "ein": "ein",
    "itin": "itin",
}

# The scorer family-evaluation order (stable, deterministic emission order).
_FAMILY_ORDER: Final[tuple[str, ...]] = ("account", "phone", "mrn", "ein", "itin")

# The aggregate's reporting name in the verdict.
_AGGREGATE_NAME: Final[str] = "aggregate"

# The 5 doctype + 5 demographic slice axes the C4 non-regression clause walks.
# Literal names: ai_an carries an underscore and is NOT re-split (it is a single
# bucket key, mirroring baseline._BUCKETS / the input schema's required keys).
_DOCTYPES: Final[tuple[str, ...]] = ("court", "medical", "financial", "foia", "generic")
_BUCKETS: Final[tuple[str, ...]] = ("white", "black", "hispanic", "asian", "ai_an")

# Clause identifiers carried in the verdict so a failing clause is named.
_C1: Final[str] = "C1_precision"
_C2: Final[str] = "C2_family_fpr"
_C3: Final[str] = "C3_recall"
_C4: Final[str] = "C4_slice_non_regression"

# Float-noise tolerance: a movement smaller than this is treated as "no change"
# for the C1/C2 regression sense, so re-derived-identical inputs never read as a
# regression on rounding dust. The metrics are ratios of small integers, so this
# is comfortably below any real one-detection movement.
_FLOAT_TOL: Final[float] = 1e-12


def _family_fpr(cell: dict[str, Any]) -> float:
    """Return the GT-label-keyed family false-positive rate of a cell.

    ``family_FPR = 1 - precision_with_decoys`` -- the decoy-inclusive FP rate.
    Reading ``precision_with_decoys`` (not ``precision``) keeps the clause
    correct when a corpus adds adversarial-suppression decoys; on G8 the two
    coincide because zero of the five families' decoys fire.

    Args:
        cell: An aggregate cell carrying ``precision_with_decoys``.

    Returns:
        ``1 - precision_with_decoys`` as a float in ``[0, 1]``.
    """
    return 1.0 - float(cell["precision_with_decoys"])


def _clause_c1(before: dict[str, Any], after: dict[str, Any], delta_p: float) -> dict[str, Any]:
    """Evaluate C1 (precision), carrying both the WIN and the regression sense.

    Two senses, one clause:

    - ``win`` -- precision rose by at least ``delta_p`` (the §5.2 uplift bar).
    - ``regressed`` -- precision *dropped* (``after < before``, beyond a tiny
      float-noise tolerance). Failing to improve is NOT a regression; only a
      drop is. So an identical BEFORE/AFTER is ``regressed=False`` (and
      ``win=False`` unless ``delta_p`` is 0).
    """
    before_p = float(before["precision"])
    after_p = float(after["precision"])
    delta = after_p - before_p
    return {
        "clause": _C1,
        "win": delta >= delta_p,
        "regressed": delta < -_FLOAT_TOL,
        "before": before_p,
        "after": after_p,
        "delta": delta,
        "win_threshold": delta_p,
    }


def _clause_c2(before: dict[str, Any], after: dict[str, Any], delta_f_rel: float) -> dict[str, Any]:
    """Evaluate C2 (family-FPR), carrying both the WIN and the regression sense.

    Reads ``precision_with_decoys`` via :func:`_family_fpr` (NOT ``precision``)
    so the clause stays correct when a corpus adds decoys. Two senses:

    - ``win`` -- FPR cut to at most ``before_FPR * (1 - delta_f_rel)``.
    - ``regressed`` -- FPR got *worse* (``after_FPR > before_FPR``, beyond
      float-noise). A 0-FP family stays at 0 -> not regressed and not a spurious
      FPR-improvement failure; a family that GROWS FP (the MRN-grows-FP
      tripwire) has ``after_FPR > 0 = before_FPR`` -> regressed.
    """
    before_fpr = _family_fpr(before)
    after_fpr = _family_fpr(after)
    win_target = before_fpr * (1.0 - delta_f_rel)
    return {
        "clause": _C2,
        "win": after_fpr <= win_target,
        "regressed": after_fpr > before_fpr + _FLOAT_TOL,
        "before": before_fpr,
        "after": after_fpr,
        "win_threshold": win_target,
        "delta_f_rel": delta_f_rel,
    }


def _clause_c3(before: dict[str, Any], after: dict[str, Any], eps: float) -> dict[str, Any]:
    """Evaluate C3 (recall), carrying both the WIN and the regression sense.

    The over-suppression guard -- the augment can only lower confidence, so this
    is the dangerous clause. Both senses share one floor here:

    - ``win`` and ``regressed`` both key on ``after >= before - eps``: recall
      below the floor is a regression, recall at/above it satisfies the WIN's
      recall constraint. (Identity: equal -> floor met -> not regressed.)
    """
    before_r = float(before["recall"])
    after_r = float(after["recall"])
    floor = before_r - eps
    floor_met = after_r >= floor
    return {
        "clause": _C3,
        "win": floor_met,
        "regressed": not floor_met,
        "before": before_r,
        "after": after_r,
        "win_threshold": floor,
        "eps": eps,
    }


def _slice_regressions(
    before: dict[str, Any],
    after: dict[str, Any],
    axis_keys: tuple[str, ...],
    axis_label: str,
    delta_slice: float,
) -> list[dict[str, Any]]:
    """Return the per-slice precision deltas for one axis (doctype or demographic).

    A slice is a regression when ``after.precision < before.precision -
    delta_slice``. Every slice is reported (passed or not) so the verdict shows
    the full axis; the C4 aggregation flags the regressing ones.

    Args:
        before: The BEFORE baseline dict.
        after: The AFTER baseline dict.
        axis_keys: The literal slice names (``_DOCTYPES`` or ``_BUCKETS``); the
            underscore-bearing ``ai_an`` key is used as-is, never re-split.
        axis_label: ``"doctype"`` or ``"demographic"`` for the record.
        delta_slice: Max tolerated precision drop, a fraction.

    Returns:
        One record per slice, in ``axis_keys`` order.
    """
    block_key = "per_doctype" if axis_label == "doctype" else "per_demographic"
    before_block = before[block_key]
    after_block = after[block_key]
    records: list[dict[str, Any]] = []
    for name in axis_keys:
        before_p = float(before_block[name]["precision"])
        after_p = float(after_block[name]["precision"])
        delta = after_p - before_p
        records.append(
            {
                "axis": axis_label,
                "slice": name,
                "before": before_p,
                "after": after_p,
                "delta": delta,
                # Regression when the drop exceeds the tolerance.
                "regressed": delta < -delta_slice,
            }
        )
    return records


def _clause_c4(
    before: dict[str, Any],
    after: dict[str, Any],
    delta_slice: float,
) -> dict[str, Any]:
    """Evaluate C4 (per-doctype/per-demographic slice non-regression).

    A slice that drops more than ``delta_slice`` is a regression; C4 is a pure
    non-regression clause, so its ``win`` and ``regressed`` senses are the
    inverse of each other (no uplift is demanded of a slice).
    """
    doctype_records = _slice_regressions(before, after, _DOCTYPES, "doctype", delta_slice)
    demographic_records = _slice_regressions(before, after, _BUCKETS, "demographic", delta_slice)
    all_records = doctype_records + demographic_records
    regressed = [r for r in all_records if r["regressed"]]
    return {
        "clause": _C4,
        "win": not regressed,
        "regressed": bool(regressed),
        "win_threshold": delta_slice,
        "slices": all_records,
        "regressed_slices": [f"{r['axis']}:{r['slice']}" for r in regressed],
    }


def _family_verdict(
    name: str,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    delta_p: float,
    delta_f_rel: float,
    eps: float,
    delta_slice: float,
) -> dict[str, Any]:
    """Build the four-clause verdict for one scorer family or the aggregate.

    ``non_regression_only`` families (MRN/EIN -- clean on G8, zero FP) drop the
    C1 uplift demand: C1 is reported but does not gate, so a clean family is not
    misread as a failure for "not improving". The C2/C3/C4 non-regression
    clauses still bind (a family that GROWS FP or DROPS recall regresses).

    Args:
        name: Family or aggregate reporting name.
        before: The BEFORE aggregate cell (its ``per_family[key]`` or
            ``totals``).
        after: The AFTER aggregate cell.
        delta_p: Min precision uplift (fraction); ignored when
            non-regression-only.
        delta_f_rel: Min relative FPR cut (fraction).
        eps: Recall floor slack (fraction).
        delta_slice: Per-slice precision tolerance (fraction); only the
            aggregate-level verdict consults the slice block (slices are a
            whole-run guard, not per-family).

    Returns:
        A verdict record with each clause (carrying both senses), the
        ``regression`` and ``win`` flags, the named ``regressed_clauses``, and
        the carried ``low_confidence`` advisory.
    """
    # MRN/EIN: clean on G8 -> non-regression bar only (no +delta_p demanded).
    # For these the C1 PRECISION clause does not gate AT ALL: a degraded-
    # precision MRN with still-0 FP is NOT flagged (per the plan), but a C2 FP
    # growth or a C3 recall drop still gates.
    non_regression_only = name in ("mrn", "ein")

    c1 = _clause_c1(before, after, delta_p)
    c2 = _clause_c2(before, after, delta_f_rel)
    c3 = _clause_c3(before, after, eps)
    clauses = [c1, c2, c3]

    # C1's REGRESSION sense gates only for the uplift-required families; C2/C3
    # always gate. (C1.regressed = precision dropped; suppressed for MRN/EIN.)
    c1_gates = not non_regression_only
    regressed_clauses = [
        clause["clause"]
        for clause in clauses
        if clause["regressed"] and (clause is not c1 or c1_gates)
    ]
    regression = bool(regressed_clauses)

    # WIN: uplift-required families need every clause's win sense; non-
    # regression-only families "win" by simply not regressing (no uplift bar).
    win = (not regression) if non_regression_only else all(clause["win"] for clause in clauses)

    return {
        "name": name,
        "non_regression_only": non_regression_only,
        "regression": regression,
        "win": win,
        "regressed_clauses": regressed_clauses,
        "clauses": clauses,
        # Advisory only -- carried through, never gated (low-support reporting,
        # not a hard gate). True if EITHER side flags the slice low-support.
        "low_confidence": bool(before.get("low_confidence")) or bool(after.get("low_confidence")),
    }


def build_compare(
    before: dict[str, Any],
    after: dict[str, Any],
    thresholds: dict[str, float],
) -> dict[str, Any]:
    """Decide the §3 predicate from two derived G8 detection baselines.

    Pure arithmetic over the frozen ``BaselineCell`` fields. Evaluates the four
    clauses per scorer family (mapped by the D-5 rule) and over the grand-total
    aggregate, then folds them into one verdict. REGRESSION is any gating clause
    failing on any family or the aggregate; an aggregate PASS never excuses a
    per-family FAIL.

    Args:
        before: The parsed derived BEFORE ``g8_detection_baseline.json`` dict
            (NOT ``*_cells.json``).
        after: The parsed derived AFTER ``g8_detection_baseline.json`` dict.
        thresholds: A dict with float keys ``delta_p`` / ``delta_f_rel`` /
            ``eps`` / ``delta_slice`` (``delta_p`` and ``delta_slice`` as
            precision FRACTIONS, e.g. ``0.05``).

    Returns:
        A JSON-serializable verdict dict matching
        ``schemas/g8_compare_verdict.schema.json``. Includes both input
        ``before_sha256`` / ``after_sha256`` provenance digests and the overall
        ``regression`` flag.

    Raises:
        KeyError: If a required field is missing from a baseline (fail loud --
            a malformed baseline is a contract violation).
    """
    delta_p = float(thresholds["delta_p"])
    delta_f_rel = float(thresholds["delta_f_rel"])
    eps = float(thresholds["eps"])
    delta_slice = float(thresholds["delta_slice"])

    before_sha = sha256_bytes(_canonical_bytes(before))
    after_sha = sha256_bytes(_canonical_bytes(after))

    before_families: dict[str, Any] = before["per_family"]
    after_families: dict[str, Any] = after["per_family"]

    family_verdicts: list[dict[str, Any]] = []
    for family in _FAMILY_ORDER:
        key = _FAMILY_KEY[family]
        before_cell = before_families.get(key)
        after_cell = after_families.get(key)
        if before_cell is None or after_cell is None:
            # ITIN is absent from the G8 panel (no GT). Tolerate with no
            # KeyError; mark off-panel rather than fabricating a verdict.
            family_verdicts.append(
                {
                    "name": family,
                    "absent": True,
                    "note": "off-G8-panel (no per_family entry); not evaluated",
                    "regression": False,
                }
            )
            continue
        family_verdicts.append(
            _family_verdict(
                family,
                before_cell,
                after_cell,
                delta_p=delta_p,
                delta_f_rel=delta_f_rel,
                eps=eps,
                delta_slice=delta_slice,
            )
        )

    # The aggregate verdict gates on C1/C2/C3 over totals AND on the C4 slice
    # non-regression guard (slices are a whole-run axis, reported once here).
    aggregate = _family_verdict(
        _AGGREGATE_NAME,
        before["totals"],
        after["totals"],
        delta_p=delta_p,
        delta_f_rel=delta_f_rel,
        eps=eps,
        delta_slice=delta_slice,
    )
    c4 = _clause_c4(before, after, delta_slice)
    aggregate["clauses"].append(c4)
    if c4["regressed"]:
        aggregate["regressed_clauses"].append(c4["clause"])
        aggregate["regression"] = True
    # The aggregate WIN also requires the slice non-regression clause.
    aggregate["win"] = bool(aggregate["win"]) and bool(c4["win"])

    # Overall regression: ANY family OR the aggregate regressed. An aggregate
    # PASS never excuses a per-family FAIL, and vice-versa.
    any_family_regression = any(fv.get("regression") for fv in family_verdicts)
    overall_regression = any_family_regression or bool(aggregate["regression"])

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "metric": _METRIC,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "thresholds": {
            "delta_p": delta_p,
            "delta_f_rel": delta_f_rel,
            "eps": eps,
            "delta_slice": delta_slice,
        },
        "regression": overall_regression,
        "families": family_verdicts,
        "aggregate": aggregate,
    }

    # The only free-form string this builder surfaces is the off-panel note;
    # guard it (and any future note) before returning. Mechanism-language only.
    for fv in family_verdicts:
        note = fv.get("note")
        if note is not None:
            assert_safe(note, context="eval.compare family note")

    verdict_word = "regression" if overall_regression else "no-regression"
    logger.info(
        "eval compare: %s (families=%d, aggregate regression=%s)",
        verdict_word,
        len(family_verdicts),
        aggregate["regression"],
    )

    return payload


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Return the canonical-JSON byte encoding of ``payload`` for hashing.

    Mirrors the serialization parameters of
    :func:`resecta_data.common.io.dump_canonical_json` (sorted keys, indent 2,
    the canonical separators, ``ensure_ascii=False``, trailing newline) so each
    input's provenance digest is invariant to upstream whitespace / key-order
    churn: an unchanged baseline yields an unchanged ``*_sha256``.
    """
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=False,
    )
    return encoded.encode("utf-8") + b"\n"


__all__ = ["build_compare"]
