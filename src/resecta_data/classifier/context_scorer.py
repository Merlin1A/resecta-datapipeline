"""In-band learned context-scorer emitter (false-positive-suppression augmentation).

The context scorer is one additive log-odds term applied at the on-device
posterior seam (``DetectionOrchestrator.swift:424`` →
``CalibratedScorer.posterior``):

    finalConfidence = sigmoid(logit(raw) + logit(prior)
                              + w_family * (bias + sum_i weights[i] * z_i))
    z_i = (feature_i - feature_means[i]) / feature_scales[i]

One per-family logistic model over ``{account, phone, mrn, ein, itin}`` keyed
by ``PresetThresholdVector.wireName(for:)``, scored against the canonical
13-feature contract (the single index authority shared by the File-5 fire
dump, the Swift seam builder ``contextFeatures(...)``, and this emitter).

A PLACEHOLDER ships first (every ``w_family`` 0, identity); a later build step fleshes
out the in-band fit: ``build(status="candidates", corpus_path=..., fire_features_path=...)``
reads the committed File-5 fire dump (``build/corpus/g8_fire_features.json`` —
pre-computed features, NOT a device dump; ``_calibration_io.load_score_dump`` /
``load_softmax_dump`` are NEVER imported here — that is a hard stop) and the
committed G8 corpus (``load_corpus``, for provenance only),
then fits one logistic regression per fittable family by Newton-IRLS + L2 (bias
unpenalized, pure stdlib — no numpy/scipy) with deterministic 5-fold
out-of-fold-NLL λ selection. The trainer emits ``status="candidates"``; the
final-named artifact stays the byte-identical ``status="placeholder"`` identity
(promotion to ``calibrated`` and the install happen under an approved change plan).

``build(status="placeholder")`` is unchanged from the initial placeholder: an input-independent
all-identity payload that reads neither the corpus nor the fire dump, so the
final ``context_scorer.json`` stays byte-for-byte the w=0 baseline.

Determinism: stdlib ``math`` only, seed ``20260416``, ``PYTHONHASHSEED=0``,
sorted iteration everywhere, no wall-clock, no RNG; the sole writer is
``dump_canonical_json``. Every emitted free-text string passes ``assert_safe``.

Cross-boundary wire-format changes here need a paired Swift PR.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json
from resecta_data.common.mechanism_language import assert_safe

from ._calibration_io import load_corpus

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.classifier.context_scorer"
_SCHEMA_VERSION: Final[int] = 1

# The canonical 13-feature order — must equal ContextFeatureContract.featureOrder
# (Swift) and the File-5 dump's feature_order. SINGLE authority.
FEATURE_ORDER: Final[tuple[str, ...]] = (
    "kw_positive_window",
    "kw_negative_window",
    "nearest_positive_distance",
    "nearest_negative_distance",
    "digit_run_length",
    "has_separator",
    "left_is_label",
    "at_line_start",
    "doctype_is_court",
    "doctype_is_medical",
    "doctype_is_financial",
    "doctype_is_foia",
    "doctype_is_generic",
)

# The five scored families, keyed by PresetThresholdVector.wireName(for:).
# MRN's wireName is "mrn" (its cell_category_key is "medicalrecord").
FAMILIES: Final[tuple[str, ...]] = ("account", "phone", "mrn", "ein", "itin")

# status enum, mirrored by schemas/context_scorer.schema.json.
_STATUSES: Final[frozenset[str]] = frozenset({"placeholder", "candidates", "calibrated"})

# Fit hyper-parameters — all fixed, no RNG, no wall-clock.
_L2_GRID: Final[tuple[float, ...]] = (0.5, 1.0, 2.0, 5.0)
_KFOLDS: Final[int] = 5
_FOLD_MULT: Final[int] = 2654435761  # Knuth multiplicative hash (stable, no RNG)
_NEWTON_ITERS: Final[int] = 60
_CONVERGENCE: Final[float] = 1.0e-9
_SCALE_FLOOR: Final[float] = 1.0e-6  # keeps a zero-variance one-hot loader-valid
# strict-beat margin (mirrors fit_temperature.py:210 / _TOLERANCE)
_NLL_GATE_MARGIN: Final[float] = 1.0e-6
_PROB_CLAMP: Final[float] = 1.0e-12  # log-arg clamp for finite NLL
_SOLVE_SINGULAR_EPS: Final[float] = 1.0e-12  # pivot below this is treated as singular
_SOLVE_RIDGE: Final[float] = 1.0e-9  # tiny ridge added to a singular pivot

_NOTES: Final[str] = (
    "Per-family logistic context false-positive-suppression term, additive in "
    "log-odds at the posterior seam. Decode by family name; feature_scales are "
    "loader-checked positive Swift-side. B04 candidates: each family with both "
    "classes present is fit by Newton-IRLS+L2 (bias unpenalized, stdlib only) "
    "and ships w_family 1 when its train negative-log-likelihood beats the "
    "base-rate model; a single-class family ships the identity block (w_family "
    "0). The candidates are not auto-installed; the installed final stays the "
    "identity placeholder pending the harness verdict and sign-off."
)

# EXACT placeholder notes — the final context_scorer.json must stay
# byte-identical (sha256 add576680…); do not edit this string.
_PLACEHOLDER_NOTES: Final[str] = (
    "Per-family logistic context false-positive-suppression term, additive in "
    "log-odds at the posterior seam. Decode by family name; feature_scales are "
    "loader-checked positive Swift-side. B03 placeholder: w_family is 0 for "
    "every family (identity), so the term is 0 and detection is unchanged; the "
    "in-band Newton-IRLS+L2 fit over the committed File-5 dump lands in B04."
)

# Calibrated (installed) notes. The promotion ships the SAME fitted weights
# as the candidates; only the status field and these notes differ. Mechanism
# language only — assert_safe-checked at emit.
_CALIBRATED_NOTES: Final[str] = (
    "Per-family logistic context false-positive-suppression term, additive in "
    "log-odds at the posterior seam. Decode by family name; feature_scales are "
    "loader-checked positive Swift-side. B05 calibrated: the in-band "
    "Newton-IRLS+L2 fit over the committed File-5 dump, promoted to the "
    "installed scorer for the families that cleared the orchestrator-path G8 "
    "before/after predicate (account and phone ship w_family 1; mrn, ein, itin "
    "are single-class on the dump and ship the identity block w_family 0). The "
    "weights are byte-identical to the locked candidates; the status field and "
    "these notes are the only difference."
)

# The families promoted to w_family 1 in the calibrated (installed) final — the
# per-family kill switch. ONLY families that cleared the orchestrator-path
# before/after predicate ship active (account precision
# 0.50->1.00, phone 0.606->1.00, both with recall held-or-up and no per-doctype /
# per-demographic slice regression on the synthetic G8 AFTER); every other family
# ships identity even if its raw fit was active. mrn/ein/itin are single-class on
# G8 and already fit to identity, so this set leaves the calibrated weights
# byte-identical to the candidates.
_CALIBRATED_PROMOTE: Final[frozenset[str]] = frozenset({"account", "phone"})


def _identity_family() -> dict[str, Any]:
    """An all-zero (identity) family block: w_family 0 contributes nothing."""
    width = len(FEATURE_ORDER)
    return {
        "weights": [0.0] * width,
        "bias": 0.0,
        "feature_means": [0.0] * width,
        "feature_scales": [1.0] * width,
        "w_family": 0.0,
        "support": {"redact": 0, "suppress": 0},
    }


def _sigmoid(z: float) -> float:
    """Numerically stable logistic sigmoid (stdlib ``math`` only)."""
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Solve ``A x = b`` via partial-pivot Gaussian elimination (stdlib).

    Promoted verbatim in behaviour from the projection's ``_solve``
    (``session-04-plan/projection/train_project.py``): deterministic pivot
    choice, a tiny ridge on a singular pivot to keep the system solvable.
    """
    n = len(rhs)
    work = [[*row, rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < _SOLVE_SINGULAR_EPS:
            work[col][col] += _SOLVE_RIDGE  # keep an ill-conditioned Hessian invertible
            pivot = col
        work[col], work[pivot] = work[pivot], work[col]
        pivot_val = work[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = work[r][col] / pivot_val
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                work[r][c] -= factor * work[col][c]
    return [work[i][n] / work[i][i] for i in range(n)]


def _fit_logistic(
    z_rows: list[list[float]],
    labels: list[float],
    l2: float,
) -> tuple[list[float], float]:
    """Newton-IRLS + L2 logistic fit; bias (intercept) is UNPENALIZED.

    The design appends a constant 1.0 column as the intercept (the last
    coefficient); only the feature coefficients carry the L2 ridge, matching
    the projection's ``fit_logistic`` (bias excluded from the penalty). Returns
    ``(weights[width], bias)`` in ``FEATURE_ORDER``-aligned standardized space.
    Deterministic: fixed iteration count, sorted index iteration, no RNG.
    """
    width = len(FEATURE_ORDER)
    n = len(z_rows)
    p = width + 1
    bias_idx = width  # the appended intercept column
    design = [[*row, 1.0] for row in z_rows]
    coeffs = [0.0] * p
    for _ in range(_NEWTON_ITERS):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for i in range(n):
            xi = design[i]
            z = sum(coeffs[j] * xi[j] for j in range(p))
            mu = _sigmoid(z)
            residual = mu - labels[i]
            weight = max(mu * (1.0 - mu), 1.0e-9)
            for a in range(p):
                grad[a] += residual * xi[a]
                xa = xi[a]
                hess_a = hess[a]
                for b in range(a, p):
                    hess_a[b] += weight * xa * xi[b]
        # symmetrize the lower triangle, then add the L2 ridge (skip the bias)
        for a in range(p):
            for b in range(a):
                hess[a][b] = hess[b][a]
        for a in range(p):
            if a != bias_idx:
                grad[a] += l2 * coeffs[a]
                hess[a][a] += l2
        delta = _solve(hess, grad)
        coeffs = [coeffs[j] - delta[j] for j in range(p)]
        if max(abs(delta[j]) for j in range(p)) < _CONVERGENCE:
            break
    return coeffs[:width], coeffs[bias_idx]


def _mean_nll(
    z_rows: list[list[float]],
    labels: list[float],
    weights: list[float],
    bias: float,
) -> float:
    """Mean negative log-likelihood of a standardized-space logistic model."""
    width = len(FEATURE_ORDER)
    total = 0.0
    for i in range(len(z_rows)):
        z = bias + sum(weights[j] * z_rows[i][j] for j in range(width))
        p = min(max(_sigmoid(z), _PROB_CLAMP), 1.0 - _PROB_CLAMP)
        total += -(labels[i] * math.log(p) + (1.0 - labels[i]) * math.log(1.0 - p))
    return total / len(z_rows)


def _base_rate_nll(labels: list[float]) -> float:
    """Mean NLL of the intercept-only (base-rate) model — the w=0 baseline."""
    n = len(labels)
    p0 = min(max(sum(labels) / n, _PROB_CLAMP), 1.0 - _PROB_CLAMP)
    total = 0.0
    for y in labels:
        total += -(y * math.log(p0) + (1.0 - y) * math.log(1.0 - p0))
    return total / n


def _fold_of(index: int, seed: int) -> int:
    """Deterministic fold assignment for a row index (stable hash, no RNG)."""
    return (index * _FOLD_MULT + seed) % _KFOLDS


def _select_lambda(z_rows: list[list[float]], labels: list[float], seed: int) -> float:
    """Pick the L2 by minimum 5-fold out-of-fold NLL; ties → most-regularized.

    Folds are assigned by the stable index hash ``(k*2654435761+seed)%5`` (no
    RNG). For each candidate λ the out-of-fold NLL is summed across folds; the
    minimum wins, and an exact tie resolves to the largest λ (most regularized),
    which damps single-feature over-reliance. CV is for selection ONLY —
    the shipped weights are the full-data fit at the chosen λ.
    """
    n = len(labels)
    folds = [_fold_of(k, seed) for k in range(n)]
    best_lambda: float | None = None
    best_nll: float | None = None
    for l2 in _L2_GRID:
        oof_total = 0.0
        oof_count = 0
        for fi in range(_KFOLDS):
            train_idx = [k for k in range(n) if folds[k] != fi]
            test_idx = [k for k in range(n) if folds[k] == fi]
            if not test_idx:
                continue
            train_labels = [labels[k] for k in train_idx]
            train_pos = sum(train_labels)
            if not train_idx or train_pos == 0.0 or train_pos == len(train_labels):
                # Single-class (or empty) training fold — fall back to the
                # base-rate (intercept-only) model on this fold, deterministically.
                fold_weights = [0.0] * len(FEATURE_ORDER)
                fold_bias = 0.0
            else:
                fold_weights, fold_bias = _fit_logistic(
                    [z_rows[k] for k in train_idx], train_labels, l2
                )
            width = len(FEATURE_ORDER)
            for k in test_idx:
                z = fold_bias + sum(fold_weights[j] * z_rows[k][j] for j in range(width))
                p = min(max(_sigmoid(z), _PROB_CLAMP), 1.0 - _PROB_CLAMP)
                oof_total += -(labels[k] * math.log(p) + (1.0 - labels[k]) * math.log(1.0 - p))
                oof_count += 1
        oof_nll = oof_total / oof_count if oof_count else float("inf")
        if (
            best_nll is None
            or oof_nll < best_nll - _CONVERGENCE
            or (abs(oof_nll - best_nll) <= _CONVERGENCE and l2 > (best_lambda or 0.0))
        ):
            best_nll = oof_nll
            best_lambda = l2
    # _L2_GRID is non-empty, so best_lambda is always set by the loop above.
    if best_lambda is None:
        raise PipelineError("context_scorer: the L2 grid search produced no lambda")
    return best_lambda


def _fit_family(
    family: str, fires: list[dict[str, Any]], seed: int
) -> tuple[dict[str, Any], float | None, float | None]:
    """Fit one family's logistic block from its File-5 fire rows.

    A family with both classes present is standardized on full data, has its L2
    chosen by out-of-fold NLL (folds seeded by ``seed``), is fit by
    Newton-IRLS+L2 at the chosen λ, and ships ``w_family`` 1 IFF its train-NLL
    strictly beats the base-rate model (the train-NLL correctness gate,
    mirroring ``fit_temperature.py:210``). A single-class (degenerate) family —
    or one that does not beat the gate — ships the identity block (``w_family``
    0) with a loud ``logger.warning``.

    Returns ``(block, nll_fit, nll_w0)``; ``nll_fit`` / ``nll_w0`` are the
    full-data fitted and base-rate negative log-likelihoods for a fittable
    family (``None`` / ``None`` for an identity block), so the caller's
    ship-gate backstop re-checks the exact arithmetic without re-reading.
    """
    width = len(FEATURE_ORDER)
    # Deterministic row order BEFORE any matrix is built (sort, no RNG).
    ordered = sorted(fires, key=lambda f: (f["start"], f["end"], f["doctype"]))
    feats = [[float(v) for v in f["features"]] for f in ordered]
    labels = [1.0 if f["gt_class"] == "positive" else 0.0 for f in ordered]
    n = len(labels)
    n_pos = int(sum(labels))
    n_neg = n - n_pos

    block = _identity_family()
    block["support"] = {"redact": n_pos, "suppress": n_neg}

    if n_pos == 0 or n_neg == 0:
        # Single-class / degenerate — cannot fit a discriminator. ITIN on G8 is
        # this off-G8 case; warn, ship identity, do not fabricate data or raise.
        _LOGGER.warning(
            "context_scorer: family %r is single-class on the fire dump "
            "(n=%d, redact=%d, suppress=%d) — shipping identity (w_family 0).",
            family,
            n,
            n_pos,
            n_neg,
        )
        return block, None, None

    for vec in feats:
        if len(vec) != width:
            raise PipelineError(
                f"context_scorer: family {family!r} fire has {len(vec)} features, "
                f"expected {width} (the canonical feature_order width)."
            )

    means = [sum(feats[k][i] for k in range(n)) / n for i in range(width)]
    scales: list[float] = []
    for i in range(width):
        variance = sum((feats[k][i] - means[i]) ** 2 for k in range(n)) / n
        scales.append(max(math.sqrt(variance), _SCALE_FLOOR))
    z_rows = [[(feats[k][i] - means[i]) / scales[i] for i in range(width)] for k in range(n)]

    chosen_lambda = _select_lambda(z_rows, labels, seed)
    weights, bias = _fit_logistic(z_rows, labels, chosen_lambda)

    nll_fit = _mean_nll(z_rows, labels, weights, bias)
    nll_w0 = _base_rate_nll(labels)
    beats_gate = nll_fit < nll_w0 - _NLL_GATE_MARGIN

    if not beats_gate:
        # The fit does not strictly improve on the base rate — ship identity
        # rather than an unhelpful active model (the train sanity gate).
        _LOGGER.warning(
            "context_scorer: family %r fitted NLL %.9f does not beat the "
            "base-rate NLL %.9f by %.0e (n=%d, redact=%d, suppress=%d) — "
            "shipping identity (w_family 0).",
            family,
            nll_fit,
            nll_w0,
            _NLL_GATE_MARGIN,
            n,
            n_pos,
            n_neg,
        )
        return block, nll_fit, nll_w0

    block["weights"] = weights
    block["bias"] = bias
    block["feature_means"] = means
    block["feature_scales"] = scales
    block["w_family"] = 1.0
    return block, nll_fit, nll_w0


def _fit_families(
    seed: int,
    corpus_path: Path,
    fire_features_path: Path,
    schemas_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[float, float]]]:
    """Fit every family from the committed File-5 dump (the candidates body).

    The corpus is loaded (and schema-validated) for provenance — the fit reads
    PRE-COMPUTED features from the fire dump, never recomputed text features,
    and never a device score/softmax dump (a hard stop). Families are
    keyed by the dump's ``family`` field (the wireName; MRN → ``"mrn"``).

    Returns ``(families, nll_by_family)`` where ``nll_by_family[F] =
    (nll_fit, nll_w0)`` for every fittable family (the ship-gate backstop input;
    identity families are absent from the map).
    """
    # Provenance / input validation only — the fit does not read corpus text.
    _ = load_corpus(corpus_path, schemas_dir)

    dump = load_json(fire_features_path)
    if not isinstance(dump, dict) or "fires" not in dump:
        raise PipelineError(
            f"{fire_features_path}: fire-features dump must be an object with a 'fires' array."
        )
    dump_order = dump.get("feature_order")
    if dump_order != list(FEATURE_ORDER):
        raise PipelineError(
            f"{fire_features_path}: feature_order {dump_order} does not match the "
            f"canonical contract {list(FEATURE_ORDER)} — the index authority drifted."
        )
    fires = dump["fires"]
    seen_families = {f["family"] for f in fires}
    unknown = seen_families - set(FAMILIES)
    if unknown:
        # Guard: the dump must bucket by wireName ⊆ the five scored families.
        raise PipelineError(
            f"{fire_features_path}: fire 'family' values {sorted(unknown)} are not "
            f"in the scored set {sorted(FAMILIES)} (bucket by wireName, never cell_category_key)."
        )

    families: dict[str, dict[str, Any]] = {}
    nll_by_family: dict[str, tuple[float, float]] = {}
    for family in FAMILIES:
        family_fires = [f for f in fires if f["family"] == family]
        if not family_fires:
            # No rows for this family at all (e.g. a family absent from the
            # corpus) — identity, named loudly, never fabricated.
            _LOGGER.warning(
                "context_scorer: family %r has no fires in the dump — "
                "shipping identity (w_family 0).",
                family,
            )
            families[family] = _identity_family()
            continue
        block, nll_fit, nll_w0 = _fit_family(family, family_fires, seed)
        families[family] = block
        if nll_fit is not None and nll_w0 is not None:
            nll_by_family[family] = (nll_fit, nll_w0)
    return families, nll_by_family


def _assert_train_nll_backstop(
    families: dict[str, dict[str, Any]],
    nll_by_family: dict[str, tuple[float, float]],
) -> None:
    """Re-check that no family ships active without strictly beating its base rate.

    The per-family gate in ``_fit_family`` already enforces this; this is the
    belt-and-braces invariant the runbook requires, over the fit's own
    ``(nll_fit, nll_w0)`` — no re-read, no re-fit.
    """
    for name, block in families.items():
        if block["w_family"] == 0.0:
            continue
        support = block["support"]
        fitted = nll_by_family.get(name)
        if fitted is None:
            raise PipelineError(
                f"context_scorer: family {name!r} shipped w_family="
                f"{block['w_family']} with no recorded fit diagnostics. "
                "Train-NLL backstop."
            )
        nll_fit, nll_w0 = fitted
        if nll_fit >= nll_w0 - _NLL_GATE_MARGIN:
            raise PipelineError(
                f"context_scorer: family {name!r} shipped w_family="
                f"{block['w_family']} but fitted NLL {nll_fit} does not beat "
                f"base-rate NLL {nll_w0} (support redact={support['redact']}, "
                f"suppress={support['suppress']}). Train-NLL backstop."
            )


def _apply_calibrated_promote(families: dict[str, dict[str, Any]]) -> None:
    """Zero any fittable family not in ``_CALIBRATED_PROMOTE`` (the promote kill switch).

    A fittable family outside the promote set ships the identity block, so a
    family that did not clear the before/after measurement never auto-ships active. Mutates
    ``families`` in place; the support counts are preserved for provenance. Here
    the set == the fittable families, so nothing is zeroed and the weights stay
    byte-identical to the candidates.
    """
    for name in sorted(families):
        block = families[name]
        if name in _CALIBRATED_PROMOTE or block["w_family"] == 0.0:
            continue
        _LOGGER.warning(
            "context_scorer: family %r fitted (w_family %s) but is not in the "
            "promote set — shipping identity (w_family 0).",
            name,
            block["w_family"],
        )
        support = block["support"]
        families[name] = _identity_family()
        families[name]["support"] = support


def _fit_active(
    seed: int,
    corpus_path: Path | None,
    fire_features_path: Path | None,
    schemas_dir: Path | None,
    status: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Fit the per-family blocks for a non-placeholder status; return (families, notes).

    ``candidates`` ships the raw fit; ``calibrated`` additionally applies the
    per-family promote filter (the promote kill switch) and the installed notes.
    """
    if corpus_path is None or fire_features_path is None or schemas_dir is None:
        raise PipelineError(
            f"context_scorer: status={status!r} requires corpus_path, "
            "fire_features_path, and schemas_dir."
        )
    families, nll_by_family = _fit_families(seed, corpus_path, fire_features_path, schemas_dir)
    _assert_train_nll_backstop(families, nll_by_family)
    if status == "calibrated":
        _apply_calibrated_promote(families)
        return families, _CALIBRATED_NOTES
    return families, _NOTES


def build(
    seed: int,
    *,
    corpus_path: Path | None = None,
    fire_features_path: Path | None = None,
    schemas_dir: Path | None = None,
    status: str = "placeholder",
) -> dict[str, Any]:
    """Return the context-scorer weights payload.

    ``status="placeholder"`` (the default) emits an input-independent
    all-identity payload (every family ``w_family`` 0); ``corpus_path`` /
    ``fire_features_path`` / ``schemas_dir`` are accepted for signature
    stability but are not read — the final ``context_scorer.json`` stays
    byte-for-byte the w=0 baseline.

    ``status="candidates"`` runs the in-band Newton-IRLS+L2 fit over the
    committed File-5 fire dump (pre-computed features) with the corpus loaded
    for provenance, and emits trained per-family weights (a fittable family that
    beats the train-NLL gate ships ``w_family`` 1; a single-class or
    gate-failing family ships the identity block). It NEVER consumes a device
    score/softmax dump (a hard stop).

    Args:
        seed: The canonical build seed (recorded for provenance; also seeds the
            deterministic fold-index hash for λ selection).
        corpus_path: The committed G8 corpus (provenance/validation for the
            candidates fit; unread by the placeholder).
        fire_features_path: The committed File-5 fire dump (the candidates fit
            input; unread by the placeholder).
        schemas_dir: Directory of ``*.schema.json`` (corpus validation for the
            candidates fit; unread by the placeholder).
        status: One of ``placeholder`` | ``candidates`` | ``calibrated``.

    Raises:
        PipelineError: On a negative seed, an out-of-enum status, a candidates
            build missing its inputs, a feature_order drift, an off-contract
            family key, or a fitted family that violates the train-NLL backstop.
    """
    if seed < 0:
        raise PipelineError(f"context_scorer: seed must be non-negative, got {seed}")
    if status not in _STATUSES:
        raise PipelineError(f"context_scorer: status {status!r} not in {sorted(_STATUSES)}")

    if status in ("candidates", "calibrated"):
        families, notes = _fit_active(seed, corpus_path, fire_features_path, schemas_dir, status)
    else:
        # Reserved inputs — accepted for signature stability, unread by the
        # placeholder (which reads neither the corpus nor the fire dump).
        _ = (corpus_path, fire_features_path, schemas_dir)
        families = {family: _identity_family() for family in FAMILIES}
        notes = _PLACEHOLDER_NOTES

    payload: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "status": status,
        "feature_order": list(FEATURE_ORDER),
        "families": families,
        "notes": notes,
    }

    # Mechanism-language gate on every emitted free-text string.
    assert_safe(payload["generated_by"], context="context_scorer.json generated_by")
    assert_safe(notes, context="context_scorer.json notes")
    return payload
