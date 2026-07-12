"""Sweep detection thresholds to produce calibrated preset vectors.

Phase 3b consumer of Swift-side detector score dumps. Two-stage flow (0.4):
``build()`` produces ``preset_thresholds_sweep_raw.json`` (status=sweep_raw)
for inspection; ``finalize()`` promotes a reviewed sweep_raw artifact to the
shipping ``preset_thresholds.json`` (status=calibrated) that replaces the
Phase 3 ``_candidates`` placeholder. The sweep never writes the shipping
file.

Algorithm per plan:
  1. Stratified 70/30 split over (doctype, demographic_bucket), matching
     fit_temperature. Priors come from the 70% train split, evaluation
     from the 30% held-out split.
  2. A7 composition: posterior = sigmoid(logit(raw_score) + logit(prior))
     where ``prior`` is the category-in-doctype ground-truth density on
     the train split. If the Swift side set ``doctype_prior_applied``
     on a candidate, its ``raw_score`` is already the posterior.
  3. Per-category two-phase sweep: 51 coarse points at 0.02, then 19
     refinement points at 0.002 around the coarse argmax.
  4. Per-preset selection rule:
       conservative: max F1 s.t. precision >= 0.95
       balanced:     max F1
       aggressive:   max F1 s.t. recall >= 0.95
     Fallbacks apply if no point meets the constraint.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json
from resecta_data.common.mechanism_language import assert_safe
from resecta_data.common.schema import validate_file

from ._calibration_io import (
    assert_corpus_seed_matches,
    assert_doc_ids_cover_corpus,
    load_corpus,
    load_score_dump,
    load_temperature,
)
from .fit_temperature import _split_indices

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.classifier.sweep_thresholds"
_SCHEMA_VERSION: Final[int] = 1

_CATEGORIES: Final[tuple[str, ...]] = (
    "ssn",
    "npi",
    "dea",
    "dob",
    "address",
    "account",
    "mrn",
    "name",
    # search-impl S2 (design 01 §4): ABA routing number. NOTE: the May-7
    # Swift score dumps predate this category — a sweep against them sees
    # an empty candidate grid for it (degenerate selection, then clamped).
    # The S4 runbook re-dumps from Swift before the real calibration run.
    "routingNumber",
)

_PRESET_NAMES: Final[tuple[str, ...]] = ("conservative", "balanced", "aggressive")

# Two-phase grid parameters. Coarse step 0.02 over [0, 1] gives 51 points;
# refinement step 0.002 around the coarse argmax gives 19 points over a
# 0.036 window. These match the Phase 3b spec.
_COARSE_STEPS: Final[int] = 51
_COARSE_STEP: Final[float] = 0.02
_REFINE_STEPS: Final[int] = 19
_REFINE_STEP: Final[float] = 0.002

_CONSERVATIVE_PRECISION_FLOOR: Final[float] = 0.95
_AGGRESSIVE_RECALL_FLOOR: Final[float] = 0.95

# 3.3: floor on the balanced preset's recall. In a degenerate corpus (no
# positives, or every candidate a false positive) the balanced F1 optimum
# ties at 0 and the tie-break-to-larger picks an unreachably high threshold
# — the exact path that produced the dead name=0.98 cutoff. When no grid
# point meets this floor, balanced falls back to the aggressive value.
_BALANCED_RECALL_FLOOR: Final[float] = 0.70

# Valid values for the prior-composition mode (3.1). See
# _compose_posterior_with_mode for semantics.
_PRIOR_MODES: Final[tuple[str, ...]] = ("corpus", "fresh", "mixed")

_IOU_THRESHOLD: Final[float] = 0.5

# Laplace smoothing for per-doctype priors so zero-incidence doesn't produce
# logit(0) = -inf. Small smoothing preserves signal while avoiding singularities.
_PRIOR_SMOOTHING: Final[float] = 1.0

_PRIOR_CLAMP_MIN: Final[float] = 1.0e-6
_PRIOR_CLAMP_MAX: Final[float] = 1.0 - 1.0e-6

# Verified 2026-06-10; S2 ceilings updated 2026-06-11. Each value is the
# physical maximum a live detector can emit. These are NOT thresholds —
# they are envelope bounds used to clamp the sweep output so calibrated
# values can never exceed max - _FEASIBILITY_MARGIN.  Update this table
# whenever a WS1 detector change raises a category's ceiling.
#
# Address note: S2 item 1.6 routes spatial addresses (assembler max 0.80)
# through resolveOverlaps + posterior + W4, so the envelope is the spatial
# max 0.80 (was the 0.70 regex max while spatial bypassed the gate).
#
# DOB note: S2 item 1.1 (D4) shipped the label-anchored financial path
# (PIIDetector.detectDOBs emits fixed 0.85), raising the ceiling from the
# 0.50 DOBDetector textual max.
#
# The 8 ungated categories (EIN, ITIN, CC, email, phone, DL, passport,
# plate) are not in the current _CATEGORIES tuple. They are pre-populated
# here for when WS2 item 1.7 adds them to presets.  'gated-as-of' = None
# means "not yet in the W4 gate."
_DETECTOR_ACHIEVABLE_MAX: Final[dict[str, float]] = {
    # Currently gated (9 categories in W4)
    "ssn": 0.95,  # SSNContextKeywords.swift:18-56 boostedConfidence
    "name": 0.85,  # PIIDetector.swift:1109 = 0.70 + max boost 0.15
    "mrn": 0.92,  # MRNContextKeywords.swift:15-48
    "npi": 0.90,  # NPIDetector.swift:20-27
    "dea": 0.90,  # DEADetector.swift:20-27
    "account": 0.75,  # AccountDetector.swift:33-74 via scorer profile
    "address": 0.80,  # AddressSpatialAssembler.swift max 0.80 (S2 item 1.6
    #                   routes spatial through the W4 gate)
    "dob": 0.85,  # PIIDetector.detectDOBs() fixed 0.85 (S2 item 1.1, D4
    #               label-anchored financial path)
    "routingNumber": 0.88,  # RoutingNumberDetector.swift boostedConfidence
    #                         (S2 item 1.8)
    # Not yet gated (WS2-1.7 will add these; pre-populated for that PR)
    "ein": 0.85,  # PIIDetector.swift EIN path (no wireName yet)
    "itin": 0.85,  # PIIDetector.swift ITIN path (no wireName yet)
    "creditCard": 0.95,  # PIIDetector.swift Luhn path
    "email": 0.90,  # PIIDetector.swift email path
    "phone": 0.80,  # PIIDetector.swift US phone path
    "driversLicense": 0.80,  # PIIDetector.swift DL path
    "passport": 0.80,  # PIIDetector.swift passport path
    "licensePlate": 0.88,  # PIIDetector.swift plate path
}

# Margin below achievable max.  Swept value is clamped to max - margin.
# This is NOT a threshold — it is a safety margin.
_FEASIBILITY_MARGIN: Final[float] = 0.05

_NOTES: Final[str] = (
    "Preset threshold vectors produced by a two-phase grid sweep over "
    "posterior scores on a 30% held-out G8 split. Priors for A7 composition "
    "are computed from the 70% train split. The conservative preset is "
    "selected to meet a precision floor; the aggressive preset is designed "
    "to meet a recall floor; balanced maximises F1. Posterior = "
    "sigmoid(logit(raw_score) + logit(prior))."
)


def _logit(p: float) -> float:
    p = min(max(p, _PRIOR_CLAMP_MIN), _PRIOR_CLAMP_MAX)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Char-offset IoU for two half-open intervals."""
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return 0.0
    inter = hi - lo
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def _positive_spans_by_doc(
    corpus: dict[str, Any],
) -> dict[tuple[str, str], list[tuple[int, int]]]:
    """Map (doc_id, category) -> list of redact-worthy ground-truth spans.

    Only spans with ``expected_outcome == "redact"`` count as positives;
    adversarial ``suppress`` spans are intentional decoys and count as
    neither truth nor hit. ``flag`` is not a positive either — it's a
    routing decision, not a detection target.
    """
    out: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for doc in corpus["documents"]:
        for span in doc["pii_spans"]:
            if span.get("expected_outcome", "redact") != "redact":
                continue
            category = span["category"]
            if category not in _CATEGORIES:
                continue
            out.setdefault((doc["id"], category), []).append((span["start"], span["end"]))
    return out


def _priors_from_train(
    corpus: dict[str, Any],
    train_ids: frozenset[str],
) -> dict[tuple[str, str], float]:
    """Per-(doctype, category) prior = smoothed fraction of train docs containing a redact span."""
    train_docs = [d for d in corpus["documents"] if d["id"] in train_ids]
    by_doctype: dict[str, list[dict[str, Any]]] = {}
    for doc in train_docs:
        by_doctype.setdefault(doc["doctype"], []).append(doc)

    priors: dict[tuple[str, str], float] = {}
    for doctype, docs in by_doctype.items():
        n = len(docs)
        if n == 0:
            for category in _CATEGORIES:
                priors[(doctype, category)] = _PRIOR_CLAMP_MIN
            continue
        for category in _CATEGORIES:
            hits = sum(
                1
                for d in docs
                if any(
                    s["category"] == category and s.get("expected_outcome", "redact") == "redact"
                    for s in d["pii_spans"]
                )
            )
            smoothed = (hits + _PRIOR_SMOOTHING) / (n + 2.0 * _PRIOR_SMOOTHING)
            priors[(doctype, category)] = smoothed
    return priors


def _compose_posterior(raw_score: float, prior: float, prior_already_applied: bool) -> float:
    if prior_already_applied:
        return min(max(raw_score, 0.0), 1.0)
    return _sigmoid(_logit(raw_score) + _logit(prior))


def _compose_posterior_with_mode(
    raw_score: float,
    prior: float,
    prior_already_applied: bool,
    prior_mode: str,
) -> float:
    """Compute the sweep posterior under the selected prior mode (3.1).

    corpus: Use the corpus-derived train-split prior (original behavior).
        Calibrates to the training distribution; inflated priors produce
        degenerate thresholds (the name=0.98 path).
    fresh:  Ignore the corpus prior; use neutral 0.5, so posterior equals
        raw_score. Matches the actual W4 gate behavior on the first scan of
        a new document (fresh prior mean = 0.5).
    mixed:  Average of the corpus and fresh posteriors. A hedge that
        captures both in-session and first-page behavior.

    Args:
        raw_score: Raw detector score from the Swift dump.
        prior: Corpus-derived prior; consulted only in corpus/mixed mode.
        prior_already_applied: True when the Swift side already composed the
            prior into raw_score; the score passes through clamped.
        prior_mode: One of ``_PRIOR_MODES``.

    Returns:
        Posterior in [0, 1].

    Raises:
        PipelineError: If ``prior_mode`` is not one of ``_PRIOR_MODES``.
    """
    if prior_already_applied:
        return min(max(raw_score, 0.0), 1.0)
    if prior_mode == "fresh":
        return _sigmoid(_logit(raw_score) + _logit(0.5))  # logit(0.5)=0 → raw_score
    if prior_mode == "corpus":
        return _compose_posterior(raw_score, prior, prior_already_applied=False)
    if prior_mode == "mixed":
        fresh_post = _sigmoid(_logit(raw_score))
        corpus_post = _compose_posterior(raw_score, prior, prior_already_applied=False)
        return (fresh_post + corpus_post) / 2.0
    raise PipelineError(f"Unknown prior_mode: {prior_mode!r}; expected one of {_PRIOR_MODES}")


def _build_candidate_rows(
    score_dump: dict[str, Any],
    corpus: dict[str, Any],
    priors: dict[tuple[str, str], float],
    eval_ids: frozenset[str],
    prior_mode: str,
) -> dict[str, list[tuple[str, int, int, float, bool]]]:
    """Return {category: [(doc_id, start, end, posterior, matched_truth)]}."""
    doctype_by_id = {doc["id"]: doc["doctype"] for doc in corpus["documents"]}
    positive_spans = _positive_spans_by_doc(corpus)

    rows: dict[str, list[tuple[str, int, int, float, bool]]] = {c: [] for c in _CATEGORIES}

    for entry in score_dump["documents"]:
        doc_id = entry["doc_id"]
        if doc_id not in eval_ids:
            continue
        doctype = doctype_by_id[doc_id]
        for cand in entry["candidates"]:
            category = cand["category"]
            if category not in _CATEGORIES:
                continue
            raw = float(cand["raw_score"])
            prior_applied = bool(cand.get("doctype_prior_applied", False))
            posterior = _compose_posterior_with_mode(
                raw, priors[(doctype, category)], prior_applied, prior_mode
            )
            cand_span = (int(cand["start"]), int(cand["end"]))
            truths = positive_spans.get((doc_id, category), [])
            matched = any(_iou(cand_span, t) >= _IOU_THRESHOLD for t in truths)
            rows[category].append((doc_id, cand_span[0], cand_span[1], posterior, matched))
    return rows


def _total_positives_by_category(
    corpus: dict[str, Any],
    eval_ids: frozenset[str],
) -> dict[str, int]:
    """Number of redact-worthy ground-truth spans per category on the eval split."""
    counts = dict.fromkeys(_CATEGORIES, 0)
    for doc in corpus["documents"]:
        if doc["id"] not in eval_ids:
            continue
        for span in doc["pii_spans"]:
            category = span["category"]
            if category not in _CATEGORIES:
                continue
            if span.get("expected_outcome", "redact") != "redact":
                continue
            counts[category] += 1
    return counts


def _metrics_at(
    rows: list[tuple[str, int, int, float, bool]],
    truth_total: int,
    threshold: float,
) -> tuple[float, float, float]:
    """(precision, recall, f1) at the given posterior threshold."""
    tp = sum(1 for r in rows if r[4] and r[3] >= threshold)
    fp = sum(1 for r in rows if (not r[4]) and r[3] >= threshold)
    fn = max(truth_total - tp, 0)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def _coarse_grid() -> list[float]:
    return [round(i * _COARSE_STEP, 6) for i in range(_COARSE_STEPS)]


def _refine_grid(center: float) -> list[float]:
    half = _REFINE_STEPS // 2
    points = {
        round(min(max(center + (i - half) * _REFINE_STEP, 0.0), 1.0), 6)
        for i in range(_REFINE_STEPS)
    }
    return sorted(points)


def _select_for_category(
    rows: list[tuple[str, int, int, float, bool]],
    truth_total: int,
    category: str,
) -> tuple[dict[str, float], dict[str, tuple[float, float, float]]]:
    """Return ({preset: threshold}, {preset: (P, R, F1)}) for one category."""
    grid: list[float] = sorted(set(_coarse_grid()))
    coarse_scored = [(t, _metrics_at(rows, truth_total, t)) for t in grid]

    # Coarse best-F1 for refinement center.
    coarse_best = max(coarse_scored, key=lambda x: (x[1][2], -x[0]))
    refined_grid = sorted(set(_refine_grid(coarse_best[0])) | set(grid))
    scored = [(t, _metrics_at(rows, truth_total, t)) for t in refined_grid]

    # Aggressive: max F1 subject to recall floor; fallback = max recall.
    # Computed before balanced — the 3.3 degenerate-recall fallback reuses it.
    aggressive_candidates = [s for s in scored if s[1][1] >= _AGGRESSIVE_RECALL_FLOOR]
    if aggressive_candidates:
        aggressive = max(aggressive_candidates, key=lambda x: (x[1][2], -x[0]))
    else:
        aggressive = max(scored, key=lambda x: (x[1][1], -x[0]))

    # Balanced: max F1 subject to the 3.3 recall floor; tie-break to the
    # larger threshold (more conservative). Fallback = aggressive value.
    balanced_candidates = [s for s in scored if s[1][1] >= _BALANCED_RECALL_FLOOR]
    if balanced_candidates:
        balanced = max(balanced_candidates, key=lambda x: (x[1][2], x[0]))
    else:
        # Degenerate recall: the corpus has no positives for this category or
        # every candidate is a false positive. Fall back to the aggressive
        # threshold (minimum viable recall) so the category surfaces at
        # least some detections instead of shipping a dead cutoff.
        logger.warning(
            "Category '%s' has degenerate balanced recall (no threshold meets "
            "recall floor %.2f); falling back to aggressive value. Check the "
            "corpus or score dump for this category.",
            category,
            _BALANCED_RECALL_FLOOR,
        )
        balanced = aggressive

    # Conservative: max F1 subject to precision floor; fallback = max precision.
    conservative_candidates = [s for s in scored if s[1][0] >= _CONSERVATIVE_PRECISION_FLOOR]
    if conservative_candidates:
        conservative = max(conservative_candidates, key=lambda x: (x[1][2], x[0]))
    else:
        conservative = max(scored, key=lambda x: (x[1][0], x[0]))

    thresholds = {
        "conservative": conservative[0],
        "balanced": balanced[0],
        "aggressive": aggressive[0],
    }
    metrics = {
        "conservative": conservative[1],
        "balanced": balanced[1],
        "aggressive": aggressive[1],
    }
    return thresholds, metrics


def _clamp_to_feasible(
    presets: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Clamp each swept threshold to at most achievable_max - _FEASIBILITY_MARGIN.

    Args:
        presets: Unclamped {preset_name: {category: threshold}} mapping.

    Returns:
        New dict with each value clamped.

    Raises:
        PipelineError: If a category in ``presets`` is not listed in
            ``_DETECTOR_ACHIEVABLE_MAX``.
    """
    result: dict[str, dict[str, float]] = {}
    for preset_name, vector in presets.items():
        clamped: dict[str, float] = {}
        for category, value in vector.items():
            if category not in _DETECTOR_ACHIEVABLE_MAX:
                raise PipelineError(
                    f"Category {category!r} has no entry in "
                    f"_DETECTOR_ACHIEVABLE_MAX; update the table before "
                    f"adding it to the sweep."
                )
            ceiling = _DETECTOR_ACHIEVABLE_MAX[category] - _FEASIBILITY_MARGIN
            if value > ceiling:
                logger.warning(
                    "Swept %s/%s=%.4f exceeds feasibility ceiling %.4f; "
                    "clamping. This usually means the corpus prior is "
                    "inflated or the score distribution is degenerate.",
                    preset_name,
                    category,
                    value,
                    ceiling,
                )
            clamped[category] = round(min(value, ceiling), 6)
        result[preset_name] = clamped
    return result


def build(
    seed: int,
    *,
    score_dump_path: Path,
    temperature_path: Path,
    corpus_path: Path,
    schemas_dir: Path,
    prior_mode: str = "fresh",
) -> dict[str, Any]:
    """Return the swept preset_thresholds payload (status="sweep_raw").

    Args:
        seed: Master seed. Used for the deterministic 70/30 split.
        score_dump_path: Swift-produced per-candidate score dump.
        temperature_path: Fitted-temperature artifact from fit_temperature.
            Loaded for provenance only in this placeholder composition
            (the A7 prior composition uses raw_score + prior directly;
            the doctype temperature applies to the 5-class softmax, not
            the per-PII-category score). Recorded in notes/metadata.
        corpus_path: The G8 corpus the dump was produced against.
        schemas_dir: Directory holding ``*.schema.json`` files.
        prior_mode: Prior composition mode (3.1): "fresh" (neutral 0.5
            prior, matches first-scan behavior — the default), "corpus"
            (train-split density), or "mixed" (average of both).

    Returns:
        A payload dict conforming to ``schemas/preset_thresholds.schema.json``
        with ``status="sweep_raw"``. The finalize step (``finalize``)
        promotes it to ``status="calibrated"``.

    Raises:
        PipelineError: On schema/cross-file violations or an unknown
            ``prior_mode``.
    """
    assert_safe(_NOTES, context="preset_thresholds.json notes")
    if prior_mode not in _PRIOR_MODES:
        raise PipelineError(f"Unknown prior_mode: {prior_mode!r}; expected one of {_PRIOR_MODES}")

    score_dump = load_score_dump(score_dump_path, schemas_dir)
    corpus = load_corpus(corpus_path, schemas_dir)
    temperature = load_temperature(temperature_path, schemas_dir)
    assert_corpus_seed_matches(score_dump, corpus, context=str(score_dump_path))
    assert_doc_ids_cover_corpus(score_dump, corpus, context=str(score_dump_path))

    dump_categories = tuple(score_dump["categories"])
    if dump_categories != _CATEGORIES:
        raise PipelineError(
            f"{score_dump_path}: dump categories {dump_categories} do not match canonical order "
            f"{_CATEGORIES}."
        )

    train_ids, eval_ids = _split_indices(corpus, seed)
    priors = _priors_from_train(corpus, train_ids)
    rows = _build_candidate_rows(score_dump, corpus, priors, eval_ids, prior_mode)
    truth_totals = _total_positives_by_category(corpus, eval_ids)

    presets: dict[str, dict[str, float]] = {name: {} for name in _PRESET_NAMES}
    for category in _CATEGORIES:
        thresholds, _metrics = _select_for_category(
            rows[category], truth_totals[category], category
        )
        for preset_name, value in thresholds.items():
            presets[preset_name][category] = round(value, 6)

    # 0.1: clamp every swept value below its detector's achievable max so a
    # degenerate sweep can never ship an unreachable threshold.
    presets = _clamp_to_feasible(presets)

    # Sort each preset vector for canonical output (Swift decodes by name).
    sorted_presets = {name: dict(sorted(vector.items())) for name, vector in presets.items()}

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "status": "sweep_raw",  # finalize() promotes to "calibrated"
        "notes": _NOTES + f" Fitted temperature T={temperature['temperature']:.6f} "
        "is recorded here for provenance; see doctype_temperature.json for fit_metadata. "
        f"Prior mode: {prior_mode}.",
        "categories": list(_CATEGORIES),
        "presets": sorted_presets,
    }


def finalize(
    sweep_raw_path: Path,
    schemas_dir: Path,
    shipping_path: Path | None = None,
) -> dict[str, Any]:
    """Promote a sweep_raw artifact to the shipping preset_thresholds payload.

    Reads the sweep_raw file, validates it, and returns a new payload with
    ``status="calibrated"``. The caller writes the result to
    ``build/classifier/preset_thresholds.json``.

    The sweep only produces values for the gated detector categories
    (``_CATEGORIES``), but the shipping file also carries hand-set rows for
    categories outside the W4 gate (8 such rows since search-impl S3 item
    1.7). When ``shipping_path`` is given and exists, those unswept rows are
    carried forward into the promoted payload; a wholesale promote would
    silently delete them (S4 calibration finding, 2026-06-11).

    Refuses to run if the input has already been finalized
    (status != "sweep_raw") to prevent double-promotion.

    Args:
        sweep_raw_path: Path to the sweep_raw artifact written by
            ``make calibrate-sweep``.
        schemas_dir: Directory holding ``*.schema.json`` files.
        shipping_path: Current shipping ``preset_thresholds.json``. Rows for
            categories the sweep does not produce are preserved from this
            file. ``None`` (or a missing file) promotes the raw payload
            unchanged.

    Returns:
        Payload dict with ``status="calibrated"``.

    Raises:
        PipelineError: If the raw file is missing, already finalized,
            or fails schema validation.
    """
    validate_file(sweep_raw_path, schemas_dir, "preset_thresholds")
    raw = load_json(sweep_raw_path)
    if not isinstance(raw, dict):
        raise PipelineError(f"{sweep_raw_path}: sweep_raw root must be an object")
    if raw.get("status") != "sweep_raw":
        raise PipelineError(
            f"{sweep_raw_path}: status is {raw.get('status')!r}, "
            "expected 'sweep_raw'. Run make calibrate-sweep first, "
            "or do not re-finalize an already-calibrated file."
        )
    promoted = dict(raw)
    promoted["status"] = "calibrated"

    if shipping_path is not None and shipping_path.exists():
        validate_file(shipping_path, schemas_dir, "preset_thresholds")
        shipping = load_json(shipping_path)
        swept_categories = set(raw["categories"])
        carried = [c for c in shipping["categories"] if c not in swept_categories]
        if carried:
            merged_presets: dict[str, dict[str, float]] = {}
            for preset_name, swept_vector in raw["presets"].items():
                prior_vector = shipping["presets"].get(preset_name, {})
                missing = [c for c in carried if c not in prior_vector]
                if missing:
                    raise PipelineError(
                        f"{shipping_path}: preset {preset_name!r} lacks values for "
                        f"unswept categories {missing}; cannot carry them forward."
                    )
                merged_presets[preset_name] = {
                    **{c: prior_vector[c] for c in carried},
                    **swept_vector,
                }
            promoted["presets"] = merged_presets
            # Shipping order already extends the canonical sweep tuple; keep
            # it, then append any newly swept category it doesn't know yet.
            known = set(shipping["categories"])
            promoted["categories"] = list(shipping["categories"]) + [
                c for c in raw["categories"] if c not in known
            ]
            promoted["notes"] = (
                raw["notes"] + f" Finalize carried {len(carried)} unswept "
                f"categories forward from the prior shipping file "
                f"({', '.join(carried)}); the sweep produces values only for "
                "the gated detector categories."
            )
            assert_safe(promoted["notes"], context="preset_thresholds.json notes")

    return promoted
